"""Tests for bounded, filtered, deduplicated Windows event log collection.

The reader is injected, so the filtering, deduplication, checkpoint, and error
handling logic is verified offline and deterministically. No Windows host is
required and no real event log is read.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.data.windows_event_log import (
    DEFAULT_ALLOWED_CHANNELS,
    DEFAULT_ALLOWED_EVENT_IDS,
    EventLogReadResult,
    EventLogReader,
    SystemEventRecord,
    WindowsEventLogCollector,
    _parse_wevtutil_xml,
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


def _record(record_id, channel="System", event_id=6008, level="ERROR"):
    return SystemEventRecord(
        record_id=record_id,
        timestamp=NOW,
        channel=channel,
        provider="TestProvider",
        event_id=event_id,
        level=level,
        message=f"message for {record_id}",
    )


@pytest.fixture
def checkpoint(tmp_path):
    return tmp_path / "checkpoint.json"


# ---------------------------------------------------------------------------
# Allowlist filtering
# ---------------------------------------------------------------------------


def test_only_allowlisted_channels_are_read(checkpoint):
    reader = FakeReader()
    collector = WindowsEventLogCollector(reader=reader, checkpoint_path=checkpoint)
    collector.collect(now=NOW)
    assert reader.calls[0][0] == DEFAULT_ALLOWED_CHANNELS
    assert "Security" not in reader.calls[0][0]


def test_events_outside_the_event_id_allowlist_are_dropped(checkpoint):
    reader = FakeReader(
        [
            _record("System:6008", event_id=6008),
            _record("System:9999", event_id=9999),
        ]
    )
    collector = WindowsEventLogCollector(reader=reader, checkpoint_path=checkpoint)
    result = collector.collect(now=NOW)
    assert [r.record_id for r in result.records] == ["System:6008"]


def test_events_from_unlisted_channels_are_dropped(checkpoint):
    reader = FakeReader(
        [
            _record("System:6008", channel="System", event_id=6008),
            _record("Security:4624", channel="Security", event_id=4624),
        ]
    )
    collector = WindowsEventLogCollector(reader=reader, checkpoint_path=checkpoint)
    result = collector.collect(now=NOW)
    assert [r.record_id for r in result.records] == ["System:6008"]


def test_allowlisted_application_events_are_kept(checkpoint):
    reader = FakeReader(
        [_record("Application:1000", channel="Application", event_id=1000)]
    )
    collector = WindowsEventLogCollector(reader=reader, checkpoint_path=checkpoint)
    result = collector.collect(now=NOW)
    assert [r.record_id for r in result.records] == ["Application:1000"]


def test_default_allowlists_are_non_empty_and_explicit():
    assert DEFAULT_ALLOWED_CHANNELS
    assert "System" in DEFAULT_ALLOWED_CHANNELS
    assert "Application" in DEFAULT_ALLOWED_CHANNELS
    assert DEFAULT_ALLOWED_EVENT_IDS["System"]
    assert 6008 in DEFAULT_ALLOWED_EVENT_IDS["System"]


# ---------------------------------------------------------------------------
# Bounded windows
# ---------------------------------------------------------------------------


def test_collection_window_is_bounded(checkpoint):
    reader = FakeReader()
    collector = WindowsEventLogCollector(
        reader=reader, checkpoint_path=checkpoint, window_minutes=15
    )
    collector.collect(now=NOW)
    channels, since, limit = reader.calls[0]
    assert since == NOW - timedelta(minutes=15)
    assert limit == 200


def test_max_records_is_passed_to_the_reader(checkpoint):
    reader = FakeReader()
    collector = WindowsEventLogCollector(
        reader=reader, checkpoint_path=checkpoint, max_records=25
    )
    collector.collect(now=NOW)
    assert reader.calls[0][2] == 25


def test_max_records_is_at_least_one(checkpoint):
    reader = FakeReader()
    collector = WindowsEventLogCollector(
        reader=reader, checkpoint_path=checkpoint, max_records=0
    )
    collector.collect(now=NOW)
    assert reader.calls[0][2] == 1


# ---------------------------------------------------------------------------
# Deduplication and checkpoint
# ---------------------------------------------------------------------------


def test_repeated_collection_deduplicates_records(checkpoint):
    reader = FakeReader([_record("System:6008")])
    collector = WindowsEventLogCollector(reader=reader, checkpoint_path=checkpoint)
    first = collector.collect(now=NOW)
    second = collector.collect(now=NOW)
    assert len(first.records) == 1
    assert len(second.records) == 0


def test_duplicate_records_within_one_batch_are_deduplicated(checkpoint):
    reader = FakeReader(
        [_record("System:6008"), _record("System:6008")]
    )
    collector = WindowsEventLogCollector(reader=reader, checkpoint_path=checkpoint)
    result = collector.collect(now=NOW)
    assert len(result.records) == 1


def test_checkpoint_survives_a_restart(checkpoint):
    reader = FakeReader([_record("System:6008")])
    first = WindowsEventLogCollector(
        reader=reader, checkpoint_path=checkpoint
    )
    assert len(first.collect(now=NOW).records) == 1

    second = WindowsEventLogCollector(
        reader=FakeReader([_record("System:6008")]),
        checkpoint_path=checkpoint,
    )
    assert len(second.collect(now=NOW).records) == 0


def test_checkpoint_file_contains_the_seen_ids(checkpoint):
    reader = FakeReader([_record("System:6008")])
    collector = WindowsEventLogCollector(reader=reader, checkpoint_path=checkpoint)
    collector.collect(now=NOW)
    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert "System:6008" in payload["seen_ids"]


def test_a_corrupt_checkpoint_is_ignored_not_fatal(checkpoint):
    checkpoint.write_text("not json at all", encoding="utf-8")
    reader = FakeReader([_record("System:6008")])
    collector = WindowsEventLogCollector(reader=reader, checkpoint_path=checkpoint)
    result = collector.collect(now=NOW)
    assert len(result.records) == 1


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_permission_errors_are_surfaced_not_hidden_as_no_events(checkpoint):
    reader = FakeReader(error="access denied")
    collector = WindowsEventLogCollector(reader=reader, checkpoint_path=checkpoint)
    result = collector.collect(now=NOW)
    assert result.error is not None
    assert result.available is False
    assert collector.last_error == "access denied"
    assert collector.available is False


def test_a_failed_read_yields_no_records(checkpoint):
    reader = FakeReader(error="boom")
    collector = WindowsEventLogCollector(reader=reader, checkpoint_path=checkpoint)
    result = collector.collect(now=NOW)
    assert result.records == ()


def test_a_successful_read_with_no_events_is_not_an_error(checkpoint):
    reader = FakeReader([])
    collector = WindowsEventLogCollector(reader=reader, checkpoint_path=checkpoint)
    result = collector.collect(now=NOW)
    assert result.error is None
    assert result.available is True
    assert result.records == ()


# ---------------------------------------------------------------------------
# Record shape
# ---------------------------------------------------------------------------


def test_record_metadata_is_bounded_and_structured():
    record = _record("System:6008")
    metadata = record.as_metadata()
    assert metadata["channel"] == "System"
    assert metadata["event_id"] == 6008
    assert metadata["record_id"] == "System:6008"
    assert metadata["level"] == "ERROR"


def test_long_messages_are_truncated():
    from src.data.windows_event_log import _truncate

    text = "x" * 5000
    assert len(_truncate(text)) <= 2001


def test_wevtutil_xml_is_parsed_into_records():
    xml = """<Events>
      <Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
        <System>
          <Provider Name="Service Control Manager"/>
          <EventID>7036</EventID>
          <Level>4</Level>
          <TimeCreated SystemTime="2026-06-01T11:59:00.000Z"/>
          <EventRecordID>4242</EventRecordID>
        </System>
        <EventData>
          <Data>Service started</Data>
        </EventData>
      </Event>
    </Events>"""
    records = _parse_wevtutil_xml(xml, "System")
    assert len(records) == 1
    assert records[0].record_id == "System:4242"
    assert records[0].event_id == 7036
    assert records[0].level == "INFORMATION"
    assert records[0].provider == "Service Control Manager"
    assert records[0].timestamp == datetime(
        2026, 6, 1, 11, 59, tzinfo=timezone.utc
    )


def test_malformed_wevtutil_xml_yields_no_records():
    assert _parse_wevtutil_xml("<not-xml", "System") == []
