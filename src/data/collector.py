"""Common collector interface and base implementation for RESON telemetry.

This module defines the cross-platform contract every RESON collector
implements, plus a :class:`BaseCollector` that owns the shared start/stop
lifecycle, background collection loop, and health reporting.

Design principles
-----------------
* Collection is strictly separated from ingestion, storage, and analysis. A
  collector produces structured samples only; it never writes to storage and
  never starts a background loop on import.
* The background loop runs in its own daemon thread, not in the Streamlit
  page-rerun path, so UI reruns cannot trigger duplicate collection loops.
* ``start`` is safe to call repeatedly: an already-running collector is left
  alone. ``stop`` waits for the worker thread to terminate.
* Every sample is stamped with the observation time (UTC), the host id, and
  the telemetry source so provenance survives collection end-to-end.
* Unsupported fields return explicit ``None`` / capability metadata rather
  than fabricated values.
"""

from __future__ import annotations

import inspect
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Iterable

from src.data.schemas import (
    Environment,
    Metric,
    SOURCE_HOSTED_COLLECTOR,
    SOURCE_IMPORTED,
    SOURCE_LINUX_LOCAL,
    SOURCE_MACOS_LOCAL,
    SOURCE_SIMULATION,
    SOURCE_UNKNOWN,
    SOURCE_WINDOWS_LOCAL,
    classify_source,
)

# ---------------------------------------------------------------------------
# Collector lifecycle state
# ---------------------------------------------------------------------------


class CollectorState(str, Enum):
    """Observable lifecycle state of a collector."""

    STOPPED = "stopped"
    RUNNING = "running"
    ERROR = "error"


# ---------------------------------------------------------------------------
# Telemetry samples
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HostSample:
    """One host-level resource sample.

    All numeric fields are ``None`` when the underlying OS API cannot provide
    them; a genuine zero is ``0.0`` and is never confused with unavailable.
    """

    timestamp: datetime
    """Observation time in UTC."""
    host_id: str
    """Stable host reference."""
    source: str
    """Telemetry source, e.g. ``"windows_local"``."""
    cpu_percent: float | None = None
    """Host CPU utilization in percent of all logical CPUs, or ``None``."""
    cpu_count_logical: int | None = None
    cpu_count_physical: int | None = None
    cpu_freq_mhz: float | None = None
    load_average: float | None = None
    """1-minute load average on platforms that expose it, or ``None``."""
    memory_percent: float | None = None
    memory_used_bytes: int | None = None
    memory_total_bytes: int | None = None
    memory_available_bytes: int | None = None
    swap_percent: float | None = None
    swap_used_bytes: int | None = None
    swap_total_bytes: int | None = None
    ok: bool = True
    error: str | None = None


@dataclass(frozen=True)
class ProcessSample:
    """One process resource sample.

    Command-line arguments, environment variables, and other sensitive
    process contents are intentionally not collected.
    """

    timestamp: datetime
    host_id: str
    source: str
    pid: int
    name: str
    cpu_percent: float | None = None
    memory_rss_bytes: int | None = None
    status: str | None = None
    ok: bool = True
    error: str | None = None


@dataclass(frozen=True)
class DiskSample:
    """One mounted filesystem / volume sample."""

    timestamp: datetime
    host_id: str
    source: str
    mountpoint: str
    device: str | None = None
    fstype: str | None = None
    total_bytes: int | None = None
    used_bytes: int | None = None
    free_bytes: int | None = None
    percent: float | None = None
    read_bytes: int | None = None
    write_bytes: int | None = None
    ok: bool = True
    error: str | None = None


@dataclass(frozen=True)
class NetworkSample:
    """One network interface sample.

    Cumulative counters are exposed as counters; callers derive rates from
    successive samples and must handle counter resets. No packet payloads or
    traffic content are ever collected.
    """

    timestamp: datetime
    host_id: str
    source: str
    interface: str
    bytes_sent: int | None = None
    bytes_recv: int | None = None
    packets_sent: int | None = None
    packets_recv: int | None = None
    errin: int | None = None
    errout: int | None = None
    dropin: int | None = None
    dropout: int | None = None
    ok: bool = True
    error: str | None = None


@dataclass(frozen=True)
class BatterySample:
    """One battery / power sample.

    A desktop with no battery reports ``present=False`` with every other
    field ``None``; it never reports a fabricated percentage.
    """

    timestamp: datetime
    host_id: str
    source: str
    present: bool = False
    percent: float | None = None
    charging: bool | None = None
    seconds_remaining: int | None = None
    power_plugged: bool | None = None


