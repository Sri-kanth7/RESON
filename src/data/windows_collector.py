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

The collector is built on :class:`src.data.collector.BaseCollector`, which owns
the shared start/stop lifecycle, background loop, and health reporting. The
Streamlit UI holds a collector in session state but never drives collection
from a page rerun.

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
from datetime import datetime, timezone

import psutil

from src.data.collector import (
    BaseCollector,
    CollectorState,
    CollectionResult,
    DiskSample,
    HostSample,
    NetworkSample,
    ObservationSession,
    ProcessSample,
    SOURCE_WINDOWS_LOCAL,
    BatterySample,
    SensorSample,
    observations_to_metrics,
)

__all__ = [
    "WindowsTelemetryCollector",
    "HostMeasurement",
    "ProcessMeasurement",
    "CollectionResult",
    "CollectorState",
    "ObservationSession",
    "SOURCE_WINDOWS_LOCAL",
    "CPU_PERCENT_MAX",
    "DEFAULT_INTERVAL_SECONDS",
    "IterableProcesses",
    "observations_to_metrics",
]

SOURCE_WINDOWS_LOCAL = SOURCE_WINDOWS_LOCAL
"""Telemetry source tag for real host telemetry collected on this computer."""

CPU_PERCENT_MAX = 100.0
"""Process and host CPU values are clamped to this bound."""

DEFAULT_INTERVAL_SECONDS = 5.0
"""Default collection interval, approximately five seconds."""


# Backward-compatible aliases: HostMeasurement / ProcessMeasurement are the
# same classes as collector.HostSample / collector.ProcessSample.
HostMeasurement = HostSample
ProcessMeasurement = ProcessSample


#: Placeholder type alias kept for readability; ``tuple`` is used below.
IterableProcesses = tuple[ProcessMeasurement, ...]


def _clamp_percent(value: float) -> float | None:
    """Clamp a percentage into ``[0, 100]`` without inventing a missing value."""
    return BaseCollector._clamp_percent(value)


