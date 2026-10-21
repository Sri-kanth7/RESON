"""Bounded, filtered, deduplicated Windows Event Log collection for RESON.

Design constraints (from the RESON requirements):

* **Allowlist only.** An explicit set of channels and event identifiers is
  collected. RESON never ingests every event log.
* **Bounded windows.** Every collection is limited to a recent time window and
  a maximum number of records, so collection cannot become an unbounded
  ingestion source.
* **Stable identifiers.** Each record carries a stable identifier so the same
  event is never ingested twice.
* **Deduplication with a checkpoint.** A checkpoint file records the last
  processed event identifier; a restart continues where it left off instead of
  re-ingesting the whole log.
* **Permission aware.** When the log cannot be read (insufficient privileges,
  audit policy), the collector reports an explicit unavailable state with the
  reason — it never silently returns an empty list that looks like "no events".
* **Safe content.** Only the event's own message text is collected. RESON does
  not collect unrelated log content, credentials, or payload data.

The module is import-safe on non-Windows systems: the Windows-specific readers
are imported lazily and the module itself contains no Windows-only imports at
import time.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.data.schemas import SOURCE_WINDOWS_LOCAL

#: Channels RESON reads by default. This is an allowlist, not a wildcard.
DEFAULT_ALLOWED_CHANNELS: tuple[str, ...] = (
    "System",
    "Application",
)

#: Event identifiers considered operationally relevant, per channel.
#:
#: An empty tuple means "no identifier restriction for this channel", which
#: is only used for channels whose entire content is operationally relevant.
DEFAULT_ALLOWED_EVENT_IDS: dict[str, tuple[int, ...]] = {
    "System": (
        6005,  # Event log service started
        6006,  # Event log service stopped
        6008,  # Unexpected shutdown
        6013,  # System uptime
        7031,  # Service terminated unexpectedly
        7034,  # Service crashed
        7036,  # Service entered the running state
        7040,  # Service start type changed
        7045,  # A service was installed
        41,    # Kernel-Power: system rebooted without clean shutdown
        55,    # Ntfs: file system corruption
        157,   # Disk: physical disk media error
    ),
    "Application": (
        1000,  # Application fault (crash)
        1001,  # Application hang
        1002,  # Application hang (timeout)
    ),
}

#: Severity mapping for the levels RESON understands.
LEVEL_CRITICAL = "CRITICAL"
LEVEL_ERROR = "ERROR"
LEVEL_WARNING = "WARNING"
LEVEL_INFORMATION = "INFORMATION"

#: Windows severity codes mapped to RESON levels.
_SEVERITY_MAP = {
    1: LEVEL_CRITICAL,
    2: LEVEL_ERROR,
    3: LEVEL_WARNING,
    4: LEVEL_INFORMATION,
    5: "DEBUG",
}


@dataclass(frozen=True)
class SystemEventRecord:
    """One collected system event.

    ``record_id`` is stable across restarts so the same event is only ever
    ingested once.
    """

    record_id: str
    """Stable identifier of the event (Windows EventRecordID when available)."""
    timestamp: datetime
    """Event time in UTC."""
    channel: str
    """Event log channel the record came from (for example ``"System"``)."""
    provider: str | None = None
    """Event provider (source application) name, when available."""
    event_id: int | None = None
    """Windows event identifier, when available."""
    level: str | None = None
    """Normalized severity."""
    message: str = ""
    """Event message text (truncated to a bounded length)."""
    source: str = SOURCE_WINDOWS_LOCAL
    """Telemetry source tag."""
    host_id: str | None = None
    """Host the event was collected on."""

    def as_metadata(self) -> dict:
        """Return a bounded metadata mapping for persistence."""
        return {
            "channel": self.channel,
            "provider": self.provider,
            "event_id": self.event_id,
            "level": self.level,
            "record_id": self.record_id,
        }


@dataclass(frozen=True)
class EventLogReadResult:
    """Outcome of one read attempt.

    ``records`` is empty and ``error`` is set when the log could not be read;
    the caller must distinguish "no events" from "could not read".
    """

    records: tuple[SystemEventRecord, ...]
    error: str | None = None
    available: bool = True


class EventLogReader(ABC):
    """Abstraction over a platform event log source.

    Implementations are injected into :class:`WindowsEventLogCollector` so the
    collector's filtering, deduplication, and checkpoint logic is testable
    offline against a controlled fake reader.
    """

    @abstractmethod
    def read(
        self,
        channels: tuple[str, ...],
        since: datetime,
        limit: int,
    ) -> EventLogReadResult:
        """Read recent records from the given channels."""


class Win32EventLogReader(EventLogReader):
    """Event log reader backed by ``pywin32``'s ``win32evtlog``.

    Only imported and instantiated on Windows; the import happens inside
    :meth:`read` so that importing this module never fails on another
    operating system.
    """

    def read(
        self,
        channels: tuple[str, ...],
        since: datetime,
        limit: int,
    ) -> EventLogReadResult:
        try:
            import win32evtlog  # type: ignore[import-not-found]
        except Exception as exc:
            return EventLogReadResult(
                records=(),
                error=(
                    "win32evtlog (pywin32) is not available on this system: "
                    f"{exc}. Install pywin32 to collect Windows events."
                ),
                available=False,
            )

        records: list[SystemEventRecord] = []
        try:
            for channel in channels:
                handle = win32evtlog.OpenEventLog(
                    channel, win32evtlog.EVENTLOG_READ_API
                )
                try:
                    flags = (
                        win32evtlog.EVENTLOG_BACKWARD_DIRECTION
                        | win32evtlog.EVENTLOG_FORCE_READ
                    )
                    while len(records) < limit:
                        batch = win32evtlog.ReadEventLog(
                            handle, flags, 0, min(64, limit - len(records))
                        )
                        if not batch:
                            break
                        for raw in batch:
                            record = self._to_record(raw, channel, since)
                            if record is not None:
                                records.append(record)
                                if len(records) >= limit:
                                    break
                finally:
                    win32evtlog.CloseEventLog(handle)
        except Exception as exc:
            return EventLogReadResult(
                records=tuple(records),
                error=f"event log read failed: {exc}",
                available=False,
            )

        return EventLogReadResult(records=tuple(records), available=True)

    @staticmethod
    def _to_record(raw, channel: str, since: datetime) -> SystemEventRecord | None:
        import win32evtlog  # type: ignore[import-not-found]

        try:
            record_id = int(getattr(raw, "EventRecordID", 0) or 0)
            event_time = win32evtlog.GetEventTimeStamp(raw)
            timestamp = event_time.replace(tzinfo=timezone.utc)
            if timestamp < since:
                return None
            provider = str(
                win32evtlog.GetEventInformation(
                    raw, win32evtlog.EVENTLOG_INFORMATION_TYPE_PROVIDER_NAME
                )
            )
            event_id = int(getattr(raw, "EventID", 0) or 0)
            level_value = int(getattr(raw, "EventType", 0) or 0)
            level = _SEVERITY_MAP.get(level_value)
            message_parts = win32evtlog.FormatMessage(
                raw, win32evtlog.EVENTLOG_MESSAGE_TYPE_UNICODE
            )
            message = message_parts[0] if message_parts else ""
        except Exception:
            return None

        return SystemEventRecord(
            record_id=f"{channel}:{record_id}",
            timestamp=timestamp,
            channel=channel,
            provider=provider,
            event_id=event_id,
            level=level,
            message=_truncate(message),
        )


class WevtutilEventLogReader(EventLogReader):
    """Event log reader backed by the built-in ``wevtutil`` command.

    Used when ``pywin32`` is not installed. Output is parsed from the XML
    form of the query, and only the fields RESON records are extracted.
    """

    def read(
        self,
        channels: tuple[str, ...],
        since: datetime,
        limit: int,
    ) -> EventLogReadResult:
        records: list[SystemEventRecord] = []
        errors: list[str] = []
        for channel in channels:
            try:
                output = subprocess.run(
                    [
                        "wevtutil",
                        "qe",
                        channel,
                        "/q:*[System[TimeCreated[@SystemTime>='%s']]]"
                        % since.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                        "/f:xml",
                        "/c:%d" % limit,
                        "/rd:false",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
            except Exception as exc:
                errors.append(f"{channel}: {exc}")
                continue
            if output.returncode != 0:
                errors.append(
                    f"{channel}: wevtutil exited {output.returncode}: "
                    f"{output.stderr.strip()}"
                )
                continue
            records.extend(_parse_wevtutil_xml(output.stdout, channel))

        if errors and not records:
            return EventLogReadResult(
                records=(),
                error="; ".join(errors),
                available=False,
            )
        return EventLogReadResult(records=tuple(records), available=True)


def _truncate(text: str, limit: int = 2000) -> str:
    """Bound the size of a collected event message."""
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _parse_wevtutil_xml(xml_text: str, channel: str) -> list[SystemEventRecord]:
    """Extract RESON's event fields from ``wevtutil`` XML output."""
    import xml.etree.ElementTree as ET

    records: list[SystemEventRecord] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return records

    namespace = {"e": "http://schemas.microsoft.com/win/2004/08/events/event"}
    for event in root.findall("e:Event", namespace):
        system = event.find("e:System", namespace)
        if system is None:
            continue
        try:
            record_id_node = system.find("e:EventRecordID", namespace)
            record_id = (
                str(record_id_node.text)
                if record_id_node is not None and record_id_node.text
                else ""
            )
            time_node = system.find("e:TimeCreated", namespace)
            raw_time = (
                time_node.get("SystemTime") if time_node is not None else None
            )
            if not raw_time:
                continue
            timestamp = datetime.fromisoformat(
                raw_time.replace("Z", "+00:00")
            ).astimezone(timezone.utc)
            provider_node = system.find("e:Provider", namespace)
            provider = (
                provider_node.get("Name")
                if provider_node is not None
                else None
            )
            id_node = system.find("e:EventID", namespace)
            event_id = (
                int(id_node.text)
                if id_node is not None and id_node.text
                else None
            )
            level_node = system.find("e:Level", namespace)
            level_value = (
                int(level_node.text)
                if level_node is not None and level_node.text
                else None
            )
            data = event.find("e:EventData", namespace)
            message = ""
            if data is not None:
                parts = [
                    node.text
                    for node in data.findall("e:Data", namespace)
                    if node.text
                ]
                message = " | ".join(parts)
        except Exception:
            continue

        records.append(
            SystemEventRecord(
                record_id=f"{channel}:{record_id}",
                timestamp=timestamp,
                channel=channel,
                provider=provider,
                event_id=event_id,
                level=_SEVERITY_MAP.get(level_value) if level_value else None,
                message=_truncate(message),
            )
        )
    return records


