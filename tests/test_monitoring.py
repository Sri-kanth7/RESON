"""Tests for source-aware monitoring queries and honest status derivation.

These are pure domain tests: no browser, no collector thread, and no network.
They pin the behaviour the dashboard depends on — in particular that stale
telemetry is never reported as live and that unknown provenance stays unknown.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.data.collector import CollectorState
from src.data.memory_storage import InMemoryStorage
from src.data.monitoring import (
    CollectorStatus,
    MonitoringMode,
    derive_status,
    host_inventory,
    latest_metric_values,
    latest_observation,
    metric_series,
    process_inventory,
    resolve_mode,
    source_inventory,
)
from src.data.schemas import (
    Environment,
    Metric,
    SOURCE_LINUX_LOCAL,
    SOURCE_SIMULATION,
    SOURCE_UNKNOWN,
)

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


def _metric(
    timestamp,
    metric_name="cpu_percent",
    value=10.0,
    service="host-1",
    source=SOURCE_LINUX_LOCAL,
    host_id="host-1",
):
    return Metric(
        timestamp=timestamp,
        service=service,
        environment=Environment.PRODUCTION,
        metric_name=metric_name,
        value=value,
        unit="percent",
        source=source,
        host_id=host_id,
    )


class StubCollector:
    """Minimal collector stub exposing the attributes status derivation reads."""

    def __init__(self, running=True, error=None):
        self.is_running = running
        self.state = CollectorState.RUNNING if running else CollectorState.STOPPED
        self.last_error = error
        self.source = SOURCE_LINUX_LOCAL
        self.host_id = "host-1"


# ---------------------------------------------------------------------------
# Source inventory
# ---------------------------------------------------------------------------


def test_source_inventory_groups_by_source():
    storage = InMemoryStorage()
    storage.save_metric(_metric(NOW, source=SOURCE_LINUX_LOCAL))
    storage.save_metric(
        _metric(NOW, source=SOURCE_SIMULATION, service="api", host_id="sim")
    )

    inventory = {item.source: item for item in source_inventory(storage)}
    assert set(inventory) == {SOURCE_LINUX_LOCAL, SOURCE_SIMULATION}
    assert inventory[SOURCE_LINUX_LOCAL].metrics == 1
    assert inventory[SOURCE_SIMULATION].metrics == 1
    assert inventory[SOURCE_LINUX_LOCAL].is_live is True
    assert inventory[SOURCE_SIMULATION].is_live is False


def test_source_inventory_reports_records_without_source_as_unknown():
    storage = InMemoryStorage()
    storage.save_metric(_metric(NOW, source=None))
    inventory = {item.source: item for item in source_inventory(storage)}
    assert SOURCE_UNKNOWN in inventory
    assert inventory[SOURCE_UNKNOWN].metrics == 1
    assert inventory[SOURCE_UNKNOWN].is_live is False


def test_source_inventory_tracks_last_observation_and_hosts():
    storage = InMemoryStorage()
    storage.save_metric(_metric(NOW - timedelta(minutes=5), host_id="host-1"))
    storage.save_metric(_metric(NOW, host_id="host-2"))
    inventory = source_inventory(storage)[0]
    assert inventory.last_observed == NOW
    assert set(inventory.hosts) == {"host-1", "host-2"}
    assert inventory.total == 2


def test_host_inventory_summarizes_hosts():
    storage = InMemoryStorage()
    storage.save_metric(_metric(NOW, host_id="host-1"))
    storage.save_metric(_metric(NOW, host_id="host-1"))
    storage.save_metric(
        _metric(NOW, source=SOURCE_SIMULATION, service="api", host_id="sim-host")
    )
    hosts = {item.host_id: item for item in host_inventory(storage)}
    assert hosts["host-1"].metric_count == 2
    assert hosts["host-1"].is_live is True
    assert hosts["sim-host"].is_live is False


def test_host_inventory_can_be_filtered_by_source():
    storage = InMemoryStorage()
    storage.save_metric(_metric(NOW, host_id="host-1"))
    storage.save_metric(
        _metric(NOW, source=SOURCE_SIMULATION, service="api", host_id="sim-host")
    )
    hosts = host_inventory(storage, source=SOURCE_LINUX_LOCAL)
    assert [item.host_id for item in hosts] == ["host-1"]


def test_latest_observation_respects_source_and_host():
    storage = InMemoryStorage()
    storage.save_metric(_metric(NOW - timedelta(hours=1)))
    storage.save_metric(
        _metric(NOW, source=SOURCE_SIMULATION, service="api", host_id="sim")
    )
    assert latest_observation(storage, source=SOURCE_LINUX_LOCAL) == NOW - timedelta(
        hours=1
    )
    assert latest_observation(storage) == NOW


# ---------------------------------------------------------------------------
# Status derivation
# ---------------------------------------------------------------------------


def test_running_collector_with_recent_data_is_running_and_live():
    storage = InMemoryStorage()
    storage.save_metric(_metric(NOW - timedelta(seconds=5)))
    report = derive_status(
        collector=StubCollector(running=True),
        storage=storage,
        now=NOW,
        stale_after_seconds=120,
    )
    assert report.status == CollectorStatus.RUNNING
    assert report.healthy is True
    assert report.is_live is True
    assert report.stale is False
    assert "running" in report.detail.lower()


def test_running_collector_with_stale_data_is_not_live():
    storage = InMemoryStorage()
    storage.save_metric(_metric(NOW - timedelta(minutes=30)))
    report = derive_status(
        collector=StubCollector(running=True),
        storage=storage,
        now=NOW,
        stale_after_seconds=120,
    )
    assert report.status == CollectorStatus.NO_RECENT_TELEMETRY
    assert report.is_live is False
    assert report.stale is True


def test_running_collector_with_no_stored_data_is_not_running_status():
    storage = InMemoryStorage()
    report = derive_status(
        collector=StubCollector(running=True),
        storage=storage,
        now=NOW,
    )
    assert report.status == CollectorStatus.NO_RECENT_TELEMETRY
    assert report.is_live is False


def test_stopped_collector_with_data_reports_stopped_not_live():
    storage = InMemoryStorage()
    storage.save_metric(_metric(NOW - timedelta(seconds=10)))
    report = derive_status(
        collector=StubCollector(running=False),
        storage=storage,
        now=NOW,
    )
    assert report.status == CollectorStatus.STOPPED
    assert report.is_live is False


def test_stopped_collector_without_data_reports_no_data():
    report = derive_status(
        collector=StubCollector(running=False),
        storage=InMemoryStorage(),
        now=NOW,
    )
    assert report.status == CollectorStatus.NO_DATA


def test_no_collector_with_data_reports_historical_only():
    storage = InMemoryStorage()
    storage.save_metric(_metric(NOW - timedelta(minutes=5)))
    report = derive_status(collector=None, storage=storage, now=NOW)
    assert report.status == CollectorStatus.HISTORICAL_ONLY
    assert report.is_live is False
    assert "historical" in report.detail.lower()


def test_no_collector_and_no_data_reports_no_data():
    report = derive_status(collector=None, storage=InMemoryStorage(), now=NOW)
    assert report.status == CollectorStatus.NO_DATA


def test_collector_error_is_surfaced():
    storage = InMemoryStorage()
    storage.save_metric(_metric(NOW))
    report = derive_status(
        collector=StubCollector(running=True, error="collection cycle failed"),
        storage=storage,
        now=NOW,
    )
    assert report.status == CollectorStatus.ERROR
    assert "collection cycle failed" in report.detail


def test_permission_error_is_reported_distinctly():
    storage = InMemoryStorage()
    report = derive_status(
        collector=StubCollector(running=True, error="Access denied reading log"),
        storage=storage,
        now=NOW,
    )
    assert report.status == CollectorStatus.PERMISSION_DENIED
    assert report.is_live is False


def test_status_is_never_inferred_from_a_session_flag():
    """A collector object that claims to be running but has no data and no
    live sample is never reported as a healthy live pipeline."""
    storage = InMemoryStorage()
    report = derive_status(
        collector=StubCollector(running=True),
        storage=storage,
        now=NOW,
    )
    assert report.healthy is False
    assert report.is_live is False


# ---------------------------------------------------------------------------
# Mode resolution
# ---------------------------------------------------------------------------


def test_local_mode_uses_the_platform_source():
    context = resolve_mode(
        MonitoringMode.LOCAL, collector=StubCollector(), storage=InMemoryStorage()
    )
    assert context.mode == MonitoringMode.LOCAL
    assert context.source == SOURCE_LINUX_LOCAL
    assert context.simulated is False


def test_demonstration_mode_targets_simulation_source():
    storage = InMemoryStorage()
    storage.save_metric(
        _metric(NOW, source=SOURCE_SIMULATION, service="api", host_id="sim")
    )
    context = resolve_mode(
        MonitoringMode.DEMONSTRATION, storage=storage
    )
    assert context.source == SOURCE_SIMULATION
    assert context.simulated is True
    assert context.available is True


def test_demonstration_mode_without_data_is_unavailable_with_a_reason():
    context = resolve_mode(
        MonitoringMode.DEMONSTRATION, storage=InMemoryStorage()
    )
    assert context.available is False
    assert context.unavailable_reason
    assert "demonstration" in context.unavailable_reason.lower()


def test_hosted_mode_without_received_telemetry_is_unavailable():
    """A hosted dashboard must not pretend to observe a visitor's computer."""
    context = resolve_mode(MonitoringMode.HOSTED, storage=InMemoryStorage())
    assert context.available is False
    assert context.unavailable_reason
    assert "collector" in context.unavailable_reason.lower()


