"""Real Windows telemetry collection for RESON.

Collects host-level and selected-process resource measurements from the
computer RESON runs on. Built on ``psutil``, which exposes the same Windows
performance counters and process APIs on Windows as on other platforms, so the
module is developed and tested locally and runs on Windows without
administrator privileges for the supported measurements.

Collection is strictly separated from ingestion and analysis: this module
produces structured measurement dataclasses only. It never writes to storage,
never runs the intelligence pipeline, and never starts a background loop on
import. Collection begins only when :meth:`WindowsTelemetryCollector.start` is
explicitly called.

CPU convention (documented, not assumed):

* Host CPU utilization is the percentage of *all* logical CPUs busy, sampled
  since the previous call, in ``[0, 100]``.
* Process CPU utilization is the percentage of *one* logical CPU consumed by
  the process since the previous sample, clamped to ``[0, 100]``. A value of
  100 means one logical CPU fully consumed by this process; it is not
  comparable to host CPU utilization and can never exceed 100 here by design.

Both use non-blocking instantaneous samples (``interval=None``). The first
sample after priming is meaningless, so the collector primes every counter on
start and discards that reading.
"""

from __future__ import annotations

import platform
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Iterable
from uuid import uuid4

import psutil

from src.data.schemas import Environment, Metric

SOURCE_WINDOWS_LOCAL = "windows_local"
"""Telemetry source tag for real host telemetry collected on this computer."""

CPU_PERCENT_MAX = 100.0
"""Process and host CPU values are clamped to this bound."""

DEFAULT_INTERVAL_SECONDS = 5.0
"""Default collection interval, approximately five seconds."""


class CollectorState(str, Enum):
    """Observable lifecycle state of the collector."""

    STOPPED = "stopped"
    RUNNING = "running"
    ERROR = "error"


@dataclass(frozen=True)
class HostMeasurement:
    """One host-level resource sample."""

    timestamp: datetime
    """Observation time in UTC."""
    host_id: str
    """Stable host reference (for example the host name)."""
    source: str
    """Telemetry source, e.g. ``"windows_local"``."""
    cpu_percent: float | None
    """Host CPU utilization in percent, or ``None`` when unavailable."""
    memory_percent: float | None
    """Physical memory utilization in percent, or ``None`` when unavailable."""
    memory_used_bytes: int | None
    """Physical memory used in bytes, or ``None`` when unavailable."""
    memory_total_bytes: int | None
    """Physical memory total in bytes, or ``None`` when unavailable."""
    ok: bool
    """Whether the host sample was collected successfully."""
    error: str | None = None
    """Collection error when ``ok`` is ``False``; a genuine zero is ``0.0``."""


@dataclass(frozen=True)
class ProcessMeasurement:
    """One selected-process resource sample."""

    timestamp: datetime
    """Observation time in UTC."""
    host_id: str
    source: str
    pid: int
    """Process identifier."""
    name: str
    """Process name (not persisted; available live from the collector)."""
    cpu_percent: float | None
    """Process CPU utilization in percent of one logical CPU, or ``None``."""
    memory_rss_bytes: int | None
    """Process resident set size in bytes, or ``None`` when unavailable."""
    status: str | None
    """Process status as reported by the operating system."""
    ok: bool
    error: str | None = None


@dataclass(frozen=True)
class CollectionResult:
    """One collection cycle: host sample plus process samples."""

    host: HostMeasurement
    processes: tuple[ProcessMeasurement, ...]


@dataclass
class ObservationSession:
    """Identifiable observation session with a start and an end."""

    id: str
    host_id: str
    source: str
    start_time: datetime
    end_time: datetime | None = None
    status: str = "active"  # "active" | "stopped" | "error"
    sample_count: int = 0
    error_count: int = 0


def _clamp_percent(value: float) -> float:
    """Clamp a percentage into ``[0, 100]`` without inventing a missing value."""
    if value is None:
        return None  # type: ignore[return-value]
    return min(CPU_PERCENT_MAX, max(0.0, float(value)))


