"""Real Linux telemetry collection for RESON.

Collects host-level and process resource measurements from the Linux machine
RESON runs on. Built on ``psutil``, which exposes the same Linux performance
counters and process APIs used here, so collection works without elevated
privileges for the supported measurements.

The collector is built on :class:`src.data.collector.BaseCollector`, which owns
the shared start/stop lifecycle, background loop, and health reporting.
Collection is strictly separated from ingestion and analysis: this module
produces structured samples only, never writes to storage, and never starts a
background loop on import.

Linux-specific sources
----------------------
* CPU: ``/proc/stat`` and ``/proc/cpuinfo`` via ``psutil``; the 1-minute load
  average comes from ``/proc/loadavg``.
* Memory and swap: ``/proc/meminfo`` via ``psutil``.
* Disk capacity: ``statvfs`` per mount point; disk I/O counters from
  ``/proc/diskstats``.
* Network: ``/proc/net/dev`` via ``psutil`` (counters only — never packet
  payloads or traffic content).
* Sensors: ``/sys/class/hwmon`` via ``psutil`` (temperatures, fan speeds).

Unsupported or unreadable values are reported as unavailable (``None`` or an
empty tuple) with explicit capability metadata. Nothing is ever fabricated to
make a dashboard look complete.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import psutil

from src.data.collector import (
    BaseCollector,
    BatterySample,
    DiskSample,
    HostSample,
    NetworkSample,
    ProcessSample,
    SensorSample,
    SOURCE_LINUX_LOCAL,
)


class LinuxTelemetryCollector(BaseCollector):
    """Collect real Linux host and process resource measurements.

    Parameters
    ----------
    host_id:
        Stable host reference. Defaults to
        :func:`src.data.host_identity.stable_host_id`, a non-secret hashed
        machine identifier.
    source:
        Telemetry source tag, defaulting to ``"linux_local"``.
    """

    @staticmethod
    def _default_host_id() -> str:
        from src.data.host_identity import stable_host_id

        return stable_host_id()

    def __init__(
        self,
        host_id: str | None = None,
        source: str = SOURCE_LINUX_LOCAL,
    ) -> None:
        super().__init__(host_id=host_id, source=source)

    @staticmethod
    def capabilities() -> dict[str, bool]:
        """Static capability flags for the Linux collector."""
        return {
            "cpu": True,
            "memory": True,
            "swap": True,
            "disk": True,
            "network": True,
            "battery": True,
            "sensors": True,
            "processes": True,
            "event_logs": True,  # journald / syslog, bounded and filtered
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

            swap_percent = None
            swap_used = None
            swap_total = None
            try:
                swap = psutil.swap_memory()
                swap_percent = round(swap.percent, 2)
                swap_used = swap.used
                swap_total = swap.total
            except Exception:
                swap_percent = None

            cpu_count_logical = None
            cpu_count_physical = None
            try:
                cpu_count_logical = psutil.cpu_count(logical=True)
                cpu_count_physical = psutil.cpu_count(logical=False)
            except Exception:
                pass

            cpu_freq = None
            try:
                freq = psutil.cpu_freq()
                if freq is not None:
                    cpu_freq = freq.current
            except Exception:
                cpu_freq = None

            load_avg = None
            try:
                load_avg = os.getloadavg()[0]
            except (OSError, AttributeError):
                load_avg = None

            return HostSample(
                timestamp=timestamp,
                host_id=self._host_id,
                source=self._source,
                cpu_percent=self._clamp_percent(cpu_raw),
                cpu_count_logical=cpu_count_logical,
                cpu_count_physical=cpu_count_physical,
                cpu_freq_mhz=cpu_freq,
                load_average=load_avg,
                memory_percent=self._clamp_percent(memory.percent),
                memory_used_bytes=memory.used,
                memory_total_bytes=memory.total,
                memory_available_bytes=memory.available,
                swap_percent=swap_percent,
                swap_used_bytes=swap_used,
                swap_total_bytes=swap_total,
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
        """Collect process samples, skipping inaccessible processes."""
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
                        cpu_percent=self._clamp_percent(cpu_raw),
                        memory_rss_bytes=int(
                            getattr(memory_info, "rss", 0) or 0
                        ),
                        status=str(status),
                        ok=True,
                    )
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                # Terminated or inaccessible processes are skipped rather than
                # failing the whole collection cycle.
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
            sorted(results, key=lambda m: (m.pid, m.name))
        )

    def collect_disk(self) -> tuple[DiskSample, ...]:
        """Collect disk usage for mounted volumes (best effort).

        Pseudo filesystems are skipped: they report meaningless capacity.
        Inaccessible mounts and removable drives are skipped gracefully.
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
            fstype = (part.fstype or "").lower()
            if fstype in _PSEUDO_FILESYSTEMS:
                continue
            if not os.path.isdir(part.mountpoint):
                # A file bind-mounted onto a path (common in containers) is
                # not a real filesystem with its own capacity.
                continue
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

        Counters are cumulative; callers derive rates from successive samples
        and handle counter resets. No packet payloads are collected.
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
        """Collect battery status.

        A machine with no battery reports ``present=False`` with every other
        field ``None`` — never a fabricated percentage.
        """
        timestamp = self._now()
        try:
            battery = psutil.sensors_battery()
        except Exception:
            battery = None
        if battery is None:
            return BatterySample(
                timestamp=timestamp,
                host_id=self._host_id,
                source=self._source,
                present=False,
            )
        return BatterySample(
            timestamp=timestamp,
            host_id=self._host_id,
            source=self._source,
            present=True,
            percent=battery.percent,
            charging=not battery.power_plugged,
            seconds_remaining=battery.secsleft,
            power_plugged=battery.power_plugged,
        )

    def collect_sensors(self) -> tuple[SensorSample, ...]:
        """Collect hardware sensors exposed through ``/sys/class/hwmon``.

        Temperatures are reported in degrees Celsius and fan speeds in RPM.
        Returns an empty tuple when the platform exposes no sensors.
        """
        samples: list[SensorSample] = []
        timestamp = self._now()

        try:
            temperatures = psutil.sensors_temperatures(fahrenheit=False) or {}
        except Exception:
            temperatures = {}
        for name, entries in temperatures.items():
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

        try:
            fans = psutil.sensors_fans() or {}
        except Exception:
            fans = {}
        for name, entries in fans.items():
            for entry in entries:
                samples.append(
                    SensorSample(
                        timestamp=timestamp,
                        host_id=self._host_id,
                        source=self._source,
                        name=name,
                        value=getattr(entry, "current", None),
                        unit="rpm",
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


#: Pseudo filesystems whose reported capacity is meaningless.
_PSEUDO_FILESYSTEMS = frozenset(
    {
        "autofs",
        "cgroup",
        "cgroup2",
        "configfs",
        "debugfs",
        "devpts",
        "devtmpfs",
        "fusectl",
        "hugetlbfs",
        "mqueue",
        "overlay",
        "proc",
        "pstore",
        "securityfs",
        "squashfs",
        "sysfs",
        "tmpfs",
        "tracefs",
        "nsfs",
        "binfmt_misc",
        "ramfs",
        "efivarfs",
    }
)