@dataclass(frozen=True)
class SensorSample:
    """One hardware sensor reading (temperature, fan speed, ...)."""

    timestamp: datetime
    host_id: str
    source: str
    name: str
    """Sensor label exposed by the OS (for example ``"coretemp``)."""
    value: float | None = None
    unit: str | None = None
    ok: bool = True
    error: str | None = None


@dataclass(frozen=True)
class CollectionResult:
    """One collection cycle: host sample plus the other collected samples."""

    host: HostSample
    processes: tuple[ProcessSample, ...] = ()
    disk: tuple[DiskSample, ...] = ()
    network: tuple[NetworkSample, ...] = ()
    battery: BatterySample | None = None
    sensors: tuple[SensorSample, ...] = ()


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


@dataclass(frozen=True)
class SamplingPolicy:
    """Controls how much of each collection cycle is published.

    A full cycle on a typical machine yields well over a hundred records
    (every process contributes two metrics, every mount contributes six).
    Publishing all of them every few seconds would grow the database without
    bound and slow every dashboard query, so the policy separates the metrics
    that change quickly from the ones that barely move.

    Attributes
    ----------
    process_sample_every:
        Publish process metrics on every Nth cycle. ``1`` publishes them every
        cycle. Process counts change quickly and are the bulk of the volume.
    slow_sample_every:
        Publish slow-changing metrics (disk capacity and I/O, network
        counters, battery, sensors) on every Nth cycle. ``1`` publishes them
        every cycle. These are cumulative counters or slowly varying
        capacities, so a lower frequency loses no meaningful information.
    max_processes:
        Maximum number of processes published per cycle, ranked by CPU
        utilization. The dashboard states that this is a top-N view.
    """

    process_sample_every: int = 5
    slow_sample_every: int = 30
    max_processes: int = 15

    def validate(self) -> "SamplingPolicy":
        """Validate the policy, raising ``ValueError`` when invalid."""
        if self.process_sample_every < 1:
            raise ValueError("process_sample_every must be at least 1")
        if self.slow_sample_every < 1:
            raise ValueError("slow_sample_every must be at least 1")
        if self.max_processes < 0:
            raise ValueError("max_processes must not be negative")
        return self


def apply_sampling_policy(
    result: CollectionResult,
    cycle: int,
    policy: SamplingPolicy,
) -> CollectionResult:
    """Return the part of ``result`` that should be published on this cycle.

    Host metrics are always published. Process and slow-changing metrics are
    published according to the policy's cadence, and the process list is
    capped to the top ``max_processes`` by CPU utilization.
    """
    processes: tuple[ProcessSample, ...] = result.processes
    if (
        cycle > 1
        and policy.process_sample_every > 1
        and cycle % policy.process_sample_every
    ):
        processes = ()
    elif 0 < policy.max_processes < len(processes):
        processes = tuple(
            sorted(
                processes,
                key=lambda sample: -(sample.cpu_percent or 0.0),
            )[: policy.max_processes]
        )

    publish_slow = (
        cycle <= 1
        or policy.slow_sample_every <= 1
        or cycle % policy.slow_sample_every == 0
    )
    return CollectionResult(
        host=result.host,
        processes=processes,
        disk=result.disk if publish_slow else (),
        network=result.network if publish_slow else (),
        battery=result.battery if publish_slow else None,
        sensors=result.sensors if publish_slow else (),
    )


@dataclass(frozen=True)
class CollectorHealth:
    """Reported collector health, derived from actual state not UI flags."""

    running: bool
    state: str
    last_error: str | None
    last_successful_sample: datetime | None
    sample_count: int
    error_count: int
    capabilities: dict[str, bool]
    source: str
    host_id: str
    policy: SamplingPolicy | None = None


# ---------------------------------------------------------------------------
# Sample -> canonical telemetry conversion
# ---------------------------------------------------------------------------


