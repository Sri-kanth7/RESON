"""Tests for the common collector interface, platform factory, and host identity.

These tests are platform independent: they exercise the shared lifecycle with
a controlled fake collector and never depend on real hardware, real sleeps, or
real processes.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from src.data.collector import (
    BaseCollector,
    Collector,
    CollectorHealth,
    CollectorState,
    HostSample,
    ProcessSample,
    observations_to_metrics,
)
from src.data.collector_factory import (
    SUPPORTED_PLATFORMS,
    create_collector,
    normalize_platform,
)
from src.data.host_identity import (
    host_display_name,
    machine_fingerprint,
    reset_host_id_cache,
    stable_host_id,
)


class FakeCollector(BaseCollector):
    """Deterministic collector used to exercise the shared lifecycle."""

    def __init__(self, host_id="fake-host", source="simulation", fail=False):
        self._fail = fail
        self.collect_calls = 0
        super().__init__(host_id=host_id, source=source)

    @staticmethod
    def _default_host_id() -> str:
        return "fake-host"

    @staticmethod
    def capabilities() -> dict[str, bool]:
        return {"cpu": True, "memory": True, "disk": False, "network": False}

    def collect_host(self) -> HostSample:
        self.collect_calls += 1
        if self._fail:
            return HostSample(
                timestamp=datetime.now(timezone.utc),
                host_id=self._host_id,
                source=self._source,
                ok=False,
                error="synthetic failure",
            )
        return HostSample(
            timestamp=datetime.now(timezone.utc),
            host_id=self._host_id,
            source=self._source,
            cpu_percent=10.0,
            memory_percent=20.0,
            memory_used_bytes=100,
            memory_total_bytes=1000,
            ok=True,
        )

    def collect_processes(self) -> tuple[ProcessSample, ...]:
        return (
            ProcessSample(
                timestamp=datetime.now(timezone.utc),
                host_id=self._host_id,
                source=self._source,
                pid=42,
                name="fake",
                cpu_percent=1.0,
                memory_rss_bytes=2048,
                status="running",
                ok=True,
            ),
        )


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_collector_does_not_start_anything_on_construction():
    collector = FakeCollector()
    assert collector.state == CollectorState.STOPPED
    assert collector.is_running is False
    assert collector.session is None
    assert collector.last_host_measurement is None
    assert collector.last_process_measurements == ()


def test_start_runs_a_background_loop_and_stop_terminates_it():
    collector = FakeCollector()
    collector.start(interval_seconds=0.05)
    assert collector.is_running is True
    assert collector.state == CollectorState.RUNNING

    deadline = time.time() + 3.0
    while collector.last_host_measurement is None and time.time() < deadline:
        time.sleep(0.02)
    assert collector.last_host_measurement is not None

    collector.stop()
    assert collector.is_running is False
    assert collector.state == CollectorState.STOPPED


def test_start_twice_does_not_create_a_duplicate_collector():
    collector = FakeCollector()
    collector.start(interval_seconds=0.05)
    first = collector._thread
    collector.start(interval_seconds=0.05)
    assert collector._thread is first
    collector.stop()


def test_health_reports_real_state_not_a_ui_flag():
    collector = FakeCollector()
    health = collector.health()
    assert isinstance(health, CollectorHealth)
    assert health.running is False
    assert health.state == "stopped"
    assert health.last_successful_sample is None
    assert health.sample_count == 0
    assert health.capabilities["cpu"] is True

    collector.start(interval_seconds=0.05)
    deadline = time.time() + 3.0
    while collector.health().last_successful_sample is None and time.time() < deadline:
        time.sleep(0.02)
    health = collector.health()
    assert health.running is True
    assert health.sample_count >= 1
    assert health.last_successful_sample is not None
    collector.stop()


def test_failed_host_collection_sets_error_state():
    collector = FakeCollector(fail=True)
    collector.start(interval_seconds=0.05)
    deadline = time.time() + 3.0
    while collector.last_error is None and time.time() < deadline:
        time.sleep(0.02)
    assert collector.last_error is not None
    assert collector.state == CollectorState.ERROR
    health = collector.health()
    assert health.error_count >= 1
    collector.stop()


def test_publish_failure_is_surfaced_not_swallowed():
    collector = FakeCollector()

    def bad_publish(host, processes):
        raise RuntimeError("publish exploded")

    collector.start(interval_seconds=0.05, publish=bad_publish)
    deadline = time.time() + 3.0
    while collector.last_error is None and time.time() < deadline:
        time.sleep(0.02)
    assert collector.last_error is not None
    assert "publish" in collector.last_error.lower()
    collector.stop()


def test_publish_callback_receives_host_and_process_samples():
    collector = FakeCollector()
    received: list = []

    collector.start(
        interval_seconds=0.05,
        publish=lambda host, processes: received.append((host, processes)),
    )
    deadline = time.time() + 3.0
    while not received and time.time() < deadline:
        time.sleep(0.02)
    collector.stop()

    assert received, "publish callback was never invoked"
    host, processes = received[0]
    assert isinstance(host, HostSample)
    assert isinstance(processes, tuple)
    assert processes and isinstance(processes[0], ProcessSample)


def test_session_records_start_and_stop():
    collector = FakeCollector()
    collector.start(interval_seconds=0.05)
    session = collector.session
    assert session is not None
    assert session.status == "active"
    assert session.host_id == "fake-host"
    collector.stop()
    assert collector.session is not None
    assert collector.session.end_time is not None
    assert collector.session.status == "stopped"


def test_context_manager_starts_and_stops():
    with FakeCollector() as collector:
        assert collector.is_running is True
    assert collector.is_running is False


# ---------------------------------------------------------------------------
# Sampling policy
# ---------------------------------------------------------------------------


def _sample_set(processes: int = 3):
    """Build a synthetic collection result with the given process count."""
    from src.data.collector import (
        CollectionResult,
        DiskSample,
        NetworkSample,
    )

    now = datetime.now(timezone.utc)
    return CollectionResult(
        host=HostSample(
            timestamp=now,
            host_id="fake-host",
            source="simulation",
            cpu_percent=1.0,
            ok=True,
        ),
        processes=tuple(
            ProcessSample(
                timestamp=now,
                host_id="fake-host",
                source="simulation",
                pid=pid,
                name=f"p{pid}",
                cpu_percent=float(pid),
                ok=True,
            )
            for pid in range(processes)
        ),
        disk=(
            DiskSample(
                timestamp=now,
                host_id="fake-host",
                source="simulation",
                mountpoint="/",
                total_bytes=100,
                ok=True,
            ),
        ),
        network=(
            NetworkSample(
                timestamp=now,
                host_id="fake-host",
                source="simulation",
                interface="eth0",
                bytes_sent=1,
                ok=True,
            ),
        ),
    )


def test_sampling_policy_keeps_host_metrics_every_cycle():
    from src.data.collector import SamplingPolicy, apply_sampling_policy

    policy = SamplingPolicy(process_sample_every=5, slow_sample_every=30)
    for cycle in range(1, 40):
        result = apply_sampling_policy(_sample_set(), cycle, policy)
        assert result.host is not None
        assert result.host.cpu_percent == 1.0


def test_sampling_policy_publishes_processes_on_the_configured_cadence():
    from src.data.collector import SamplingPolicy, apply_sampling_policy

    policy = SamplingPolicy(process_sample_every=5, slow_sample_every=30)
    published = {
        cycle: len(
            apply_sampling_policy(_sample_set(), cycle, policy).processes
        )
        for cycle in range(1, 12)
    }
    # The first cycle always publishes the full set.
    assert published[1] == 3
    # Then every 5th cycle.
    assert published[5] == 3
    assert published[10] == 3
    for cycle in (2, 3, 4, 6, 7, 9, 11):
        assert published[cycle] == 0, cycle


def test_sampling_policy_publishes_slow_metrics_on_the_configured_cadence():
    from src.data.collector import SamplingPolicy, apply_sampling_policy

    policy = SamplingPolicy(process_sample_every=5, slow_sample_every=30)
    assert apply_sampling_policy(_sample_set(), 1, policy).disk
    assert apply_sampling_policy(_sample_set(), 2, policy).disk == ()
    assert apply_sampling_policy(_sample_set(), 30, policy).disk
    assert apply_sampling_policy(_sample_set(), 31, policy).disk == ()


def test_sampling_policy_caps_the_process_list_by_cpu():
    from src.data.collector import SamplingPolicy, apply_sampling_policy

    policy = SamplingPolicy(process_sample_every=1, max_processes=2)
    result = apply_sampling_policy(_sample_set(processes=5), 2, policy)
    assert len(result.processes) == 2
    # The two highest-CPU processes are kept.
    assert {sample.pid for sample in result.processes} == {3, 4}


def test_sampling_policy_validation():
    from src.data.collector import SamplingPolicy

    with pytest.raises(ValueError):
        SamplingPolicy(process_sample_every=0).validate()
    with pytest.raises(ValueError):
        SamplingPolicy(slow_sample_every=0).validate()
    with pytest.raises(ValueError):
        SamplingPolicy(max_processes=-1).validate()
    assert SamplingPolicy().validate() is not None


def test_collector_reports_its_sampling_policy():
    from src.data.collector import SamplingPolicy

    collector = FakeCollector()
    assert collector.health().policy is not None
    collector = FakeCollector()
    collector._policy = SamplingPolicy(process_sample_every=7)
    assert collector.health().policy.process_sample_every == 7


# ---------------------------------------------------------------------------
# Sample conversion
# ---------------------------------------------------------------------------


def test_observations_to_metrics_preserves_source_and_host_id():
    host = HostSample(
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        host_id="host-1",
        source="linux_local",
        cpu_percent=10.0,
        memory_percent=20.0,
        memory_used_bytes=100,
        memory_total_bytes=1000,
    )
    metrics = observations_to_metrics(host, ())
    assert metrics
    assert {m.source for m in metrics} == {"linux_local"}
    assert {m.host_id for m in metrics} == {"host-1"}
    assert {m.service for m in metrics} == {"host-1"}


def test_observations_to_metrics_omits_unavailable_measurements():
    host = HostSample(
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        host_id="host-1",
        source="linux_local",
        cpu_percent=None,
        memory_percent=None,
        ok=False,
        error="failed",
    )
    assert observations_to_metrics(host, ()) == []


def test_observations_to_metrics_skips_failed_processes():
    host = None
    processes = (
        ProcessSample(
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            host_id="host-1",
            source="linux_local",
            pid=1,
            name="ok",
            cpu_percent=5.0,
            ok=True,
        ),
        ProcessSample(
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            host_id="host-1",
            source="linux_local",
            pid=2,
            name="denied",
            ok=False,
            error="access denied",
        ),
    )
    records = observations_to_metrics(host, processes)
    assert len(records) == 1
    assert records[0].service == "host-1:1"


# ---------------------------------------------------------------------------
# Platform factory
# ---------------------------------------------------------------------------


def test_normalize_platform_maps_common_names():
    assert normalize_platform("Windows") == "windows"
    assert normalize_platform("Linux") == "linux"
    assert normalize_platform("Darwin") == "macos"
    assert normalize_platform("macOS") == "macos"


def test_normalize_platform_rejects_unsupported_platform():
    with pytest.raises(ValueError) as excinfo:
        normalize_platform("plan9")
    assert "Supported platforms" in str(excinfo.value)
    for platform in SUPPORTED_PLATFORMS:
        assert platform in str(excinfo.value)


def test_create_collector_returns_the_right_platform_implementation():
    from src.data.linux_collector import LinuxTelemetryCollector
    from src.data.macos_collector import MacOSTelemetryCollector
    from src.data.windows_collector import WindowsTelemetryCollector

    assert isinstance(create_collector("windows"), WindowsTelemetryCollector)
    assert isinstance(create_collector("linux"), LinuxTelemetryCollector)
    assert isinstance(create_collector("macos"), MacOSTelemetryCollector)


def test_created_collectors_use_their_platform_source():
    assert create_collector("windows").source == "windows_local"
    assert create_collector("linux").source == "linux_local"
    assert create_collector("macos").source == "macos_local"


def test_created_collectors_implement_the_common_interface():
    for platform in SUPPORTED_PLATFORMS:
        collector = create_collector(platform)
        assert isinstance(collector, Collector)
        assert collector.is_running is False
        health = collector.health()
        assert health.source == collector.source
        assert health.host_id == collector.host_id
        assert isinstance(health.capabilities, dict)


def test_explicit_host_id_is_used():
    collector = create_collector("linux", host_id="explicit-host")
    assert collector.host_id == "explicit-host"


# ---------------------------------------------------------------------------
# Host identity
# ---------------------------------------------------------------------------


def test_stable_host_id_is_stable_within_a_process():
    reset_host_id_cache()
    first = stable_host_id()
    second = stable_host_id()
    assert first == second
    assert first
    reset_host_id_cache()


def test_stable_host_id_is_not_a_username_or_path():
    reset_host_id_cache()
    host_id = stable_host_id()
    assert "/" not in host_id
    assert "\\" not in host_id
    assert "root" not in host_id.lower().split("-")[-1]
    reset_host_id_cache()


def test_stable_host_id_does_not_expose_the_raw_machine_identifier():
    reset_host_id_cache()
    fingerprint = machine_fingerprint()
    if fingerprint:
        assert fingerprint not in stable_host_id()
    reset_host_id_cache()


def test_host_id_environment_override(monkeypatch):
    monkeypatch.setenv("RESON_HOST_ID", "reson-custom")
    assert stable_host_id() == "reson-custom"
    monkeypatch.delenv("RESON_HOST_ID", raising=False)


def test_host_display_name_is_non_empty():
    assert host_display_name()