def test_hosted_mode_with_received_telemetry_is_available():
    storage = InMemoryStorage()
    storage.save_metric(
        _metric(NOW, source="hosted_collector", service="host-9", host_id="host-9")
    )
    context = resolve_mode(MonitoringMode.HOSTED, storage=storage)
    assert context.available is True
    assert context.source == "hosted_collector"


def test_imported_mode_is_never_simulated():
    context = resolve_mode(MonitoringMode.IMPORTED, storage=InMemoryStorage())
    assert context.mode == MonitoringMode.IMPORTED
    assert context.simulated is False


def test_unknown_mode_string_falls_back_to_local():
    context = resolve_mode("nonsense", storage=InMemoryStorage())
    assert context.mode == MonitoringMode.LOCAL


# ---------------------------------------------------------------------------
# Process inventory
# ---------------------------------------------------------------------------


def test_process_inventory_aggregates_cpu_and_memory():
    storage = InMemoryStorage()
    storage.save_metric(
        _metric(
            NOW,
            metric_name="process_cpu_percent",
            value=10.0,
            service="host-1:42",
            host_id="host-1",
        )
    )
    storage.save_metric(
        _metric(
            NOW,
            metric_name="process_cpu_percent",
            value=30.0,
            service="host-1:42",
            host_id="host-1",
        )
    )
    storage.save_metric(
        _metric(
            NOW,
            metric_name="process_memory_rss_bytes",
            value=4096.0,
            service="host-1:42",
            host_id="host-1",
        )
    )

    rows = process_inventory(storage)
    assert len(rows) == 1
    row = rows[0]
    assert row.pid == 42
    assert row.samples == 3
    assert row.avg_cpu_percent == pytest.approx(20.0)
    assert row.max_cpu_percent == pytest.approx(30.0)
    assert row.max_memory_rss_bytes == pytest.approx(4096.0)


