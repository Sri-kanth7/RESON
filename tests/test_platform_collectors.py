"""Tests for the Linux and macOS platform collectors.

The platform-specific collection logic is exercised with mocked ``psutil``
calls so the tests are deterministic and run on any developer machine. A
separate live smoke test exercises the collector that matches the *running*
platform; that test is the platform-validation evidence recorded in the
implementation plan.
"""

from __future__ import annotations

from unittest import mock

import psutil

from src.data.collector import CollectorState
from src.data.linux_collector import LinuxTelemetryCollector
from src.data.macos_collector import MacOSTelemetryCollector


def _memory(percent=50.0, total=8_000_000_000, used=4_000_000_000, available=4_000_000_000):
    mem = mock.Mock()
    mem.percent = percent
    mem.total = total
    mem.used = used
    mem.available = available
    return mem


def _swap(percent=0.0, used=0, total=0):
    swap = mock.Mock()
    swap.percent = percent
    swap.used = used
    swap.total = total
    return swap


def _process(pid, name, cpu_percent=1.0, rss=4096, status="running"):
    proc = mock.Mock()
    proc.info = {"pid": pid, "name": name}
    proc.cpu_percent.return_value = cpu_percent
    proc.memory_info.return_value = mock.Mock(rss=rss)
    proc.status.return_value = status
    return proc


class _ProcessIter:
    def __init__(self, processes):
        self._processes = processes

    def __iter__(self):
        return iter(self._processes)


# ---------------------------------------------------------------------------
# Linux collector
# ---------------------------------------------------------------------------


def test_linux_collector_uses_the_linux_source():
    assert LinuxTelemetryCollector().source == "linux_local"


def test_linux_collector_capabilities_are_explicit():
    caps = LinuxTelemetryCollector.capabilities()
    assert caps["cpu"] is True
    assert caps["memory"] is True
    assert caps["disk"] is True
    assert caps["network"] is True
    assert caps["processes"] is True
    assert "battery" in caps
    assert "sensors" in caps


def test_linux_host_sample_collects_real_values():
    collector = LinuxTelemetryCollector(host_id="linux-1")
    with mock.patch(
        "src.data.linux_collector.psutil.cpu_percent", return_value=42.5
    ), mock.patch(
        "src.data.linux_collector.psutil.virtual_memory",
        return_value=_memory(percent=61.0),
    ), mock.patch(
        "src.data.linux_collector.psutil.swap_memory",
        return_value=_swap(percent=10.0, used=100, total=1000),
    ), mock.patch(
        "src.data.linux_collector.psutil.cpu_count", return_value=8
    ), mock.patch(
        "src.data.linux_collector.psutil.cpu_freq", return_value=None
    ), mock.patch(
        "src.data.linux_collector.os.getloadavg", return_value=(0.5, 0.4, 0.3)
    ):
        sample = collector.collect_host()

    assert sample.ok is True
    assert sample.source == "linux_local"
    assert sample.host_id == "linux-1"
    assert sample.cpu_percent == 42.5
    assert sample.memory_percent == 61.0
    assert sample.memory_total_bytes == 8_000_000_000
    assert sample.swap_percent == 10.0
    assert sample.cpu_count_logical == 8
    assert sample.load_average == 0.5
    assert sample.timestamp.tzinfo is not None


def test_linux_host_sample_reports_failure_without_fabricating_values():
    collector = LinuxTelemetryCollector(host_id="linux-1")
    with mock.patch(
        "src.data.linux_collector.psutil.cpu_percent",
        side_effect=RuntimeError("kernel unavailable"),
    ):
        sample = collector.collect_host()

    assert sample.ok is False
    assert sample.error is not None
    assert sample.cpu_percent is None
    assert sample.memory_percent is None


