"""Tests for the Linux journal and macOS unified-log event collection.

Both readers are injected, so the filtering, deduplication, checkpoint and
error-handling logic is verified offline and deterministically, on any
operating system. The parsers are additionally exercised against recorded
command output, and the Linux reader is additionally run against the real
``journalctl`` binary when the machine has one.

No Windows, macOS or systemd machine is required.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.data.collector_factory import create_event_collector, normalize_platform
from src.data.linux_journal import (
    DEFAULT_ALLOWED_CHANNELS as LINUX_ALLOWED_CHANNELS,
)
from src.data.linux_journal import (
    JOURNAL_MATCHES,
    LinuxJournalCollector,
    LinuxJournalReader,
    _parse_journal_output,
)
from src.data.macos_unified_log import (
    DEFAULT_ALLOWED_CHANNELS as MACOS_ALLOWED_CHANNELS,
)
from src.data.macos_unified_log import (
    MacOSUnifiedLogCollector,
    MacOSUnifiedLogReader,
    _build_predicate,
    _parse_unified_log,
)
from src.data.schemas import SOURCE_LINUX_LOCAL, SOURCE_MACOS_LOCAL
from src.data.system_event_log import (
    EventLogReadResult,
    EventLogReader,
    SystemEventRecord,
)

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


class FakeReader(EventLogReader):
    """Reader returning a fixed set of records, or a simulated failure."""

    def __init__(self, records=(), error=None):
        self.records = list(records)
        self.error = error
        self.calls: list[tuple] = []

    def read(self, channels, since, limit):
        self.calls.append((channels, since, limit))
        if self.error is not None:
            return EventLogReadResult(
                records=(), error=self.error, available=False
            )
        return EventLogReadResult(records=tuple(self.records), available=True)


@pytest.fixture
def checkpoint(tmp_path):
    return tmp_path / "checkpoint.json"


# ---------------------------------------------------------------------------
# Shared guarantees
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "collector_class,source",
    [
        (LinuxJournalCollector, SOURCE_LINUX_LOCAL),
        (MacOSUnifiedLogCollector, SOURCE_MACOS_LOCAL),
    ],
)
def test_the_window_is_bounded(collector_class, source, checkpoint):
    reader = FakeReader()
    collector = collector_class(
        reader=reader, checkpoint_path=checkpoint, window_minutes=15
    )
    collector.collect(now=NOW)
    channels, since, limit = reader.calls[0]
    assert since == NOW - timedelta(minutes=15)
    assert limit == 200
    assert channels


@pytest.mark.parametrize(
    "collector_class,source",
    [
        (LinuxJournalCollector, SOURCE_LINUX_LOCAL),
        (MacOSUnifiedLogCollector, SOURCE_MACOS_LOCAL),
    ],
)
def test_the_record_limit_is_passed_to_the_reader(
    collector_class, source, checkpoint
):
    reader = FakeReader()
    collector = collector_class(
        reader=reader, checkpoint_path=checkpoint, max_records=25
    )
    collector.collect(now=NOW)
    assert reader.calls[0][2] == 25


@pytest.mark.parametrize(
    "collector_class,source",
    [
        (LinuxJournalCollector, SOURCE_LINUX_LOCAL),
        (MacOSUnifiedLogCollector, SOURCE_MACOS_LOCAL),
    ],
)
def test_records_are_deduplicated_across_collections(
    collector_class, source, checkpoint
):
    record = SystemEventRecord(
        record_id="journal:cursor-1",
        timestamp=NOW,
        channel=LINUX_ALLOWED_CHANNELS[0]
        if collector_class is LinuxJournalCollector
        else MACOS_ALLOWED_CHANNELS[0],
        event_id=3,
        level="ERROR",
        message="something failed",
    )
    reader = FakeReader([record])
    collector = collector_class(reader=reader, checkpoint_path=checkpoint)
    first = collector.collect(now=NOW)
    second = collector.collect(now=NOW)
    assert len(first.records) == 1
    assert second.records == ()


@pytest.mark.parametrize(
    "collector_class,source",
    [
        (LinuxJournalCollector, SOURCE_LINUX_LOCAL),
        (MacOSUnifiedLogCollector, SOURCE_MACOS_LOCAL),
    ],
)
def test_the_checkpoint_survives_a_restart(collector_class, source, checkpoint):
    record = SystemEventRecord(
        record_id="journal:cursor-1",
        timestamp=NOW,
        channel=LINUX_ALLOWED_CHANNELS[0]
        if collector_class is LinuxJournalCollector
        else MACOS_ALLOWED_CHANNELS[0],
        event_id=3,
        level="ERROR",
    )
    first = collector_class(
        reader=FakeReader([record]), checkpoint_path=checkpoint
    )
    assert len(first.collect(now=NOW).records) == 1

    second = collector_class(
        reader=FakeReader([record]), checkpoint_path=checkpoint
    )
    assert second.collect(now=NOW).records == ()


@pytest.mark.parametrize(
    "collector_class,source",
    [
        (LinuxJournalCollector, SOURCE_LINUX_LOCAL),
        (MacOSUnifiedLogCollector, SOURCE_MACOS_LOCAL),
    ],
)
def test_permission_errors_are_surfaced_not_hidden(collector_class, source, checkpoint):
    reader = FakeReader(error="access denied")
    collector = collector_class(reader=reader, checkpoint_path=checkpoint)
    result = collector.collect(now=NOW)
    assert result.error is not None
    assert result.available is False
    assert collector.last_error == "access denied"
    assert collector.available is False


@pytest.mark.parametrize(
    "collector_class,source",
    [
        (LinuxJournalCollector, SOURCE_LINUX_LOCAL),
        (MacOSUnifiedLogCollector, SOURCE_MACOS_LOCAL),
    ],
)
def test_an_empty_log_is_not_an_error(collector_class, source, checkpoint):
    collector = collector_class(
        reader=FakeReader([]), checkpoint_path=checkpoint
    )
    result = collector.collect(now=NOW)
    assert result.error is None
    assert result.available is True
    assert result.records == ()


@pytest.mark.parametrize(
    "collector_class,source",
    [
        (LinuxJournalCollector, SOURCE_LINUX_LOCAL),
        (MacOSUnifiedLogCollector, SOURCE_MACOS_LOCAL),
    ],
)
def test_the_collector_stamps_its_telemetry_source(
    collector_class, source, checkpoint
):
    channel = (
        LINUX_ALLOWED_CHANNELS[0]
        if collector_class is LinuxJournalCollector
        else MACOS_ALLOWED_CHANNELS[0]
    )
    record = SystemEventRecord(
        record_id="id-1",
        timestamp=NOW,
        channel=channel,
        event_id=3,
        level="ERROR",
    )
    collector = collector_class(reader=FakeReader([record]), checkpoint_path=checkpoint)
    stored = collector.collect(now=NOW).records[0]
    assert stored.source == source


# ---------------------------------------------------------------------------
# Linux journal
# ---------------------------------------------------------------------------


def test_the_linux_allowlist_is_explicit():
    assert LINUX_ALLOWED_CHANNELS
    assert "kernel" in LINUX_ALLOWED_CHANNELS
    for channel in LINUX_ALLOWED_CHANNELS:
        assert channel in JOURNAL_MATCHES


def test_journal_entries_are_parsed_into_records():
    output = "\n".join(
        [
            json.dumps(
                {
                    "__REALTIME_TIMESTAMP": "1780315200000000",
                    "__CURSOR": "s=abc;i=1;b=2",
                    "MESSAGE": "kernel: something failed",
                    "SYSLOG_IDENTIFIER": "kernel",
                    "PRIORITY": "3",
                }
            ),
            json.dumps(
                {
                    "__REALTIME_TIMESTAMP": "1780315260000000",
                    "__CURSOR": "s=def;i=2;b=2",
                    "MESSAGE": "second entry",
                    "SYSLOG_IDENTIFIER": "kernel",
                    "PRIORITY": "2",
                }
            ),
        ]
    )
    records = _parse_journal_output(output, "kernel", limit=10)
    assert len(records) == 2
    assert records[0].record_id == "journal:s=abc;i=1;b=2"
    assert records[0].channel == "kernel"
    assert records[0].event_id == 3
    assert records[0].level == "ERROR"
    assert records[0].provider == "kernel"
    assert records[0].timestamp == datetime(
        2026, 6, 1, 12, 0, tzinfo=timezone.utc
    )
    assert records[1].level == "CRITICAL"


def test_journal_records_are_bounded_by_the_limit():
    output = "\n".join(
        json.dumps(
            {
                "__REALTIME_TIMESTAMP": "1780315200000000",
                "__CURSOR": f"cursor-{index}",
                "MESSAGE": "entry",
                "SYSLOG_IDENTIFIER": "kernel",
                "PRIORITY": "3",
            }
        )
        for index in range(10)
    )
    records = _parse_journal_output(output, "kernel", limit=3)
    assert len(records) == 3


def test_malformed_journal_output_yields_no_records():
    assert _parse_journal_output("not json", "kernel", limit=10) == []


def test_journal_entries_outside_the_allowlist_are_dropped(checkpoint):
    record = SystemEventRecord(
        record_id="id-1",
        timestamp=NOW,
        channel="not-allowed",
        event_id=3,
        level="ERROR",
    )
    collector = LinuxJournalCollector(
        reader=FakeReader([record]), checkpoint_path=checkpoint
    )
    assert collector.collect(now=NOW).records == ()


def test_the_linux_reader_runs_against_the_real_journalctl():
    """The real binary is invoked; an empty journal is not an error."""
    import shutil

    if shutil.which("journalctl") is None:
        pytest.skip("journalctl is not installed on this machine")

    reader = LinuxJournalReader()
    result = reader.read(
        ("kernel",), NOW - timedelta(minutes=60), limit=5
    )
    assert result.available is True
    assert result.error is None
    for record in result.records:
        assert record.channel == "kernel"
        assert record.source == SOURCE_LINUX_LOCAL
        assert record.record_id.startswith("journal:")


# ---------------------------------------------------------------------------
# macOS unified log
# ---------------------------------------------------------------------------


def test_the_macos_allowlist_is_explicit():
    assert MACOS_ALLOWED_CHANNELS
    assert "kernel" in MACOS_ALLOWED_CHANNELS


def test_unified_log_entries_are_parsed_into_records():
    payload = json.dumps(
        [
            {
                "timestamp": "2026-06-01 12:00:00.000000+0000",
                "process": "kernel",
                "processID": 0,
                "message": "panic: something failed",
                "messageType": "Error",
                "eventType": "log",
                "category": "kernel",
                "subsystem": "com.apple.kernel",
            }
        ]
    )
    records = _parse_unified_log(payload, limit=10)
    assert len(records) == 1
    assert records[0].channel == "kernel"
    assert records[0].level == "ERROR"
    assert records[0].provider == "com.apple.kernel"
    assert records[0].record_id.startswith("unified:")
    assert records[0].timestamp == datetime(
        2026, 6, 1, 12, 0, tzinfo=timezone.utc
    )


def test_malformed_unified_log_output_yields_no_records():
    assert _parse_unified_log("not json", limit=10) == []
    assert _parse_unified_log("", limit=10) == []


def test_the_unified_log_predicate_is_bounded_to_the_allowlist():
    predicate = _build_predicate(("kernel", "loginwindow"))
    assert 'process == "kernel"' in predicate
    assert 'process == "loginwindow"' in predicate
    assert "messageType" in predicate


def test_informational_unified_log_entries_are_dropped(checkpoint):
    record = SystemEventRecord(
        record_id="unified:1",
        timestamp=NOW,
        channel="kernel",
        level="INFORMATION",
        message="routine",
    )
    collector = MacOSUnifiedLogCollector(
        reader=FakeReader([record]), checkpoint_path=checkpoint
    )
    assert collector.collect(now=NOW).records == ()


def test_error_unified_log_entries_are_kept(checkpoint):
    record = SystemEventRecord(
        record_id="unified:1",
        timestamp=NOW,
        channel="kernel",
        level="ERROR",
        message="panic",
    )
    collector = MacOSUnifiedLogCollector(
        reader=FakeReader([record]), checkpoint_path=checkpoint
    )
    assert len(collector.collect(now=NOW).records) == 1


def test_entries_from_processes_outside_the_allowlist_are_dropped(checkpoint):
    record = SystemEventRecord(
        record_id="unified:1",
        timestamp=NOW,
        channel="SomeApp",
        level="ERROR",
    )
    collector = MacOSUnifiedLogCollector(
        reader=FakeReader([record]), checkpoint_path=checkpoint
    )
    assert collector.collect(now=NOW).records == ()


# ---------------------------------------------------------------------------
# Platform selection
# ---------------------------------------------------------------------------


def test_the_event_collector_is_selected_per_platform(tmp_path):
    from src.data.windows_event_log import WindowsEventLogCollector

    windows = create_event_collector(
        "windows", checkpoint_path=str(tmp_path / "w.json")
    )
    linux = create_event_collector(
        "linux", checkpoint_path=str(tmp_path / "l.json")
    )
    macos = create_event_collector(
        "macos", checkpoint_path=str(tmp_path / "m.json")
    )
    assert isinstance(windows, WindowsEventLogCollector)
    assert isinstance(linux, LinuxJournalCollector)
    assert isinstance(macos, MacOSUnifiedLogCollector)


def test_an_unsupported_platform_is_rejected():
    with pytest.raises(ValueError):
        normalize_platform("plan9")
