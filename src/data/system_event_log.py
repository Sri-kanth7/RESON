"""Shared system-event collection contract for RESON.

Windows, Linux and macOS each expose an operating-system event log, but they
expose it differently. RESON collects the same *kind* of evidence from all
three and applies the same guarantees to all of them:

* **Allowlist only.** An explicit set of categories is collected. RESON never
  ingests an entire event log.
* **Bounded windows.** Every collection is limited to a recent time window and
  a maximum number of records, so collection cannot become an unbounded
  ingestion source.
* **Stable identifiers.** Each record carries a stable identifier, so the same
  event is never ingested twice.
* **Deduplication with a checkpoint.** A checkpoint file records what has
  already been ingested; a restart continues where it left off instead of
  re-ingesting the whole log.
* **Permission aware.** When the log cannot be read, the collector reports an
  explicit unavailable state with the reason — it never returns an empty list
  that looks like "no events".
* **Safe content.** Only the event's own message text is collected, bounded in
  length. RESON does not collect unrelated log content, credentials, or
  payload data.

This module holds the shared pieces: the record contract, the read result, the
reader seam, and the collector that enforces the guarantees above. The
platform implementations live in ``windows_event_log.py``,
``linux_journal.py`` and ``macos_unified_log.py``.

The module is import-safe on every operating system: no platform-specific
import happens at module scope.
"""

from __future__ import annotations

import json
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.data.schemas import SOURCE_UNKNOWN

#: Severity mapping for the levels RESON understands.
LEVEL_CRITICAL = "CRITICAL"
LEVEL_ERROR = "ERROR"
LEVEL_WARNING = "WARNING"
LEVEL_INFORMATION = "INFORMATION"

#: Maximum length of a collected event message.
MAX_MESSAGE_LENGTH = 2000


def truncate_message(text: str, limit: int = MAX_MESSAGE_LENGTH) -> str:
    """Bound the size of a collected event message."""
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


@dataclass(frozen=True)
class SystemEventRecord:
    """One collected system event.

    ``record_id`` is stable across restarts so the same event is only ever
    ingested once.
    """

    record_id: str
    """Stable identifier of the event."""
    timestamp: datetime
    """Event time in UTC."""
    channel: str
    """Category the record came from (a Windows channel, a Linux unit or
    syslog identifier, a macOS process)."""
    provider: str | None = None
    """Event provider (source application) name, when available."""
    event_id: int | None = None
    """Platform event identifier, when the platform exposes one."""
    level: str | None = None
    """Normalized severity."""
    message: str = ""
    """Event message text (truncated to a bounded length)."""
    source: str | None = None
    """Telemetry source tag. The collector stamps it when a reader leaves it
    unset."""
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

    Implementations are injected into :class:`SystemEventCollector` so the
    collector's filtering, deduplication and checkpoint logic is testable
    offline against a controlled fake reader, on any operating system.
    """

    @abstractmethod
    def read(
        self,
        channels: tuple[str, ...],
        since: datetime,
        limit: int,
    ) -> EventLogReadResult:
        """Read recent records from the given categories.

        ``since`` is the oldest timestamp the caller is interested in and
        ``limit`` the maximum number of records to return. An implementation
        that cannot read the log must return a result whose ``error`` is set
        and whose ``available`` is ``False`` — never an empty list that looks
        like "there were no events".
        """


class SystemEventCollector:
    """Collect a bounded, filtered, deduplicated set of system events.

    Parameters
    ----------
    host_id:
        Host the events are collected on.
    source:
        Telemetry source tag stamped on the collected records.
    allowed_channels:
        Category allowlist. Only these categories are collected.
    allowed_event_ids:
        Per-category identifier allowlist. An empty tuple for a category means
        every event in that category is collected.
    window_minutes:
        Maximum age of a collected event, in minutes. Bounds each collection.
    max_records:
        Maximum number of records returned by one collection.
    checkpoint_path:
        File used to persist the deduplication checkpoint. When ``None``, a
        default path under the user's temporary directory is used.
    reader:
        Event log reader to use. Each platform supplies its own default.
    """

    #: Name of the default checkpoint file for this platform.
    default_checkpoint_name = "reson_system_events_checkpoint.json"

    def __init__(
        self,
        *,
        host_id: str | None = None,
        source: str = SOURCE_UNKNOWN,
        allowed_channels: tuple[str, ...] = (),
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
            else {}
        )
        self._window_minutes = max(1, int(window_minutes))
        self._max_records = max(1, int(max_records))
        self._checkpoint_path = (
            Path(checkpoint_path)
            if checkpoint_path is not None
            else Path(tempfile.gettempdir()) / self.default_checkpoint_name
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
        """Return the platform's default reader."""
        raise NotImplementedError

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

    @property
    def allowed_channels(self) -> tuple[str, ...]:
        """The category allowlist in force."""
        return self._allowed_channels

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
            records=tuple(
                self._with_source(record) for record in unique.values()
            ),
            available=True,
        )

    def _with_source(self, record: SystemEventRecord) -> SystemEventRecord:
        """Stamp the collector's telemetry source on a record without one."""
        if record.source:
            return record
        return replace(record, source=self._source)

    def _is_allowed(self, record: SystemEventRecord) -> bool:
        """Return whether the record passes the category/identifier allowlist."""
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