def test_linux_cpu_percent_is_clamped_to_bounds():
    collector = LinuxTelemetryCollector(host_id="linux-1")
    with mock.patch(
        "src.data.linux_collector.psutil.cpu_percent", return_value=140.0
    ), mock.patch(
        "src.data.linux_collector.psutil.virtual_memory",
        return_value=_memory(),
    ):
        assert collector.collect_host().cpu_percent == 100.0

    with mock.patch(
        "src.data.linux_collector.psutil.cpu_percent", return_value=-5.0
    ), mock.patch(
        "src.data.linux_collector.psutil.virtual_memory",
        return_value=_memory(),
    ):
        assert collector.collect_host().cpu_percent == 0.0


def test_linux_process_collection_skips_inaccessible_processes():
    collector = LinuxTelemetryCollector(host_id="linux-1")
    denied = _process(2, "denied")
    denied.cpu_percent.side_effect = psutil.AccessDenied()
    with mock.patch(
        "src.data.linux_collector.psutil.process_iter",
        return_value=_ProcessIter([_process(1, "ok"), denied]),
    ):
        processes = collector.collect_processes()

    assert len(processes) == 1
    assert processes[0].pid == 1
    assert processes[0].name == "ok"


def test_linux_disk_collection_skips_pseudo_filesystems():
    collector = LinuxTelemetryCollector(host_id="linux-1")
    usage = mock.Mock(total=100, used=50, free=50, percent=50.0)
    with mock.patch(
        "src.data.linux_collector.psutil.disk_partitions",
        return_value=[
            mock.Mock(device="/dev/sda1", mountpoint="/", fstype="ext4"),
            mock.Mock(device="proc", mountpoint="/proc", fstype="proc"),
            mock.Mock(device="tmpfs", mountpoint="/run", fstype="tmpfs"),
        ],
    ), mock.patch(
        "src.data.linux_collector.psutil.disk_usage", return_value=usage
    ), mock.patch(
        "src.data.linux_collector.psutil.disk_io_counters", return_value={}
    ), mock.patch(
        "src.data.linux_collector.os.path.isdir", return_value=True
    ):
        disks = collector.collect_disk()

    assert len(disks) == 1
    assert disks[0].mountpoint == "/"
    assert disks[0].total_bytes == 100
    assert disks[0].percent == 50.0


def test_linux_disk_collection_skips_inaccessible_mounts():
    collector = LinuxTelemetryCollector(host_id="linux-1")
    with mock.patch(
        "src.data.linux_collector.psutil.disk_partitions",
        return_value=[
            mock.Mock(device="/dev/sr0", mountpoint="/media/cd", fstype="iso9660"),
        ],
    ), mock.patch(
        "src.data.linux_collector.psutil.disk_usage",
        side_effect=PermissionError("denied"),
    ), mock.patch(
        "src.data.linux_collector.psutil.disk_io_counters", return_value={}
    ), mock.patch(
        "src.data.linux_collector.os.path.isdir", return_value=True
    ):
        assert collector.collect_disk() == ()


def test_linux_battery_reports_absent_rather_than_a_fake_percentage():
    collector = LinuxTelemetryCollector(host_id="linux-1")
    with mock.patch(
        "src.data.linux_collector.psutil.sensors_battery", return_value=None
    ):
        battery = collector.collect_battery()

    assert battery is not None
    assert battery.present is False
    assert battery.percent is None
    assert battery.seconds_remaining is None


def test_linux_battery_reports_real_values_when_present():
    collector = LinuxTelemetryCollector(host_id="linux-1")
    raw = mock.Mock(percent=77.0, power_plugged=False, secsleft=3600)
    with mock.patch(
        "src.data.linux_collector.psutil.sensors_battery", return_value=raw
    ):
        battery = collector.collect_battery()

    assert battery.present is True
    assert battery.percent == 77.0
    assert battery.charging is True
    assert battery.seconds_remaining == 3600