def observations_to_metrics(
    host: HostMeasurement | None,
    processes: Iterable[ProcessMeasurement] = (),
) -> list[Metric]:
    """Convert collector output into canonical :class:`~src.data.schemas.Metric` records.

    Pure data shaping: no ingestion, no storage, no analysis. Unavailable
    measurements (``ok is False`` or a ``None`` value) are omitted rather than
    recorded as zero, so a collection failure is never disguised as a
    normal zero-valued observation.
    """

    records: list[Metric] = []
    if host is not None:
        base = dict(
            timestamp=host.timestamp,
            service=host.host_id,
            environment=Environment.PRODUCTION,
            source=host.source,
            host_id=host.host_id,
        )
        if host.cpu_percent is not None:
            records.append(
                Metric(
                    **base,
                    metric_name="cpu_percent",
                    value=host.cpu_percent,
                    unit="percent",
                )
            )
        if host.memory_percent is not None:
            records.append(
                Metric(
                    **base,
                    metric_name="memory_percent",
                    value=host.memory_percent,
                    unit="percent",
                )
            )
        if host.memory_used_bytes is not None:
            records.append(
                Metric(
                    **base,
                    metric_name="memory_used_bytes",
                    value=float(host.memory_used_bytes),
                    unit="bytes",
                )
            )
        if host.memory_total_bytes is not None:
            records.append(
                Metric(
                    **base,
                    metric_name="memory_total_bytes",
                    value=float(host.memory_total_bytes),
                    unit="bytes",
                )
            )

    for process in processes:
        if not process.ok:
            continue
        base = dict(
            timestamp=process.timestamp,
            service=f"{process.host_id}:{process.pid}",
            environment=Environment.PRODUCTION,
            source=process.source,
            host_id=process.host_id,
        )
        if process.cpu_percent is not None:
            records.append(
                Metric(
                    **base,
                    metric_name="process_cpu_percent",
                    value=process.cpu_percent,
                    unit="percent",
                )
            )
        if process.memory_rss_bytes is not None:
            records.append(
                Metric(
                    **base,
                    metric_name="process_memory_rss_bytes",
                    value=float(process.memory_rss_bytes),
                    unit="bytes",
                )
            )

    return records


class IterableProcesses(tuple[ProcessMeasurement, ...]):
    """Placeholder type alias kept for readability; ``tuple`` is used below."""