def test_process_inventory_ignores_non_process_metrics():
    storage = InMemoryStorage()
    storage.save_metric(_metric(NOW, metric_name="cpu_percent"))
    assert process_inventory(storage) == []


def test_process_inventory_can_be_filtered_by_source():
    storage = InMemoryStorage()
    storage.save_metric(
        _metric(
            NOW,
            metric_name="process_cpu_percent",
            service="host-1:1",
            source=SOURCE_LINUX_LOCAL,
        )
    )
    storage.save_metric(
        _metric(
            NOW,
            metric_name="process_cpu_percent",
            service="api:2",
            source=SOURCE_SIMULATION,
            host_id=None,
        )
    )
    rows = process_inventory(storage, source=SOURCE_LINUX_LOCAL)
    assert [row.pid for row in rows] == [1]


# ---------------------------------------------------------------------------
# Series helpers
# ---------------------------------------------------------------------------


def test_metric_series_groups_and_sorts_by_name():
    storage = InMemoryStorage()
    storage.save_metric(_metric(NOW, metric_name="cpu_percent", value=2.0))
    storage.save_metric(
        _metric(NOW - timedelta(seconds=10), metric_name="cpu_percent", value=1.0)
    )
    storage.save_metric(_metric(NOW, metric_name="memory_percent", value=3.0))

    series = metric_series(storage)
    assert set(series) == {"cpu_percent", "memory_percent"}
    values = [value for _, value in series["cpu_percent"]]
    assert values == [1.0, 2.0]


def test_metric_series_respects_the_time_window():
    storage = InMemoryStorage()
    storage.save_metric(_metric(NOW - timedelta(hours=2)))
    storage.save_metric(_metric(NOW - timedelta(minutes=1)))
    series = metric_series(
        storage, start=NOW - timedelta(minutes=30)
    )
    assert len(series["cpu_percent"]) == 1


def test_latest_metric_values_returns_the_newest_per_name():
    storage = InMemoryStorage()
    storage.save_metric(_metric(NOW - timedelta(minutes=5), value=10.0))
    storage.save_metric(_metric(NOW, value=20.0))
    latest = latest_metric_values(storage)
    assert latest["cpu_percent"].value == 20.0