def test_linux_network_collection_reports_counters_not_rates():
    collector = LinuxTelemetryCollector(host_id="linux-1")
    counters = {
        "eth0": mock.Mock(
            bytes_sent=1000,
            bytes_recv=2000,
            packets_sent=10,
            packets_recv=20,
            errin=1,
            errout=2,
            dropin=3,
            dropout=4,
        )
    }
    with mock.patch(
        "src.data.linux_collector.psutil.net_io_counters",
        return_value=counters,
    ):
        samples = collector.collect_network()

    assert len(samples) == 1
    assert samples[0].interface == "eth0"
    assert samples[0].bytes_sent == 1000
    assert samples[0].errin == 1
    assert samples[0].dropin == 3


# ---------------------------------------------------------------------------
# macOS collector
# ---------------------------------------------------------------------------


def test_macos_collector_uses_the_macos_source():
    assert MacOSTelemetryCollector().source == "macos_local"


def test_macos_capabilities_report_sensors_as_unsupported():
    """macOS hides temperature sensors from unprivileged processes."""
    caps = MacOSTelemetryCollector.capabilities()
    assert caps["sensors"] is False
    assert caps["battery"] is True


def test_macos_sensors_are_never_fabricated():
    collector = MacOSTelemetryCollector(host_id="mac-1")
    assert collector.collect_sensors() == ()


def test_macos_host_sample_collects_real_values():
    collector = MacOSTelemetryCollector(host_id="mac-1")
    with mock.patch(
        "src.data.macos_collector.psutil.cpu_percent", return_value=17.5
    ), mock.patch(
        "src.data.macos_collector.psutil.virtual_memory",
        return_value=_memory(percent=44.0),
    ), mock.patch(
        "src.data.macos_collector.psutil.swap_memory",
        return_value=_swap(),
    ), mock.patch(
        "src.data.macos_collector.psutil.cpu_count", return_value=10
    ), mock.patch(
        "src.data.macos_collector.psutil.cpu_freq", return_value=None
    ), mock.patch(
        "src.data.macos_collector.os.getloadavg", return_value=(2.0, 1.0, 0.5)
    ):
        sample = collector.collect_host()

    assert sample.ok is True
    assert sample.source == "macos_local"
    assert sample.cpu_percent == 17.5
    assert sample.memory_percent == 44.0
    assert sample.load_average == 2.0


def test_macos_battery_reports_absent_rather_than_a_fake_percentage():
    collector = MacOSTelemetryCollector(host_id="mac-1")
    with mock.patch(
        "src.data.macos_collector.psutil.sensors_battery", return_value=None
    ):
        battery = collector.collect_battery()

    assert battery is not None
    assert battery.present is False
    assert battery.percent is None


# ---------------------------------------------------------------------------
# Live platform smoke test (platform-validation evidence)
# ---------------------------------------------------------------------------


def test_collector_for_the_running_platform_collects_real_data():
    """The collector matching the running OS returns a real host sample.

    On Linux this is genuine platform validation of the Linux collector; on
    Windows/macOS it validates that platform's collector instead.
    """
    from src.data.collector_factory import create_collector

    collector = create_collector()
    sample = collector.collect_host()

    assert sample.ok is True, sample.error
    assert sample.source in ("windows_local", "linux_local", "macos_local")
    assert sample.host_id
    assert sample.timestamp.tzinfo is not None
    assert 0.0 <= sample.cpu_percent <= 100.0
    assert 0.0 <= sample.memory_percent <= 100.0
    assert sample.memory_total_bytes and sample.memory_total_bytes > 0

    processes = collector.collect_processes()
    assert processes
    assert all(p.host_id == sample.host_id for p in processes)
    assert all(p.source == sample.source for p in processes)


def test_collector_for_the_running_platform_reports_its_capabilities():
    from src.data.collector_factory import create_collector

    collector = create_collector()
    health = collector.health()
    assert health.capabilities
    assert health.capabilities.get("cpu") is True
    assert health.host_id == collector.host_id
    assert health.source == collector.source
    assert health.state == CollectorState.STOPPED.value