def observations_to_metrics(
    host: HostSample | None,
    processes: Iterable[ProcessSample] = (),
    disk: Iterable[DiskSample] = (),
    network: Iterable[NetworkSample] = (),
    battery: BatterySample | None = None,
    sensors: Iterable[SensorSample] = (),
) -> list[Metric]:
    """Convert collector output into canonical :class:`~src.data.schemas.Metric` records.

    Pure data shaping: no ingestion, no storage, no analysis. Unavailable
    measurements (``ok is False`` or a ``None`` value) are omitted rather than
    recorded as zero, so a collection failure is never disguised as a
    normal zero-valued observation.

    Every produced metric carries the originating ``source`` and ``host_id``,
    so provenance survives collection into storage, aggregation, incident
    generation, and export.

    Per-subsystem samples are scoped through the ``service`` field using the
    same convention the process metrics use (``<host_id>:<kind>:<name>``),
    for example ``host:disk:/var`` and ``host:net:eth0``. Cumulative counters
    (disk I/O, network bytes) are stored as counters; rates are derived from
    successive samples, never stored as if they were instantaneous values.
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
        if host.swap_percent is not None:
            records.append(
                Metric(
                    **base,
                    metric_name="swap_percent",
                    value=host.swap_percent,
                    unit="percent",
                )
            )
        if host.swap_used_bytes is not None:
            records.append(
                Metric(
                    **base,
                    metric_name="swap_used_bytes",
                    value=float(host.swap_used_bytes),
                    unit="bytes",
                )
            )
        if host.swap_total_bytes is not None:
            records.append(
                Metric(
                    **base,
                    metric_name="swap_total_bytes",
                    value=float(host.swap_total_bytes),
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

    for volume in disk:
        if not volume.ok:
            continue
        base = dict(
            timestamp=volume.timestamp,
            service=f"{volume.host_id}:disk:{volume.mountpoint}",
            environment=Environment.PRODUCTION,
            source=volume.source,
            host_id=volume.host_id,
        )
        for name, value, unit in (
            ("disk_total_bytes", volume.total_bytes, "bytes"),
            ("disk_used_bytes", volume.used_bytes, "bytes"),
            ("disk_free_bytes", volume.free_bytes, "bytes"),
            ("disk_percent", volume.percent, "percent"),
            ("disk_read_bytes", volume.read_bytes, "bytes"),
            ("disk_write_bytes", volume.write_bytes, "bytes"),
        ):
            if value is None:
                continue
            records.append(
                Metric(
                    **base,
                    metric_name=name,
                    value=float(value),
                    unit=unit,
                )
            )

    for interface in network:
        if not interface.ok:
            continue
        base = dict(
            timestamp=interface.timestamp,
            service=f"{interface.host_id}:net:{interface.interface}",
            environment=Environment.PRODUCTION,
            source=interface.source,
            host_id=interface.host_id,
        )
        for name, value in (
            ("network_bytes_sent", interface.bytes_sent),
            ("network_bytes_recv", interface.bytes_recv),
            ("network_packets_sent", interface.packets_sent),
            ("network_packets_recv", interface.packets_recv),
            ("network_errin", interface.errin),
            ("network_errout", interface.errout),
            ("network_dropin", interface.dropin),
            ("network_dropout", interface.dropout),
        ):
            if value is None:
                continue
            records.append(
                Metric(
                    **base,
                    metric_name=name,
                    value=float(value),
                    unit="bytes" if "bytes" in name else "count",
                )
            )

    if battery is not None and battery.present:
        base = dict(
            timestamp=battery.timestamp,
            service=battery.host_id,
            environment=Environment.PRODUCTION,
            source=battery.source,
            host_id=battery.host_id,
        )
        if battery.percent is not None:
            records.append(
                Metric(
                    **base,
                    metric_name="battery_percent",
                    value=float(battery.percent),
                    unit="percent",
                )
            )
        if battery.seconds_remaining is not None:
            records.append(
                Metric(
                    **base,
                    metric_name="battery_seconds_remaining",
                    value=float(battery.seconds_remaining),
                    unit="seconds",
                )
            )

    for sensor in sensors:
        if not sensor.ok or sensor.value is None:
            continue
        records.append(
            Metric(
                timestamp=sensor.timestamp,
                service=f"{sensor.host_id}:sensor:{sensor.name}",
                environment=Environment.PRODUCTION,
                source=sensor.source,
                host_id=sensor.host_id,
                metric_name="sensor_value",
                value=float(sensor.value),
                unit=sensor.unit or "unknown",
            )
        )

    return records


def collection_to_metrics(result: CollectionResult) -> list[Metric]:
    """Convert a complete collection result into canonical metric records."""
    return observations_to_metrics(
        result.host,
        result.processes,
        result.disk,
        result.network,
        result.battery,
        result.sensors,
    )


# ---------------------------------------------------------------------------
# Collector interface
# ---------------------------------------------------------------------------


PublishCallback = Callable[..., None]
"""Publish callback invoked with every completed collection cycle.