class WindowsTelemetryCollector:
    """Collect real host and process resource measurements on a schedule.

    Parameters
    ----------
    host_id:
        Stable host reference. Defaults to the host name from
        :func:`platform.node`.
    source:
        Telemetry source tag, defaulting to ``"windows_local"`` so real
        measurements are distinguishable from simulated telemetry.
    """

    def __init__(self, host_id: str | None = None, source: str = SOURCE_WINDOWS_LOCAL) -> None:
        self._host_id = host_id or platform.node()
        self._source = source
        self._state = CollectorState.STOPPED
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._session: ObservationSession | None = None
        self._last_host: HostMeasurement | None = None
        self._last_processes: tuple[ProcessMeasurement, ...] = ()
        self._last_error: str | None = None
        self._publish: (
            Callable[[HostMeasurement, tuple[ProcessMeasurement, ...]], None] | None
        ) = None

    # ------------------------------------------------------------------
    # Observable state
    # ------------------------------------------------------------------

    @property
    def host_id(self) -> str:
        return self._host_id

    @property
    def source(self) -> str:
        return self._source

    @property
    def state(self) -> CollectorState:
        with self._lock:
            return self._state

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def session(self) -> ObservationSession | None:
        with self._lock:
            return self._session

    @property
    def last_host_measurement(self) -> HostMeasurement | None:
        with self._lock:
            return self._last_host

    @property
    def last_process_measurements(self) -> tuple[ProcessMeasurement, ...]:
        with self._lock:
            return self._last_processes

    @property
    def last_error(self) -> str | None:
        with self._lock:
            return self._last_error

    # ------------------------------------------------------------------
    # One-off collection
    # ------------------------------------------------------------------

    def collect_host(self) -> HostMeasurement:
        """Collect one host-level sample (no scheduling side effects)."""

        timestamp = datetime.now(timezone.utc)
        try:
            cpu_raw = psutil.cpu_percent(interval=None)
            memory = psutil.virtual_memory()
            return HostMeasurement(
                timestamp=timestamp,
                host_id=self._host_id,
                source=self._source,
                cpu_percent=_clamp_percent(cpu_raw),
                memory_percent=_clamp_percent(memory.percent),
                memory_used_bytes=memory.used,
                memory_total_bytes=memory.total,
                ok=True,
            )
        except Exception as exc:  # hardware/OS access is not guaranteed
            return HostMeasurement(
                timestamp=timestamp,
                host_id=self._host_id,
                source=self._source,
                cpu_percent=None,
                memory_percent=None,
                memory_used_bytes=None,
                memory_total_bytes=None,
                ok=False,
                error=f"host collection failed: {exc}",
            )

    def collect_processes(self) -> tuple[ProcessMeasurement, ...]:
        """Collect selected-process samples, skipping inaccessible processes."""

        timestamp = datetime.now(timezone.utc)
        results: list[ProcessMeasurement] = []

        for proc in psutil.process_iter(["pid", "name"]):
            pid: int | None = None
            name = ""
            try:
                pid = int(proc.info.get("pid") or 0)
                name = proc.info.get("name") or ""
                cpu_raw = proc.cpu_percent(interval=None)
                memory_info = proc.memory_info()
                status = proc.status()
                results.append(
                    ProcessMeasurement(
                        timestamp=timestamp,
                        host_id=self._host_id,
                        source=self._source,
                        pid=pid,
                        name=name,
                        cpu_percent=_clamp_percent(cpu_raw),
                        memory_rss_bytes=int(getattr(memory_info, "rss", 0) or 0),
                        status=str(status),
                        ok=True,
                    )
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                # Short-lived, terminated, or inaccessible processes are skipped
                # rather than failing the whole collection cycle.
                continue
            except Exception as exc:  # one process must not crash the cycle
                results.append(
                    ProcessMeasurement(
                        timestamp=timestamp,
                        host_id=self._host_id,
                        source=self._source,
                        pid=pid if pid is not None else 0,
                        name=name,
                        cpu_percent=None,
                        memory_rss_bytes=None,
                        status=None,
                        ok=False,
                        error=f"process {pid or 'unknown'} collection failed: {exc}",
                    )
                )

        return tuple(
            sorted(results, key=lambda measurement: (measurement.pid, measurement.name))
        )

    def collect_once(self) -> CollectionResult:
        """Collect one host sample and all process samples without scheduling."""

        return CollectionResult(
            host=self.collect_host(),
            processes=self.collect_processes(),
        )

    # ------------------------------------------------------------------
    # Scheduled lifecycle
    # ------------------------------------------------------------------

    def start(
        self,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        publish: Callable[[HostMeasurement, tuple[ProcessMeasurement, ...]], None] | None = None,
    ) -> None:
        """Start the scheduled collection loop.

        Safe to call repeatedly: an already-running collector is left alone so
        UI reruns cannot start duplicate loops. ``publish``, when supplied, is
        called with every completed collection cycle and is owned by the caller
        (typically wiring ingestion); this module never imports ingestion.
        """

        with self._lock:
            if self.is_running:
                return
            self._stop_event.clear()
            self._state = CollectorState.RUNNING
            self._last_error = None
            self._session = ObservationSession(
                id=str(uuid4()),
                host_id=self._host_id,
                source=self._source,
                start_time=datetime.now(timezone.utc),
                status="active",
            )
            self._publish = publish

        # Prime every CPU counter once and discard: the first psutil sample
        # after a counter is created is meaningless (0.0 / no interval delta).
        try:
            self.collect_once()
        except Exception as exc:
            self._set_state_error(f"collector initialization failed: {exc}")
            return

        self._thread = threading.Thread(
            target=self._loop,
            args=(float(interval_seconds),),
            name="reson-windows-collector",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        """Stop the loop and wait for the worker thread to terminate."""

        with self._lock:
            thread = self._thread
            session = self._session
            if thread is not None:
                self._thread = None
                self._stop_event.set()
            self._publish = None

        if thread is not None:
            thread.join(timeout=timeout)

        with self._lock:
            if session is not None:
                session.end_time = datetime.now(timezone.utc)
                session.status = "stopped"
            self._state = CollectorState.STOPPED

    def _set_state_error(self, message: str) -> None:
        with self._lock:
            self._state = CollectorState.ERROR
            self._last_error = message
            if self._session is not None:
                self._session.status = "error"

    def _loop(self, interval_seconds: float) -> None:
        while not self._stop_event.is_set():
            self._stop_event.wait(interval_seconds)
            if self._stop_event.is_set():
                break

            try:
                result = self.collect_once()
            except Exception as exc:
                self._set_state_error(f"collection cycle failed: {exc}")
                return

            with self._lock:
                self._last_host = result.host
                self._last_processes = result.processes
                if not result.host.ok:
                    self._last_error = result.host.error
                    self._state = CollectorState.ERROR
                    if self._session is not None:
                        self._session.error_count += 1
                else:
                    self._last_error = None
                    self._state = CollectorState.RUNNING
                    if self._session is not None:
                        self._session.sample_count += 1

            publish = self._publish
            if publish is not None:
                try:
                    publish(result.host, result.processes)
                except Exception as exc:
                    # A failing publish callback must not kill the collector;
                    # surface it as an error state instead.
                    self._set_state_error(f"publish failed: {exc}")
                    return

    def __enter__(self) -> "WindowsTelemetryCollector":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()
