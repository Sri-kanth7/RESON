"""Standalone RESON collector agent.

The agent is a normal operating-system process that continuously collects real
telemetry from the machine it runs on and ingests it into the configured
storage backend. It exists so that continuous monitoring does **not** depend on
the Streamlit application, its page reruns, or a browser session being open.

Usage::

    python -m src.data.agent --backend sqlite --interval 5
    python -m src.data.agent --backend sqlite --retention-days 30 --events

The agent is deliberately conservative:

* It only collects the telemetry categories it is configured for.
* It never transmits anything anywhere by itself; remote transport is a
  separate, explicitly configured feature (see the deployment docs).
* It applies a retention window so local storage does not grow without bound.
* It reports its own health honestly: a failed collection or a failed write
  is surfaced, never silently swallowed.
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from src.data.collector import (
    Collector,
    CollectorHealth,
    SamplingPolicy,
    apply_sampling_policy,
    collection_to_metrics,
)
from src.data.collector_factory import create_collector
from src.data.ingestion import TelemetryIngestionService
from src.data.schemas import (
    Environment,
    Event,
    EventType,
)
from src.data.storage import Storage

#: Minimum permitted collection interval, in seconds.
MIN_INTERVAL_SECONDS = 1.0

#: Minimum permitted retention window, in days.
MIN_RETENTION_DAYS = 1


@dataclass
class AgentConfig:
    """Configuration for the standalone collector agent."""

    storage_backend: str = "sqlite"
    """Storage backend the agent writes into."""
    interval_seconds: float = 5.0
    """Collection interval in seconds."""
    retention_days: int | None = 30
    """Retention window in days. ``None`` disables retention pruning."""
    collect_events: bool = False
    """Whether to collect Windows system events (Windows only)."""
    event_window_minutes: int = 60
    """Maximum age of a collected system event, in minutes."""
    event_max_records: int = 200
    """Maximum number of system events collected per cycle."""
    platform: str | None = None
    """Platform override; ``None`` uses the running platform."""
    host_id: str | None = None
    """Host identity override; ``None`` uses the stable platform identity."""
    process_sample_every: int = 5
    """Publish process metrics on every Nth cycle."""
    slow_sample_every: int = 30
    """Publish disk/network/battery/sensor metrics on every Nth cycle."""
    max_processes: int = 15
    """Maximum processes published per cycle, ranked by CPU."""

    def validate(self) -> "AgentConfig":
        """Validate the configuration, raising ``ValueError`` when invalid."""
        if self.interval_seconds < MIN_INTERVAL_SECONDS:
            raise ValueError(
                "interval_seconds must be at least "
                f"{MIN_INTERVAL_SECONDS} seconds; got {self.interval_seconds}."
            )
        if (
            self.retention_days is not None
            and self.retention_days < MIN_RETENTION_DAYS
        ):
            raise ValueError(
                "retention_days must be at least "
                f"{MIN_RETENTION_DAYS} day(s) or None; got {self.retention_days}."
            )
        if self.event_max_records < 1:
            raise ValueError(
                "event_max_records must be at least 1; "
                f"got {self.event_max_records}."
            )
        if self.process_sample_every < 1:
            raise ValueError("process_sample_every must be at least 1")
        if self.slow_sample_every < 1:
            raise ValueError("slow_sample_every must be at least 1")
        if self.max_processes < 0:
            raise ValueError("max_processes must not be negative")
        return self

    def sampling_policy(self) -> SamplingPolicy:
        """Return the collector sampling policy described by this config."""
        return SamplingPolicy(
            process_sample_every=self.process_sample_every,
            slow_sample_every=self.slow_sample_every,
            max_processes=self.max_processes,
        )


@dataclass
class AgentHealth:
    """Health report of a running (or stopped) agent."""

    running: bool
    storage_backend: str
    host_id: str
    source: str
    last_collection: datetime | None = None
    collection_count: int = 0
    error_count: int = 0
    last_error: str | None = None
    records_ingested: int = 0
    events_ingested: int = 0
    retention: dict[str, int] = field(default_factory=dict)
    collector: CollectorHealth | None = None


#: Windows event identifiers mapped onto RESON event types.
_EVENT_ID_TO_TYPE: dict[int, EventType] = {
    6005: EventType.RESTART,
    6006: EventType.RESTART,
    6008: EventType.RESTART,
    41: EventType.RESTART,
    7031: EventType.SERVICE_FAILURE,
    7034: EventType.SERVICE_FAILURE,
    1000: EventType.SERVICE_FAILURE,
    1001: EventType.SERVICE_FAILURE,
    1002: EventType.SERVICE_FAILURE,
    55: EventType.SERVICE_FAILURE,
    157: EventType.SERVICE_FAILURE,
    7040: EventType.CONFIG_CHANGE,
    7045: EventType.CONFIG_CHANGE,
}


def map_event_type(event_id: int | None) -> EventType:
    """Map a Windows event identifier onto a RESON event type.

    Unmapped identifiers become ``SERVICE_FAILURE`` (the generic operational
    failure category) rather than being dropped.
    """
    if event_id is None:
        return EventType.SERVICE_FAILURE
    return _EVENT_ID_TO_TYPE.get(event_id, EventType.SERVICE_FAILURE)


class CollectorAgent:
    """Continuously collect real telemetry and ingest it into storage.

    The agent owns a platform collector and a publish callback. It can be
    driven either by the collector's own background thread
    (:meth:`start`) or by an external loop calling :meth:`run_once`.
    """

    def __init__(
        self,
        config: AgentConfig | None = None,
        storage: Storage | None = None,
        collector: Collector | None = None,
        event_collector=None,
    ) -> None:
        self._config = (config or AgentConfig()).validate()
        self._storage = storage
        self._collector = collector or create_collector(
            platform_name=self._config.platform,
            host_id=self._config.host_id,
        )
        self._policy = self._config.sampling_policy()
        self._event_collector = event_collector
        self._ingestion: TelemetryIngestionService | None = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_collection: datetime | None = None
        self._collection_count = 0
        self._error_count = 0
        self._last_error: str | None = None
        self._records_ingested = 0
        self._events_ingested = 0
        self._retention: dict[str, int] = {}
        self._cycle = 0

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def collector(self) -> Collector:
        """The platform collector used by this agent."""
        return self._collector

    @property
    def config(self) -> AgentConfig:
        """The validated agent configuration."""
        return self._config

    @property
    def is_running(self) -> bool:
        """Whether the agent's collection loop is active."""
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------

    def _ensure_storage(self) -> Storage:
        """Resolve the storage backend, building it on first use."""
        if self._storage is None:
            from src.orchestrator import create_application

            application = create_application(self._config.storage_backend)
            self._storage = application.storage
            self._ingestion = application.ingestion
        elif self._ingestion is None:
            self._ingestion = TelemetryIngestionService(self._storage)
        return self._storage

    # ------------------------------------------------------------------
    # Collection
    # ------------------------------------------------------------------

    def _publish(self, result) -> None:
        """Publish callback: convert the collection result and ingest it."""
        self._ensure_storage()
        assert self._ingestion is not None
        records = collection_to_metrics(result)
        if records:
            self._ingestion.ingest_batch(records)
            with self._lock:
                self._records_ingested += len(records)

    def run_once(self) -> int:
        """Run one collection cycle and return the number of records ingested.

        Errors are recorded in the agent's health state and re-raised so the
        caller (or the background loop) can react; they are never swallowed.
        """
        storage = self._ensure_storage()
        assert self._ingestion is not None
        try:
            result = self._collector.collect_once()
            with self._lock:
                self._cycle += 1
                cycle = self._cycle
            published = apply_sampling_policy(result, cycle, self._policy)
            records = collection_to_metrics(published)
            if records:
                self._ingestion.ingest_batch(records)
            with self._lock:
                self._records_ingested += len(records)
                self._collection_count += 1
                self._last_collection = datetime.now(timezone.utc)
            self._collect_events(storage)
            self._apply_retention(storage)
            return len(records)
        except Exception as exc:
            with self._lock:
                self._error_count += 1
                self._last_error = str(exc)
            raise

    def _collect_events(self, storage: Storage) -> None:
        """Collect and ingest system events when the feature is enabled."""
        if not self._config.collect_events:
            return
        if self._event_collector is None:
            from src.data.windows_event_log import WindowsEventLogCollector

            self._event_collector = WindowsEventLogCollector(
                host_id=self._collector.host_id,
                source=self._collector.source,
                window_minutes=self._config.event_window_minutes,
                max_records=self._config.event_max_records,
            )
        assert self._ingestion is not None

        result = self._event_collector.collect()
        if result.error:
            # Event collection is optional; a failure is recorded but must not
            # stop metric collection.
            with self._lock:
                self._last_error = result.error
                self._error_count += 1
            return

        events: list[Event] = []
        for record in result.records:
            events.append(
                Event(
                    timestamp=record.timestamp,
                    service=self._collector.host_id,
                    environment=Environment.PRODUCTION,
                    event_type=map_event_type(record.event_id),
                    description=record.message or record.record_id,
                    metadata=record.as_metadata(),
                    source=record.source,
                    host_id=record.host_id,
                )
            )
        if events:
            self._ingestion.ingest_batch(events)
            with self._lock:
                self._events_ingested += len(events)

    def _apply_retention(self, storage: Storage) -> None:
        """Prune records older than the retention window."""
        if self._config.retention_days is None:
            return
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=self._config.retention_days
        )
        removed = storage.purge_before(cutoff)
        with self._lock:
            self._retention = removed

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start continuous collection in a background thread.

        Calling this while already running is a no-op, so a supervisor cannot
        accidentally start two collection loops against the same database.
        """
        if self.is_running:
            return
        self._ensure_storage()
        self._stop_event.clear()
        self._collector.start(
            interval_seconds=self._config.interval_seconds,
            publish=self._publish,
        )
        self._thread = threading.Thread(
            target=self._maintenance_loop,
            name="reson-agent-maintenance",
            daemon=True,
        )
        self._thread.start()

    def _maintenance_loop(self) -> None:
        """Periodically apply retention while the collector runs."""
        while not self._stop_event.wait(60.0):
            if self._stop_event.is_set():
                break
            try:
                self._apply_retention(self._ensure_storage())
            except Exception as exc:
                with self._lock:
                    self._error_count += 1
                    self._last_error = str(exc)

    def stop(self, timeout: float = 10.0) -> None:
        """Stop the collector and join the maintenance thread."""
        self._stop_event.set()
        try:
            self._collector.stop(timeout=timeout)
        except Exception as exc:
            with self._lock:
                self._error_count += 1
                self._last_error = str(exc)
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=timeout)

    def health(self) -> AgentHealth:
        """Return the agent's health report."""
        with self._lock:
            return AgentHealth(
                running=self.is_running,
                storage_backend=self._config.storage_backend,
                host_id=self._collector.host_id,
                source=self._collector.source,
                last_collection=self._last_collection,
                collection_count=self._collection_count,
                error_count=self._error_count,
                last_error=self._last_error,
                records_ingested=self._records_ingested,
                events_ingested=self._events_ingested,
                retention=dict(self._retention),
                collector=self._collector.health(),
            )

    def __enter__(self) -> "CollectorAgent":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()