The callback may accept either the full :class:`CollectionResult` (one
positional argument) or the legacy ``(host, processes)`` pair (two positional
arguments). The arity is resolved once, by inspecting the callable, so a
genuine error inside the callback is never mistaken for a signature
mismatch.
"""


def _publish_arity(callback: PublishCallback) -> int:
    """Return the number of positional parameters ``callback`` accepts."""
    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError):
        return 1
    positional = 0
    for parameter in signature.parameters.values():
        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            return 1
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            positional += 1
    return max(1, positional)


def invoke_publish(callback: PublishCallback, result: CollectionResult) -> None:
    """Invoke a publish callback with the collected result.

    Supports both the single-argument ``(result)`` form and the legacy
    two-argument ``(host, processes)`` form.
    """
    if _publish_arity(callback) >= 2:
        callback(result.host, result.processes)
    else:
        callback(result)


class Collector(ABC):
    """Abstract interface for RESON telemetry collectors."""

    @property
    @abstractmethod
    def host_id(self) -> str:
        """Stable host reference."""

    @property
    @abstractmethod
    def source(self) -> str:
        """Telemetry source tag."""

    @property
    @abstractmethod
    def state(self) -> CollectorState:
        """Observable lifecycle state."""

    @property
    def is_running(self) -> bool:
        """Whether the background collection thread is alive."""
        return self.state == CollectorState.RUNNING

    @property
    @abstractmethod
    def session(self) -> ObservationSession | None:
        """Current observation session, if any."""

    @property
    @abstractmethod
    def last_error(self) -> str | None:
        """Most recent collection/publish error, if any."""

    @property
    def last_host_sample(self) -> HostSample | None:
        """Most recent host sample (default: unavailable)."""
        return None

    @property
    def last_process_samples(self) -> tuple[ProcessSample, ...]:
        """Most recent process samples (default: none)."""
        return ()

    @property
    def last_host_measurement(self) -> HostSample | None:
        """Alias of :attr:`last_host_sample` (original RESON API name)."""
        return self.last_host_sample

    @property
    def last_process_measurements(self) -> tuple[ProcessSample, ...]:
        """Alias of :attr:`last_process_samples` (original RESON API name)."""
        return self.last_process_samples

    @abstractmethod
    def collect_host(self) -> HostSample:
        """Collect one host-level sample (no scheduling side effects)."""

    @abstractmethod
    def collect_processes(self) -> tuple[ProcessSample, ...]:
        """Collect process samples, skipping inaccessible processes."""

    def collect_disk(self) -> tuple[DiskSample, ...]:
        """Collect disk samples for mounted volumes (default: none)."""
        return ()

    def collect_network(self) -> tuple[NetworkSample, ...]:
        """Collect network interface samples (default: none)."""
        return ()

    def collect_battery(self) -> BatterySample | None:
        """Collect one battery sample, or ``None`` when no battery."""
        return None

    def collect_sensors(self) -> tuple[SensorSample, ...]:
        """Collect hardware sensor samples (default: none)."""
        return ()

    def collect_once(self) -> CollectionResult:
        """Collect one full sample set without scheduling.

        Platform collectors that do not support a category simply return an
        empty result for it; nothing is fabricated.
        """
        return CollectionResult(
            host=self.collect_host(),
            processes=self.collect_processes(),
            disk=self.collect_disk(),
            network=self.collect_network(),
            battery=self.collect_battery(),
            sensors=self.collect_sensors(),
        )

    @abstractmethod
    def start(
        self,
        interval_seconds: float = 5.0,
        publish: PublishCallback | None = None,
    ) -> None:
        """Start the scheduled collection loop."""

    @abstractmethod
    def stop(self, timeout: float = 10.0) -> None:
        """Stop the loop and wait for the worker thread to terminate."""

    @abstractmethod
    def health(self) -> CollectorHealth:
        """Return a health report derived from actual collector state."""

    def __enter__(self) -> "Collector":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()


# ---------------------------------------------------------------------------
# Base collector: shared lifecycle and loop
# ---------------------------------------------------------------------------


class BaseCollector(Collector):
    """Shared start/stop/loop behaviour for platform collectors.

    Subclasses implement :meth:`collect_host` and :meth:`collect_processes`
    (and optionally the disk/network/battery/sensor methods). The background
    loop, state machine, and health report are shared.
    """

    def __init__(
        self,
        host_id: str | None = None,
        source: str = SOURCE_UNKNOWN,
        policy: SamplingPolicy | None = None,
    ) -> None:
        self._host_id = host_id or self._default_host_id()
        self._source = classify_source(source)
        self._policy = (policy or SamplingPolicy()).validate()
        self._state = CollectorState.STOPPED
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._session: ObservationSession | None = None
        self._last_host: HostSample | None = None
        self._last_processes: tuple[ProcessSample, ...] = ()
        self._last_error: str | None = None
        self._publish: PublishCallback | None = None
        self._sample_count = 0
        self._error_count = 0
        self._last_success: datetime | None = None
        self._cycle = 0

    # ------------------------------------------------------------------
    # Platform identity
    # ------------------------------------------------------------------

    @staticmethod
    @abstractmethod
    def _default_host_id() -> str:
        """Return the platform default stable host reference."""

    @staticmethod
    @abstractmethod
    def capabilities() -> dict[str, bool]:
        """Static capability flags for this platform collector."""

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
        return (
            self._thread is not None and self._thread.is_alive()
        )

    @property
    def session(self) -> ObservationSession | None:
        with self._lock:
            return self._session

    @property
    def last_error(self) -> str | None:
        with self._lock:
            return self._last_error

    @property
    def last_host_sample(self) -> HostSample | None:
        with self._lock:
            return self._last_host

    @property
    def last_process_samples(self) -> tuple[ProcessSample, ...]:
        with self._lock:
            return self._last_processes

    # ------------------------------------------------------------------
    # One-off collection
    # ------------------------------------------------------------------

    def collect_once(self) -> CollectionResult:
        result = super().collect_once()
        return result

    # ------------------------------------------------------------------
    # Scheduled lifecycle
    # ------------------------------------------------------------------

    def start(
        self,
        interval_seconds: float = 5.0,
        publish: PublishCallback | None = None,
    ) -> None:
        with self._lock:
            if self.is_running:
                return
            self._stop_event.clear()
            self._state = CollectorState.RUNNING
            self._last_error = None
            self._session = ObservationSession(
                id=self._new_session_id(),
                host_id=self._host_id,
                source=self._source,
                start_time=datetime.now(timezone.utc),
                status="active",
            )
            self._publish = publish

        # Prime every counter once and discard: the first psutil sample is
        # meaningless (no interval delta).
        try:
            self.collect_once()
        except Exception as exc:
            self._set_state_error(f"collector initialization failed: {exc}")
            return

        self._thread = threading.Thread(
            target=self._loop,
            args=(float(interval_seconds),),
            name="reson-collector",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
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
            self._error_count += 1
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
                self._cycle += 1
                cycle = self._cycle
                if not result.host.ok:
                    self._last_error = result.host.error
                    self._state = CollectorState.ERROR
                    self._error_count += 1
                    if self._session is not None:
                        self._session.error_count += 1
                else:
                    self._last_error = None
                    self._state = CollectorState.RUNNING
                    self._sample_count += 1
                    self._last_success = datetime.now(timezone.utc)
                    if self._session is not None:
                        self._session.sample_count += 1

            publish = self._publish
            if publish is not None:
                try:
                    invoke_publish(
                        publish,
                        apply_sampling_policy(result, cycle, self._policy),
                    )
                except Exception as exc:
                    self._set_state_error(f"publish failed: {exc}")
                    return

    def health(self) -> CollectorHealth:
        with self._lock:
            return CollectorHealth(
                running=self.is_running,
                state=self._state.value,
                last_error=self._last_error,
                last_successful_sample=self._last_success,
                sample_count=self._sample_count,
                error_count=self._error_count,
                capabilities=dict(self.capabilities()),
                source=self._source,
                host_id=self._host_id,
                policy=self._policy,
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _new_session_id() -> str:
        from uuid import uuid4

        return str(uuid4())

    @staticmethod
    def _clamp_percent(value: float) -> float | None:
        if value is None:
            return None
        return min(100.0, max(0.0, float(value)))


# Backwards-compatible aliases for the original Windows module layout.
HostMeasurement = HostSample
ProcessMeasurement = ProcessSample

__all__ = [
    "CollectorState",
    "HostSample",
    "HostMeasurement",
    "ProcessSample",
    "ProcessMeasurement",
    "DiskSample",
    "NetworkSample",
    "BatterySample",
    "SensorSample",
    "CollectionResult",
    "ObservationSession",
    "CollectorHealth",
    "Collector",
    "BaseCollector",
    "PublishCallback",
    "SOURCE_WINDOWS_LOCAL",
    "SOURCE_LINUX_LOCAL",
    "SOURCE_MACOS_LOCAL",
    "SOURCE_HOSTED_COLLECTOR",
    "SOURCE_SIMULATION",
    "SOURCE_IMPORTED",
    "SOURCE_UNKNOWN",
    "classify_source",
]