class WindowsTelemetryCollector(BaseCollector):
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

    @staticmethod
    def _default_host_id() -> str:
        return platform.node()

    def __init__(
        self,
        host_id: str | None = None,
        source: str = SOURCE_WINDOWS_LOCAL,
    ) -> None:
        super().__init__(host_id=host_id, source=source)

    @staticmethod
    def capabilities() -> dict[str, bool]:
        """Static capability flags for the Windows collector."""
        return {
            "cpu": True,
            "memory": True,
            "disk": True,
            "network": True,
            "battery": False,  # best-effort; many desktops have no battery
            "sensors": False,  # best-effort; requires drivers/WMI
            "processes": True,
            "event_logs": True,  # bounded, filtered, checkpointed collection
        }

    # ------------------------------------------------------------------
    # One-off collection
    # ------------------------------------------------------------------

    def collect_host(self) -> HostSample:
        """Collect one host-level sample (no scheduling side effects)."""

        timestamp = self._now()
        try:
            cpu_raw = psutil.cpu_percent(interval=None)
            memory = psutil.virtual_memory()
            cpu_count_logical = psutil.cpu_count(logical=True)
            cpu_count_physical = psutil.cpu_count(logical=False)
            cpu_freq = None
            try:
                freq = psutil.cpu_freq()
                if freq is not None:
                    cpu_freq = freq.current
            except Exception:
                cpu_freq = None
            load_avg = None
            if hasattr(psutil, "getloadavg"):
                try:
                    load_avg = psutil.getloadavg()[0]
                except Exception:
                    load_avg = None
            return HostSample(
                timestamp=timestamp,
                host_id=self._host_id,
                source=self._source,
                cpu_percent=_clamp_percent(cpu_raw),
                cpu_count_logical=cpu_count_logical,
                cpu_count_physical=cpu_count_physical,
                cpu_freq_mhz=cpu_freq,
                load_average=load_avg,
                memory_percent=_clamp_percent(memory.percent),
                memory_used_bytes=memory.used,
                memory_total_bytes=memory.total,
                memory_available_bytes=memory.available,
                ok=True,
            )
        except Exception as exc:  # hardware/OS access is not guaranteed
            return HostSample(
                timestamp=timestamp,
                host_id=self._host_id,
                source=self._source,
                ok=False,
                error=f"host collection failed: {exc}",
            )

    def collect_processes(self) -> tuple[ProcessSample, ...]:
        """Collect selected-process samples, skipping inaccessible processes."""

        timestamp = self._now()
        results: list[ProcessSample] = []

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
                    ProcessSample(
                        timestamp=timestamp,
                        host_id=self._host_id,
                        source=self._source,
                        pid=pid,
                        name=name,
                        cpu_percent=_clamp_percent(cpu_raw),
                        memory_rss_bytes=int(
                            getattr(memory_info, "rss", 0) or 0
                        ),
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
                    ProcessSample(
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

    def collect_disk(self) -> tuple[DiskSample, ...]:
        """Collect disk usage for mounted Windows volumes (best effort).

        Inaccessible mounts and removable drives are skipped gracefully.
        Cumulative I/O counters are reported as counters; callers derive rates
        from successive samples and handle counter resets.
        """
        samples: list[DiskSample] = []
        timestamp = self._now()
        try:
            partitions = psutil.disk_partitions(all=False)
        except Exception:
            return ()
        try:
            io_counters = psutil.disk_io_counters(perdisk=True) or {}
        except Exception:
            io_counters = {}
        for part in partitions:
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except Exception:
                # Inaccessible mount or removable drive: skip gracefully.
                continue
            io = io_counters.get(part.device)
            samples.append(
                DiskSample(
                    timestamp=timestamp,
                    host_id=self._host_id,
                    source=self._source,
                    mountpoint=part.mountpoint,
                    device=part.device,
                    fstype=part.fstype,
                    total_bytes=usage.total,
                    used_bytes=usage.used,
                    free_bytes=usage.free,
                    percent=round(usage.percent, 2),
                    read_bytes=getattr(io, "read_bytes", None) if io else None,
                    write_bytes=getattr(io, "write_bytes", None) if io else None,
                    ok=True,
                )
            )
        return tuple(samples)

    def collect_network(self) -> tuple[NetworkSample, ...]:
        """Collect network interface counters (best effort).

        No packet payloads or traffic content are collected. Counters are
        cumulative; callers derive rates from successive samples and handle
        counter resets.
        """
        samples: list[NetworkSample] = []
        timestamp = self._now()
        try:
            counters = psutil.net_io_counters(pernic=True)
        except Exception:
            return ()
        for interface, c in (counters or {}).items():
            samples.append(
                NetworkSample(
                    timestamp=timestamp,
                    host_id=self._host_id,
                    source=self._source,
                    interface=interface,
                    bytes_sent=c.bytes_sent,
                    bytes_recv=c.bytes_recv,
                    packets_sent=c.packets_sent,
                    packets_recv=c.packets_recv,
                    errin=c.errin,
                    errout=c.errout,
                    dropin=c.dropin,
                    dropout=c.dropout,
                    ok=True,
                )
            )
        return tuple(samples)

    def collect_battery(self) -> BatterySample | None:
        """Collect battery status (best effort; None when no battery)."""
        try:
            battery = psutil.sensors_battery()
        except Exception:
            return None
        if battery is None:
            return None
        return BatterySample(
            timestamp=self._now(),
            host_id=self._host_id,
            source=self._source,
            present=True,
            percent=battery.percent,
            charging=battery.power_plugged,
            seconds_remaining=battery.secsleft,
            power_plugged=battery.power_plugged,
        )

    def collect_sensors(self) -> tuple[SensorSample, ...]:
        """Collect hardware sensors (best effort; Windows needs WMI/drivers).

        Returns an empty tuple when no temperature sensors are exposed.
        """
        samples: list[SensorSample] = []
        timestamp = self._now()
        try:
            raw = psutil.sensors_temperatures(fahrenheit=False) or {}
        except Exception:
            return ()
        for name, entries in raw.items():
            for entry in entries:
                samples.append(
                    SensorSample(
                        timestamp=timestamp,
                        host_id=self._host_id,
                        source=self._source,
                        name=name,
                        value=getattr(entry, "current", None),
                        unit="celsius",
                        ok=True,
                    )
                )
        return tuple(samples)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)
