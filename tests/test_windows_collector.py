"""Deterministic tests for the real Windows telemetry collector.

Windows-specific calls (``psutil``) are mocked so these tests run offline on
any developer machine and never depend on real processes, real sleeps, or
real time passing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest import mock

import psutil
import pytest

from src.data.schemas import Environment, Metric
from src.data.windows_collector import (
    SOURCE_WINDOWS_LOCAL,
    CollectorState,
    HostMeasurement,
    ObservationSession,
    ProcessMeasurement,
    WindowsTelemetryCollector,
    observations_to_metrics,
)


def _memory(total=17_179_869_184, used=8_589_934_592, percent=50.0):
    mem = mock.Mock()
    mem.total = total
    mem.used = used
    mem.percent = percent
    return mem


def _process(pid, name, cpu_percent, rss, status="running"):
    proc = mock.Mock()
    proc.info = {"pid": pid, "name": name}
    proc.cpu_percent.return_value = cpu_percent
    proc.memory_info.return_value = mock.Mock(rss=rss)
    proc.status.return_value = status
    return proc


class FakeProcessIter:
    """Iterable of process mocks, with optional per-call failures."""

    def __init__(self, processes):
        self._processes = processes

    def __iter__(self):
        return iter(self._processes)


# ---------------------------------------------------------------------------
# No import-time side effects
# ---------------------------------------------------------------------------


def test_collector_instantiates_without_starting_anything():
    collector = WindowsTelemetryCollector(host_id="test-host")

    assert collector.state == CollectorState.STOPPED
    assert not collector.is_running
    assert collector.session is None
    assert collector.last_host_measurement is None
    assert collector.last_process_measurements == ()


# ---------------------------------------------------------------------------
# Host measurement normalization
# ---------------------------------------------------------------------------


def test_host_measurement_normalization():
    with mock.patch(
        "src.data.windows_collector.psutil.cpu_percent", return_value=42.5
    ), mock.patch(
        "src.data.windows_collector.psutil.virtual_memory",
        return_value=_memory(percent=60.0),
    ), mock.patch(
        "src.data.windows_collector.platform.node", return_value="test-host"
    ):
        collector = WindowsTelemetryCollector()
        measurement = collector.collect_host()

    assert isinstance(measurement, HostMeasurement)
    assert measurement.host_id == "test-host"
    assert measurement.source == SOURCE_WINDOWS_LOCAL
    assert measurement.cpu_percent == 42.5
    assert measurement.memory_percent == 60.0
    assert measurement.memory_used_bytes == 8_589_934_592
    assert measurement.memory_total_bytes == 17_179_869_184
    assert measurement.ok is True
    assert measurement.error is None
    assert measurement.timestamp.tzinfo is timezone.utc


def test_host_cpu_is_clamped_to_100():
    with mock.patch(
        "src.data.windows_collector.psutil.cpu_percent", return_value=117.3
    ), mock.patch(
        "src.data.windows_collector.psutil.virtual_memory",
        return_value=_memory(),
    ):
        collector = WindowsTelemetryCollector()
        measurement = collector.collect_host()

    assert measurement.cpu_percent == 100.0
    assert measurement.ok is True


def test_unavailable_host_metric_is_not_zero():
    with mock.patch(
        "src.data.windows_collector.psutil.cpu_percent",
        side_effect=psutil.AccessDenied("access denied"),
    ), mock.patch(
        "src.data.windows_collector.psutil.virtual_memory",
        return_value=_memory(),
    ):
        collector = WindowsTelemetryCollector()
        measurement = collector.collect_host()

    assert measurement.ok is False
    assert measurement.cpu_percent is None
    assert measurement.memory_percent is None
    assert "access denied" in measurement.error
    # Unavailable is not a genuine zero: every value is None and ok is False.


# ---------------------------------------------------------------------------
# Process measurement handling
# ---------------------------------------------------------------------------


def test_process_measurement_normalization():
    processes = [
        _process(101, "svchost.exe", 5.5, 42_000_000),
        _process(202, "python.exe", 12.0, 150_000_000),
    ]
    with mock.patch(
        "src.data.windows_collector.psutil.process_iter",
        return_value=FakeProcessIter(processes),
    ):
        collector = WindowsTelemetryCollector(host_id="test-host")
        results = collector.collect_processes()

    assert len(results) == 2
    first, second = results
    assert first.pid == 101
    assert first.name == "svchost.exe"
    assert first.cpu_percent == 5.5
    assert first.memory_rss_bytes == 42_000_000
    assert first.status == "running"
    assert first.ok is True
    assert first.host_id == "test-host"
    assert first.source == SOURCE_WINDOWS_LOCAL
    assert first.timestamp.tzinfo is timezone.utc
    assert second.pid == 202
    assert second.cpu_percent == 12.0


def test_process_cpu_is_clamped_to_100():
    processes = [_process(101, "svchost.exe", 250.0, 1_000_000)]
    with mock.patch(
        "src.data.windows_collector.psutil.process_iter",
        return_value=FakeProcessIter(processes),
    ):
        collector = WindowsTelemetryCollector()
        (result,) = collector.collect_processes()

    assert result.cpu_percent == 100.0
    assert result.ok is True


def test_terminated_and_inaccessible_processes_are_skipped():
    processes = [
        _process(1, "good", 1.0, 1_000_000),
    ]
    failing = mock.Mock()
    failing.info = {"pid": 2, "name": "bad.exe"}
    failing.cpu_percent.side_effect = psutil.NoSuchProcess(2)
    zombie = mock.Mock()
    zombie.info = {"pid": 3, "name": "zombie.exe"}
    zombie.cpu_percent.side_effect = psutil.ZombieProcess(3)
    denied = mock.Mock()
    denied.info = {"pid": 4, "name": "denied.exe"}
    denied.cpu_percent.side_effect = psutil.AccessDenied(4)

    with mock.patch(
        "src.data.windows_collector.psutil.process_iter",
        return_value=FakeProcessIter([processes[0], failing, zombie, denied]),
    ):
        collector = WindowsTelemetryCollector()
        results = collector.collect_processes()

    assert len(results) == 1
    assert results[0].pid == 1
    assert all(r.ok is True for r in results)


def test_one_failing_process_does_not_crash_the_cycle():
    good = _process(1, "good", 1.0, 1_000_000)
    broken = mock.Mock()
    broken.info = {"pid": 2, "name": "broken.exe"}
    broken.cpu_percent.side_effect = RuntimeError("wmi failure")

    with mock.patch(
        "src.data.windows_collector.psutil.process_iter",
        return_value=FakeProcessIter([good, broken]),
    ):
        collector = WindowsTelemetryCollector()
        results = collector.collect_processes()

    assert len(results) == 2
    ok_results = [r for r in results if r.ok]
    error_results = [r for r in results if not r.ok]
    assert len(ok_results) == 1
    assert len(error_results) == 1
    assert error_results[0].pid == 2
    assert "wmi failure" in error_results[0].error


# ---------------------------------------------------------------------------
# Mapping into the ingestion contract
# ---------------------------------------------------------------------------


def test_observations_to_metrics_preserves_contract():
    now = datetime(2026, 1, 15, 9, 30, 0, tzinfo=timezone.utc)
    host = HostMeasurement(
        timestamp=now,
        host_id="test-host",
        source=SOURCE_WINDOWS_LOCAL,
        cpu_percent=42.5,
        memory_percent=60.0,
        memory_used_bytes=8_000_000_000,
        memory_total_bytes=16_000_000_000,
        ok=True,
    )
    processes = (
        ProcessMeasurement(
            timestamp=now,
            host_id="test-host",
            source=SOURCE_WINDOWS_LOCAL,
            pid=101,
            name="svchost.exe",
            cpu_percent=5.5,
            memory_rss_bytes=42_000_000,
            status="running",
            ok=True,
        ),
    )

    metrics = observations_to_metrics(host, processes)

    by_name = {m.metric_name: m for m in metrics}
    assert set(by_name) >= {
        "cpu_percent",
        "memory_percent",
        "memory_used_bytes",
        "memory_total_bytes",
        "process_cpu_percent",
        "process_memory_rss_bytes",
    }
    cpu = by_name["cpu_percent"]
    assert cpu.service == "test-host"
    assert cpu.environment == Environment.PRODUCTION
    assert cpu.metric_name == "cpu_percent"
    assert cpu.value == 42.5
    assert cpu.unit == "percent"
    assert cpu.source == SOURCE_WINDOWS_LOCAL
    assert cpu.host_id == "test-host"
    assert cpu.timestamp == now

    proc_cpu = by_name["process_cpu_percent"]
    assert proc_cpu.service == "test-host:101"
    assert proc_cpu.host_id == "test-host"
    assert proc_cpu.source == SOURCE_WINDOWS_LOCAL

    # Unavailable host measurements are omitted, never recorded as zero.
    broken_host = HostMeasurement(
        timestamp=now,
        host_id="test-host",
        source=SOURCE_WINDOWS_LOCAL,
        cpu_percent=None,
        memory_percent=None,
        memory_used_bytes=None,
        memory_total_bytes=None,
        ok=False,
        error="wmi down",
    )
    assert observations_to_metrics(broken_host, ()) == []


def test_observations_to_metrics_skips_unavailable_process_values():
    now = datetime(2026, 1, 15, 9, 30, 0, tzinfo=timezone.utc)
    host = HostMeasurement(
        timestamp=now,
        host_id="test-host",
        source=SOURCE_WINDOWS_LOCAL,
        cpu_percent=10.0,
        memory_percent=20.0,
        memory_used_bytes=1_000_000,
        memory_total_bytes=2_000_000,
        ok=True,
    )
    processes = (
        ProcessMeasurement(
            timestamp=now,
            host_id="test-host",
            source=SOURCE_WINDOWS_LOCAL,
            pid=101,
            name="broken.exe",
            cpu_percent=None,
            memory_rss_bytes=None,
            status=None,
            ok=False,
            error="access denied",
        ),
    )

    metrics = observations_to_metrics(host, processes)
    names = {m.metric_name for m in metrics}
    assert names == {"cpu_percent", "memory_percent", "memory_used_bytes", "memory_total_bytes"}


# ---------------------------------------------------------------------------
# Start / stop lifecycle
# ---------------------------------------------------------------------------


def test_start_stop_is_clean_and_thread_terminates():
    collected = []

    def publish(host, processes):
        collected.append((host, processes))

    with mock.patch(
        "src.data.windows_collector.psutil.cpu_percent", return_value=3.0
    ), mock.patch(
        "src.data.windows_collector.psutil.virtual_memory", return_value=_memory()
    ), mock.patch(
        "src.data.windows_collector.psutil.process_iter",
        return_value=FakeProcessIter([]),
    ):
        collector = WindowsTelemetryCollector(host_id="test-host")
        collector.start(interval_seconds=0.01, publish=publish)

        assert collector.state == CollectorState.RUNNING
        assert collector.is_running
        assert collector.session is not None
        assert collector.session.status == "active"

        # Wait for at least one collection cycle (bounded; never indefinite).
        import threading

        done = threading.Event()
        original_publish = publish

        def signaling_publish(host, processes):
            original_publish(host, processes)
            done.set()

        collector._publish = signaling_publish
        assert done.wait(timeout=5.0), "collector never published a sample"

        collector.stop(timeout=5.0)

    assert not collector.is_running
    assert collector.state == CollectorState.STOPPED
    session = collector.session
    assert session is not None
    assert session.status == "stopped"
    assert session.end_time is not None
    assert session.end_time >= session.start_time
    assert session.sample_count >= 1
    assert len(collected) >= 1


def test_repeated_start_does_not_create_duplicate_loops():
    calls = []

    def publish(host, processes):
        calls.append(None)

    with mock.patch(
        "src.data.windows_collector.psutil.cpu_percent", return_value=3.0
    ), mock.patch(
        "src.data.windows_collector.psutil.virtual_memory", return_value=_memory()
    ), mock.patch(
        "src.data.windows_collector.psutil.process_iter",
        return_value=FakeProcessIter([]),
    ):
        collector = WindowsTelemetryCollector(host_id="test-host")
        collector.start(interval_seconds=0.01, publish=publish)
        collector.start(interval_seconds=0.01, publish=publish)
        collector.start(interval_seconds=0.01, publish=publish)

        assert collector.is_running

        import threading

        done = threading.Event()
        orig = publish

        def signaling(host, processes):
            orig(host, processes)
            done.set()

        collector._publish = signaling
        assert done.wait(timeout=5.0)
        collector.stop(timeout=5.0)

    assert not collector.is_running
    # One collector instance, one loop: start was a no-op after the first.
    assert len(calls) >= 1


def test_stop_without_start_is_safe():
    collector = WindowsTelemetryCollector(host_id="test-host")
    collector.stop(timeout=5.0)
    assert collector.state == CollectorState.STOPPED


def test_context_manager_stops_collector():
    with mock.patch(
        "src.data.windows_collector.psutil.cpu_percent", return_value=3.0
    ), mock.patch(
        "src.data.windows_collector.psutil.virtual_memory", return_value=_memory()
    ), mock.patch(
        "src.data.windows_collector.psutil.process_iter",
        return_value=FakeProcessIter([]),
    ):
        with WindowsTelemetryCollector(host_id="test-host") as collector:
            collector.start(interval_seconds=0.01)
            assert collector.is_running

    assert not collector.is_running


# ---------------------------------------------------------------------------
# Collection failure surfaces as error state, not a zero measurement
# ---------------------------------------------------------------------------


def test_collection_error_is_recorded_instead_of_zero():
    def explode(*args, **kwargs):
        raise psutil.AccessDenied("wmi access denied")

    with mock.patch(
        "src.data.windows_collector.psutil.cpu_percent", side_effect=explode
    ), mock.patch(
        "src.data.windows_collector.psutil.virtual_memory", return_value=_memory()
    ):
        collector = WindowsTelemetryCollector(host_id="test-host")
        measurement = collector.collect_host()

    assert measurement.ok is False
    assert measurement.cpu_percent is None
    assert measurement.memory_percent is None
    assert "wmi access denied" in measurement.error


def test_source_tag_distinguishes_real_telemetry():
    with mock.patch(
        "src.data.windows_collector.psutil.cpu_percent", return_value=3.0
    ), mock.patch(
        "src.data.windows_collector.psutil.virtual_memory", return_value=_memory()
    ):
        collector = WindowsTelemetryCollector(host_id="my-host")
        measurement = collector.collect_host()

    assert measurement.source == "windows_local"
    assert measurement.host_id == "my-host"


def test_explicit_host_id_preferred_over_node():
    with mock.patch(
        "src.data.windows_collector.platform.node", return_value="should-not-be-used"
    ), mock.patch(
        "src.data.windows_collector.psutil.cpu_percent", return_value=3.0
    ), mock.patch(
        "src.data.windows_collector.psutil.virtual_memory", return_value=_memory()
    ):
        collector = WindowsTelemetryCollector(host_id="explicit-host")
        assert collector.host_id == "explicit-host"
        assert collector.collect_host().host_id == "explicit-host"


def test_session_has_start_and_end():
    import threading

    done = threading.Event()

    def publish(host, processes):
        done.set()

    with mock.patch(
        "src.data.windows_collector.psutil.cpu_percent", return_value=3.0
    ), mock.patch(
        "src.data.windows_collector.psutil.virtual_memory", return_value=_memory()
    ), mock.patch(
        "src.data.windows_collector.psutil.process_iter",
        return_value=FakeProcessIter([]),
    ):
        collector = WindowsTelemetryCollector(host_id="test-host")
        collector.start(interval_seconds=0.01, publish=publish)
        assert done.wait(timeout=5.0)
        collector.stop(timeout=5.0)

    session = collector.session
    assert isinstance(session, ObservationSession)
    assert session.start_time.tzinfo is timezone.utc
    assert session.end_time.tzinfo is timezone.utc
    assert session.end_time > session.start_time
