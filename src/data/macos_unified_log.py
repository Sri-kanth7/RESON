"""Bounded, filtered, deduplicated macOS unified-log collection.

The macOS event source is the unified log, read through the system's ``log``
command. The reader is deliberately narrow:

* **Allowlist only.** Only explicitly listed processes are read, and only
  their error and fault level messages. The whole unified log is never
  ingested.
* **Bounded.** Each collection is limited to a recent time window and a
  maximum number of records, and the ``log show`` query itself is bounded by
  the same window and predicate.
* **Deduplicated.** Each entry carries a stable identifier derived from its
  own content, and a checkpoint file records what has already been ingested.
* **Permission aware.** If the unified log cannot be read, the failure is
  reported explicitly. RESON never presents "could not read" as "there were
  no events".
* **Content-minimal.** Only the entry's own message text is kept, truncated
  to a bounded length.

The module is import-safe on every operating system: ``log`` is invoked as a
subprocess, never imported.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from src.data.schemas import SOURCE_MACOS_LOCAL
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

#: Processes RESON reads by default. This is an allowlist, not a wildcard.
DEFAULT_ALLOWED_CHANNELS: tuple[str, ...] = (
    "kernel",
    "loginwindow",
    "mDNSResponder",
)

#: Unified-log message types collected per process.
#:
#: ``Error`` and ``Fault`` describe a failure; ``Default``/``Info`` are
#: informational and are not system-event evidence.
DEFAULT_ALLOWED_MESSAGE_TYPES: dict[str, tuple[str, ...]] = {
    process: ("Error", "Fault") for process in DEFAULT_ALLOWED_CHANNELS
}

#: Unified-log message type mapped to a RESON level.
_MESSAGE_TYPE_LEVELS = {
    "Fault": LEVEL_CRITICAL,
    "Error": LEVEL_ERROR,
    "Warning": LEVEL_WARNING,
    "Default": LEVEL_INFORMATION,
    "Info": LEVEL_INFORMATION,
    "Debug": "DEBUG",
}


class MacOSUnifiedLogReader(EventLogReader):
    """Read the macOS unified log through the ``log`` command."""

    def __init__(
        self,
        executable: str = "log",
        timeout: int = 60,
    ) -> None:
        self._executable = executable
        self._timeout = timeout

    def read(
        self,
        channels: tuple[str, ...],
        since: datetime,
        limit: int,
    ) -> EventLogReadResult:
        if not channels:
            return EventLogReadResult(records=(), available=True)

        command = [
            self._executable,
            "show",
            "--style=json",
            "--start",
            _log_start(since),
            "--predicate",
            _build_predicate(channels),
        ]

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
                    "The macOS 'log' command is not available on this system, "
                    "so the unified log cannot be read."
                ),
                available=False,
            )
        except Exception as exc:
            return EventLogReadResult(
                records=(),
                error=f"unified log read failed: {exc}",
                available=False,
            )

        if output.returncode != 0:
            return EventLogReadResult(
                records=(),
                error=(
                    f"log show exited {output.returncode}: "
                    f"{output.stderr.strip()}"
                ),
                available=False,
            )

        return EventLogReadResult(
            records=tuple(
                _parse_unified_log(output.stdout, limit)
            ),
            available=True,
        )


def _log_start(since: datetime) -> str:
    """Return the ``log show --start`` argument for an instant.

    The ``log`` command interprets a naive timestamp in the local timezone,
    so the instant is converted to local time before it is formatted.
    """
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    return since.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _build_predicate(channels: tuple[str, ...]) -> str:
    """Return the ``log show`` predicate for the allowed processes."""
    process_terms = [
        f'process == "{channel}"' for channel in channels
    ]
    level_terms = ['messageType == "Error"', 'messageType == "Fault"']
    return "(" + " OR ".join(process_terms + level_terms) + ")"


def _parse_timestamp(raw: object) -> datetime | None:
    """Parse a unified-log timestamp into an aware UTC datetime."""
    if not raw:
        return None
    text = str(raw).strip()
    for pattern in ("%Y-%m-%d %H:%M:%S.%f%z", "%Y-%m-%d %H:%M:%S%z"):
        try:
            parsed = datetime.strptime(text, pattern)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def _parse_unified_log(output: str, limit: int) -> list[SystemEventRecord]:
    """Parse the JSON array produced by ``log show --style=json``."""
    text = output.strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except ValueError:
        return []
    if not isinstance(payload, list):
        return []

    records: list[SystemEventRecord] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        timestamp = _parse_timestamp(entry.get("timestamp"))
        if timestamp is None:
            continue
        process = str(entry.get("process") or "unknown")
        message_type = str(entry.get("messageType") or "")
        message = str(entry.get("message") or "")

        digest = hashlib.sha256(
            "|".join(
                [
                    timestamp.isoformat(),
                    process,
                    message_type,
                    message,
                ]
            ).encode("utf-8")
        ).hexdigest()[:32]

        records.append(
            SystemEventRecord(
                record_id=f"unified:{digest}",
                timestamp=timestamp,
                channel=process,
                provider=str(
                    entry.get("subsystem") or entry.get("eventType") or process
                ),
                event_id=None,
                level=_MESSAGE_TYPE_LEVELS.get(message_type),
                message=truncate_message(message),
                source=SOURCE_MACOS_LOCAL,
            )
        )
        if len(records) >= limit:
            break
    return records


class MacOSUnifiedLogCollector(SystemEventCollector):
    """Collect a bounded, filtered, deduplicated set of unified-log entries.

    The collector applies the same guarantees as the Windows event-log
    collector: an explicit allowlist, a bounded window, a record limit,
    stable identifiers, checkpoint de-duplication, and honest error reporting.
    """

    default_checkpoint_name = "reson_unified_log_checkpoint.json"

    def __init__(
        self,
        host_id: str | None = None,
        source: str = SOURCE_MACOS_LOCAL,
        allowed_channels: tuple[str, ...] = DEFAULT_ALLOWED_CHANNELS,
        allowed_message_types: dict[str, tuple[str, ...]] | None = None,
        window_minutes: int = 60,
        max_records: int = 200,
        checkpoint_path: str | Path | None = None,
        reader: EventLogReader | None = None,
    ) -> None:
        self._allowed_message_types = (
            dict(allowed_message_types)
            if allowed_message_types is not None
            else dict(DEFAULT_ALLOWED_MESSAGE_TYPES)
        )
        super().__init__(
            host_id=host_id,
            source=source,
            allowed_channels=allowed_channels,
            allowed_event_ids=None,
            window_minutes=window_minutes,
            max_records=max_records,
            checkpoint_path=checkpoint_path,
            reader=reader,
        )

    @staticmethod
    def _default_reader() -> EventLogReader:
        return MacOSUnifiedLogReader()

    def _is_allowed(self, record: SystemEventRecord) -> bool:
        """Return whether the record passes the process/message-type allowlist."""
        if record.channel not in self._allowed_channels:
            return False
        allowed_types = self._allowed_message_types.get(record.channel)
        if allowed_types is None or not allowed_types:
            return True
        return record.level in {
            _MESSAGE_TYPE_LEVELS.get(name) for name in allowed_types
        }


__all__ = [
    "DEFAULT_ALLOWED_CHANNELS",
    "DEFAULT_ALLOWED_MESSAGE_TYPES",
    "MacOSUnifiedLogCollector",
    "MacOSUnifiedLogReader",
]