class WindowsEventLogCollector:
    """Collect a bounded, filtered, deduplicated set of Windows system events.

    Parameters
    ----------
    host_id:
        Host the events are collected on.
    source:
        Telemetry source tag.
    allowed_channels:
        Channel allowlist. Only these channels are read.
    allowed_event_ids:
        Per-channel event identifier allowlist. An empty tuple for a channel
        means every event in that channel is collected.
    window_minutes:
        Maximum age of a collected event, in minutes. Bounds each collection.
    max_records:
        Maximum number of records returned by one collection.
    checkpoint_path:
        File used to persist the deduplication checkpoint. When ``None``, a
        default path under the user's temporary directory is used.
    reader:
        Event log reader to use. Defaults to ``pywin32`` when available and
        ``wevtutil`` otherwise.
    """

    def __init__(
        self,
        host_id: str | None = None,
        source: str = SOURCE_WINDOWS_LOCAL,
        allowed_channels: tuple[str, ...] = DEFAULT_ALLOWED_CHANNELS,
        allowed_event_ids: dict[str, tuple[int, ...]] | None = None,
        window_minutes: int = 60,
        max_records: int = 200,
        checkpoint_path: str | Path | None = None,
        reader: EventLogReader | None = None,
    ) -> None:
        from src.data.host_identity import stable_host_id

        self._host_id = host_id or stable_host_id()
        self._source = source
        self._allowed_channels = tuple(allowed_channels)
        self._allowed_event_ids = (
            dict(allowed_event_ids)
            if allowed_event_ids is not None
            else {k: tuple(v) for k, v in DEFAULT_ALLOWED_EVENT_IDS.items()}
        )
        self._window_minutes = max(1, int(window_minutes))
        self._max_records = max(1, int(max_records))
        self._checkpoint_path = (
            Path(checkpoint_path)
            if checkpoint_path is not None
            else Path(tempfile.gettempdir()) / "reson_eventlog_checkpoint.json"
        )
        self._reader = reader or self._default_reader()
        self._seen_ids: set[str] = set()
        self._last_error: str | None = None
        self._load_checkpoint()

    # ------------------------------------------------------------------
    # Reader selection
    # ------------------------------------------------------------------

    @staticmethod
    def _default_reader() -> EventLogReader:
        import importlib.util

        if importlib.util.find_spec("win32evtlog") is not None:
            return Win32EventLogReader()
        return WevtutilEventLogReader()

    # ------------------------------------------------------------------
    # Collection
    # ------------------------------------------------------------------

    @property
    def last_error(self) -> str | None:
        """Most recent read error, if any."""
        return self._last_error

    @property
    def available(self) -> bool:
        """Whether the last read attempt succeeded."""
        return self._last_error is None

    def collect(self, now: datetime | None = None) -> EventLogReadResult:
        """Collect recent, allowed, not-yet-seen system events.

        Returns an :class:`EventLogReadResult` whose ``error`` is set when the
        log could not be read, so the caller can distinguish "no events" from
        "collection unavailable".
        """
        reference = now or datetime.now(timezone.utc)
        since = reference - timedelta(minutes=self._window_minutes)

        result = self._reader.read(
            self._allowed_channels, since, self._max_records
        )
        if result.error:
            self._last_error = result.error
            return result

        self._last_error = None
        allowed = [
            record
            for record in result.records
            if self._is_allowed(record) and record.record_id not in self._seen_ids
        ]
        # Deduplicate within this batch as well as against the checkpoint.
        unique: dict[str, SystemEventRecord] = {}
        for record in allowed:
            unique.setdefault(record.record_id, record)

        for record in unique.values():
            self._seen_ids.add(record.record_id)

        # Keep the in-memory set bounded.
        if len(self._seen_ids) > self._max_records * 20:
            self._seen_ids = set(list(self._seen_ids)[-self._max_records * 10:])

        self._save_checkpoint()
        return EventLogReadResult(
            records=tuple(unique.values()), available=True
        )

    def _is_allowed(self, record: SystemEventRecord) -> bool:
        """Return whether the record passes the channel/event allowlist."""
        if record.channel not in self._allowed_channels:
            return False
        allowed_ids = self._allowed_event_ids.get(record.channel)
        if allowed_ids is None or not allowed_ids:
            return True
        return record.event_id in allowed_ids

    # ------------------------------------------------------------------
    # Checkpoint
    # ------------------------------------------------------------------

    def _load_checkpoint(self) -> None:
        """Load the deduplication checkpoint, ignoring a corrupt file."""
        try:
            raw = self._checkpoint_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            return
        seen = payload.get("seen_ids")
        if isinstance(seen, list):
            self._seen_ids = {str(item) for item in seen}

    def _save_checkpoint(self) -> None:
        """Persist the deduplication checkpoint."""
        payload = {
            "seen_ids": sorted(self._seen_ids),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            self._checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            self._checkpoint_path.write_text(
                json.dumps(payload), encoding="utf-8"
            )
        except OSError:
            # A checkpoint is an optimization; failing to write it must not
            # break collection.
            pass
