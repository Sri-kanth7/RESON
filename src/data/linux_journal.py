"""Bounded, filtered, deduplicated Linux systemd-journal collection.

The Linux event source is the systemd journal. It is read through
``journalctl``, because the journal exposes no stable Python API. The reader
is deliberately narrow:

* **Allowlist only.** Only explicitly listed units and syslog identifiers are
  read; the whole journal is never ingested.
* **Bounded.** Each collection is limited to a recent time window and a
  maximum number of records, and the journal query itself is bounded by the
  same window, a priority floor and a record limit.
* **Deduplicated.** Each entry carries the journal's own stable cursor, and a
  checkpoint file records what has already been ingested.
* **Permission aware.** If the journal cannot be read (no journal on the
  machine, insufficient privileges, a missing ``journalctl``), the failure is
  reported explicitly. RESON never presents "could not read" as "there were
  no events".
* **Content-minimal.** Only the entry's own message text is kept, truncated
  to a bounded length.

The module is import-safe on every operating system: ``journalctl`` is invoked
as a subprocess, never imported.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from src.data.schemas import SOURCE_LINUX_LOCAL
from src.data.system_event_log import (
    LEVEL_CRITICAL,
    LEVEL_ERROR,
    LEVEL_INFORMATION,
    LEVEL_WARNING,
    EventLogReader,
    EventLogReadResult,
    SystemEventCollector,
    SystemEventRecord,
    truncate_message,
)

#: Categories RESON reads from the journal, mapped to the journalctl match
#: expression that selects them.
#:
#: This is an explicit allowlist: RESON never reads the whole journal.
JOURNAL_MATCHES: dict[str, str] = {
    "kernel": "SYSLOG_IDENTIFIER=kernel",
    "sshd.service": "_SYSTEMD_UNIT=sshd.service",
    "systemd-networkd.service": "_SYSTEMD_UNIT=systemd-networkd.service",
    "systemd-resolved.service": "_SYSTEMD_UNIT=systemd-resolved.service",
    "cron.service": "_SYSTEMD_UNIT=cron.service",
    "dbus.service": "_SYSTEMD_UNIT=dbus.service",
}

#: Categories collected by default.
DEFAULT_ALLOWED_CHANNELS: tuple[str, ...] = tuple(JOURNAL_MATCHES)

#: Syslog priorities collected per category.
#:
#: 0 emerg, 1 alert, 2 crit, 3 err, 4 warning, 5 notice, 6 info, 7 debug.
#: Only emerg/alert/crit/err are collected: they are the entries that
#: describe a failure. Informational chatter is not system-event evidence.
DEFAULT_ALLOWED_EVENT_IDS: dict[str, tuple[int, ...]] = {
    category: (0, 1, 2, 3) for category in DEFAULT_ALLOWED_CHANNELS
}

#: Priority floor used in the journalctl query.
DEFAULT_PRIORITY_FILTER = "err"

#: Syslog priority mapped to a RESON level.
_PRIORITY_LEVELS = {
    0: LEVEL_CRITICAL,
    1: LEVEL_CRITICAL,
    2: LEVEL_CRITICAL,
    3: LEVEL_ERROR,
    4: LEVEL_WARNING,
    5: LEVEL_INFORMATION,
    6: LEVEL_INFORMATION,
    7: "DEBUG",
}


def _priority_to_level(priority: int | None) -> str | None:
    """Return the RESON level for a syslog priority."""
    if priority is None:
        return None
    return _PRIORITY_LEVELS.get(int(priority))


def _journal_timestamp(raw: object) -> datetime | None:
    """Return the UTC timestamp of a journal entry.

    ``__REALTIME_TIMESTAMP`` is microseconds since the Unix epoch.
    """
    if raw is None:
        return None
    try:
        micros = int(raw)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(micros / 1_000_000, tz=timezone.utc)


class LinuxJournalReader(EventLogReader):
    """Read the systemd journal through ``journalctl``."""

    def __init__(
        self,
        executable: str = "journalctl",
        timeout: int = 30,
        priority_filter: str = DEFAULT_PRIORITY_FILTER,
    ) -> None:
        self._executable = executable
        self._timeout = timeout
        self._priority_filter = priority_filter

    def read(
        self,
        channels: tuple[str, ...],
        since: datetime,
        limit: int,
    ) -> EventLogReadResult:
        records: list[SystemEventRecord] = []
        errors: list[str] = []

        for channel in channels:
            match = JOURNAL_MATCHES.get(channel, channel)
            command = [
                self._executable,
                "--since",
                _journal_since(since),
                "--output=json",
                "--no-pager",
                "--lines",
                str(limit),
            ]
            if self._priority_filter:
                command.extend(["--priority", self._priority_filter])
            command.append(match)

            try:
                output = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout,
                    check=False,
                )
            except FileNotFoundError:
                return EventLogReadResult(
                    records=(),
                    error=(
                        "journalctl is not available on this system, so the "
                        "systemd journal cannot be read."
                    ),
                    available=False,
                )
            except Exception as exc:
                errors.append(f"{channel}: {exc}")
                continue

            if output.returncode != 0:
                errors.append(
                    f"{channel}: journalctl exited {output.returncode}: "
                    f"{output.stderr.strip()}"
                )
                continue

            records.extend(
                _parse_journal_output(output.stdout, channel, limit)
            )

        if errors and not records:
            return EventLogReadResult(
                records=(),
                error="; ".join(errors),
                available=False,
            )
        return EventLogReadResult(records=tuple(records), available=True)


def _journal_since(since: datetime) -> str:
    """Return the ``journalctl --since`` argument for a UTC instant."""
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    return since.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _parse_journal_output(
    output: str,
    channel: str,
    limit: int,
) -> list[SystemEventRecord]:
    """Parse ``journalctl --output=json`` lines into records."""
    records: list[SystemEventRecord] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue

        timestamp = _journal_timestamp(entry.get("__REALTIME_TIMESTAMP"))
        if timestamp is None:
            continue

        message = str(entry.get("MESSAGE", "") or "")
        identifier = entry.get("SYSLOG_IDENTIFIER")
        unit = entry.get("_SYSTEMD_UNIT")
        provider = str(identifier or unit or channel)
        priority_raw = entry.get("PRIORITY")
        try:
            priority = int(priority_raw) if priority_raw is not None else None
        except (TypeError, ValueError):
            priority = None

        cursor = entry.get("__CURSOR")
        if cursor:
            record_id = f"journal:{cursor}"
        else:
            digest = hashlib.sha256(
                "|".join(
                    [
                        timestamp.isoformat(),
                        provider,
                        str(priority),
                        message,
                    ]
                ).encode("utf-8")
            ).hexdigest()[:32]
            record_id = f"journal:{digest}"

        records.append(
            SystemEventRecord(
                record_id=record_id,
                timestamp=timestamp,
                channel=channel,
                provider=provider,
                event_id=priority,
                level=_priority_to_level(priority),
                message=truncate_message(message),
                source=SOURCE_LINUX_LOCAL,
            )
        )
        if len(records) >= limit:
            break
    return records


class LinuxJournalCollector(SystemEventCollector):
    """Collect a bounded, filtered, deduplicated set of journal entries.

    The collector applies the same guarantees as the Windows event-log
    collector: an explicit allowlist, a bounded window, a record limit,
    stable identifiers, checkpoint de-duplication, and honest error reporting.
    """

    default_checkpoint_name = "reson_journal_checkpoint.json"

    def __init__(
        self,
        host_id: str | None = None,
        source: str = SOURCE_LINUX_LOCAL,
        allowed_channels: tuple[str, ...] = DEFAULT_ALLOWED_CHANNELS,
        allowed_event_ids: dict[str, tuple[int, ...]] | None = None,
        window_minutes: int = 60,
        max_records: int = 200,
        checkpoint_path: str | Path | None = None,
        reader: EventLogReader | None = None,
    ) -> None:
        super().__init__(
            host_id=host_id,
            source=source,
            allowed_channels=allowed_channels,
            allowed_event_ids=(
                dict(allowed_event_ids)
                if allowed_event_ids is not None
                else dict(DEFAULT_ALLOWED_EVENT_IDS)
            ),
            window_minutes=window_minutes,
            max_records=max_records,
            checkpoint_path=checkpoint_path,
            reader=reader,
        )

    @staticmethod
    def _default_reader() -> EventLogReader:
        return LinuxJournalReader()


__all__ = [
    "DEFAULT_ALLOWED_CHANNELS",
    "DEFAULT_ALLOWED_EVENT_IDS",
    "JOURNAL_MATCHES",
    "LinuxJournalCollector",
    "LinuxJournalReader",
]