# ---------------------------------------------------------------------------
# Command line entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the agent's command line parser."""
    parser = argparse.ArgumentParser(
        prog="python -m src.data.agent",
        description=(
            "Run the RESON standalone collector agent: continuously collect "
            "real telemetry from this machine and store it locally."
        ),
    )
    parser.add_argument(
        "--backend",
        default="sqlite",
        choices=("sqlite", "memory", "supabase"),
        help="Storage backend the agent writes into (default: sqlite).",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Collection interval in seconds (default: 5).",
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=30,
        help=(
            "Delete telemetry older than this many days. Use 0 to disable "
            "retention pruning (default: 30)."
        ),
    )
    parser.add_argument(
        "--events",
        action="store_true",
        help=(
            "Also collect Windows system events (Windows only; ignored on "
            "other platforms)."
        ),
    )
    parser.add_argument(
        "--platform",
        default=None,
        help="Platform override (windows, linux, macos).",
    )
    parser.add_argument(
        "--host-id",
        default=None,
        help="Host identity override.",
    )
    parser.add_argument(
        "--process-sample-every",
        type=int,
        default=5,
        help=(
            "Publish process metrics on every Nth cycle. Higher values cut "
            "the process history volume (default: 5)."
        ),
    )
    parser.add_argument(
        "--slow-sample-every",
        type=int,
        default=30,
        help=(
            "Publish disk, network, battery and sensor metrics on every Nth "
            "cycle (default: 30, roughly every 2.5 minutes at a 5s interval)."
        ),
    )
    parser.add_argument(
        "--max-processes",
        type=int,
        default=15,
        help="Maximum processes published per cycle, ranked by CPU (default: 15).",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single collection cycle and exit (useful for testing).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the collector agent from the command line."""
    args = build_parser().parse_args(argv)

    retention: int | None = args.retention_days
    if retention is not None and retention <= 0:
        retention = None

    config = AgentConfig(
        storage_backend=args.backend,
        interval_seconds=args.interval,
        retention_days=retention,
        collect_events=args.events,
        platform=args.platform,
        host_id=args.host_id,
        process_sample_every=args.process_sample_every,
        slow_sample_every=args.slow_sample_every,
        max_processes=args.max_processes,
    )

    agent = CollectorAgent(config=config)
    print(
        f"RESON collector agent starting: "
        f"backend={config.storage_backend} "
        f"host={agent.collector.host_id} "
        f"source={agent.collector.source} "
        f"interval={config.interval_seconds}s"
    )

    if args.once:
        try:
            count = agent.run_once()
        except Exception as exc:
            print(f"Collection failed: {exc}", file=sys.stderr)
            return 1
        print(f"Collected and ingested {count} metric record(s).")
        return 0

    def _handle_signal(signum, frame) -> None:
        print(f"Received signal {signum}; shutting down.")
        agent.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_signal)
        except (ValueError, OSError):
            # Signal handlers cannot be installed in every environment
            # (for example a non-main thread); the agent still stops cleanly
            # on KeyboardInterrupt.
            pass

    agent.start()
    try:
        # A simple polling loop works identically on every platform
        # (``signal.pause`` is not available on Windows).
        while agent.is_running:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        agent.stop()

    health = agent.health()
    print(
        f"Agent stopped: {health.collection_count} collection(s), "
        f"{health.records_ingested} metric(s) ingested, "
        f"{health.error_count} error(s)."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
