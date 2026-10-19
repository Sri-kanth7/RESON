"""Tests for day-over-day historical comparison of retained observations.

All tests use in-memory storage through the existing ``Storage`` abstraction,
fixed timestamps, and deterministic data. No real waiting, no wall clock, and
no Supabase.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.data.memory_storage import InMemoryStorage
from src.data.schemas import Environment, Metric
from src.intelligence.baseline import BaselineReadiness
from src.intelligence.historical_comparison import compare_periods
from src.intelligence.models import BaselineKey

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)  # prior day
T1 = datetime(2026, 1, 2, tzinfo=timezone.utc)  # current day


def _bk(host, metric_name="cpu_percent"):
    return BaselineKey(
        service=host, environment="production", metric_name=metric_name
    )


def _metrics(values, host, source, day, metric_name="cpu_percent", unit="percent"):
    return [
        Metric(
            timestamp=day + timedelta(minutes=index * 5),
            service=host,
            environment=Environment.PRODUCTION,
            metric_name=metric_name,
            value=value,
            unit=unit,
            source=source,
            host_id=host,
        )
        for index, value in enumerate(values)
    ]


def _store(storage, metrics):
    for metric in metrics:
        storage.save_metric(metric)


def test_sufficient_history_produces_comparison():
    storage = InMemoryStorage()
    _store(
        storage,
        _metrics([40.0, 42.0, 41.0, 43.0, 42.0], "host-a", "windows_local", T0),
    )
    _store(
        storage,
        _metrics([85.0, 88.0], "host-a", "windows_local", T1),
    )

    result = compare_periods(
        storage,
        host_id="host-a",
        source="windows_local",
        current_start=T1,
        current_end=T1 + timedelta(hours=1),
        historical_start=T0,
        historical_end=T1 - timedelta(microseconds=1),
    )

    assert result.sufficient is True
    assert result.host_id == "host-a"
    assert result.source == "windows_local"
    assert result.historical_sample_counts[_bk("host-a")] == 5
    assert result.current_sample_counts[_bk("host-a")] == 2
    assert result.baseline_readiness[_bk("host-a")] == BaselineReadiness.READY
    assert result.analysis is not None
    # The current window is far above the prior-day baseline: at least one
    # anomalous deviation is expected, but we do not assert a root cause.
    assert any(d.is_anomalous for d in result.deviations)


def test_insufficient_history_reports_not_sufficient():
    storage = InMemoryStorage()
    _store(
        storage,
        _metrics([42.0], "host-a", "windows_local", T0),
    )
    _store(
        storage,
        _metrics([85.0], "host-a", "windows_local", T1),
    )

    result = compare_periods(
        storage,
        host_id="host-a",
        source="windows_local",
        current_start=T1,
        current_end=T1 + timedelta(hours=1),
        historical_start=T0,
        historical_end=T1 - timedelta(microseconds=1),
    )

    assert result.sufficient is False
    assert result.analysis is None
    assert any("insufficient" in reason.lower() for reason in result.reasons)


def test_no_prior_day_history_is_unavailable_not_fabricated():
    storage = InMemoryStorage()
    _store(
        storage,
        _metrics([85.0], "host-a", "windows_local", T1),
    )

    result = compare_periods(
        storage,
        host_id="host-a",
        source="windows_local",
        current_start=T1,
        current_end=T1 + timedelta(hours=1),
        historical_start=T0,
        historical_end=T1 - timedelta(microseconds=1),
    )

    assert result.sufficient is False
    assert result.analysis is None
    assert result.deviations == []
    assert any("no historical" in reason.lower() for reason in result.reasons)


def test_no_current_observations_reports_unavailable():
    storage = InMemoryStorage()
    _store(
        storage,
        _metrics([42.0, 43.0], "host-a", "windows_local", T0),
    )

    result = compare_periods(
        storage,
        host_id="host-a",
        source="windows_local",
        current_start=T1,
        current_end=T1 + timedelta(hours=1),
        historical_start=T0,
        historical_end=T1 - timedelta(microseconds=1),
    )

    assert result.sufficient is False
    assert any("no current" in reason.lower() for reason in result.reasons)


def test_missing_requested_metrics_are_listed():
    storage = InMemoryStorage()
    _store(
        storage,
        _metrics([42.0, 43.0], "host-a", "windows_local", T0, metric_name="cpu_percent"),
    )
    _store(
        storage,
        _metrics([42.0], "host-a", "windows_local", T0, metric_name="memory_percent"),
    )

    result = compare_periods(
        storage,
        host_id="host-a",
        source="windows_local",
        metric_names=["cpu_percent", "memory_percent", "disk_percent"],
        current_start=T1,
        current_end=T1 + timedelta(hours=1),
        historical_start=T0,
        historical_end=T1 - timedelta(microseconds=1),
    )

    assert "disk_percent" in result.missing_metrics


def test_incompatible_units_are_excluded():
    storage = InMemoryStorage()
    _store(
        storage,
        _metrics([42.0, 43.0], "host-a", "windows_local", T0, unit="percent"),
    )
    _store(
        storage,
        _metrics([85.0], "host-a", "windows_local", T1, unit="bytes"),
    )

    result = compare_periods(
        storage,
        host_id="host-a",
        source="windows_local",
        current_start=T1,
        current_end=T1 + timedelta(hours=1),
        historical_start=T0,
        historical_end=T1 - timedelta(microseconds=1),
    )

    assert "cpu_percent" in result.incompatible_units
    assert result.sufficient is False


def test_windows_must_not_overlap():
    storage = InMemoryStorage()

    with pytest.raises(ValueError, match="must end at or before"):
        compare_periods(
            storage,
            host_id="host-a",
            current_start=T1,
            current_end=T1 + timedelta(hours=1),
            historical_start=T1 - timedelta(hours=1),
            historical_end=T1 + timedelta(minutes=30),
        )


def test_other_host_observations_are_excluded():
    storage = InMemoryStorage()
    _store(
        storage,
        _metrics([42.0, 43.0], "host-a", "windows_local", T0),
    )
    _store(
        storage,
        _metrics([99.0, 99.0], "host-b", "windows_local", T0),
    )
    _store(
        storage,
        _metrics([85.0], "host-a", "windows_local", T1),
    )

    result = compare_periods(
        storage,
        host_id="host-a",
        source="windows_local",
        current_start=T1,
        current_end=T1 + timedelta(hours=1),
        historical_start=T0,
        historical_end=T1 - timedelta(microseconds=1),
    )

    assert result.sufficient is True
    assert result.historical_sample_counts[_bk("host-a")] == 2


def test_simulated_source_is_not_compared_with_real_source():
    storage = InMemoryStorage()
    _store(
        storage,
        _metrics([42.0, 43.0], "host-a", "simulator", T0),
    )
    _store(
        storage,
        _metrics([85.0], "host-a", "windows_local", T1),
    )

    result = compare_periods(
        storage,
        host_id="host-a",
        source="windows_local",
        current_start=T1,
        current_end=T1 + timedelta(hours=1),
        historical_start=T0,
        historical_end=T1 - timedelta(microseconds=1),
    )

    assert result.sufficient is False
    assert any("no historical" in reason.lower() for reason in result.reasons)


def test_storage_abstraction_supports_supabase_style_history():
    """The same comparison code works with any ``Storage`` implementation."""
    storage = InMemoryStorage()
    _store(
        storage,
        _metrics([40.0, 41.0, 42.0], "host-a", "windows_local", T0),
    )
    _store(
        storage,
        _metrics([90.0], "host-a", "windows_local", T1),
    )

    result = compare_periods(
        storage,
        host_id="host-a",
        source="windows_local",
        current_start=T1,
        current_end=T1 + timedelta(hours=1),
        historical_start=T0,
        historical_end=T1 - timedelta(microseconds=1),
    )

    assert result.sufficient is True


def test_memory_mode_keeps_two_periods_in_one_live_instance():
    storage = InMemoryStorage()
    _store(
        storage,
        _metrics([40.0, 41.0, 42.0, 41.0, 40.0], "host-a", "windows_local", T0),
    )
    _store(
        storage,
        _metrics([90.0, 92.0], "host-a", "windows_local", T1),
    )

    first = compare_periods(
        storage,
        host_id="host-a",
        source="windows_local",
        current_start=T1,
        current_end=T1 + timedelta(hours=1),
        historical_start=T0,
        historical_end=T1 - timedelta(microseconds=1),
    )
    assert first.sufficient is True

    # A later current window against the same history still works within the
    # same live instance.
    T2 = datetime(2026, 1, 3, tzinfo=timezone.utc)
    _store(storage, _metrics([41.0], "host-a", "windows_local", T2))
    second = compare_periods(
        storage,
        host_id="host-a",
        source="windows_local",
        current_start=T2,
        current_end=T2 + timedelta(hours=1),
        historical_start=T0,
        historical_end=T1 - timedelta(microseconds=1),
    )
    assert second.sufficient is True


def test_memory_mode_loses_history_on_new_instance():
    first = InMemoryStorage()
    _store(
        first,
        _metrics([40.0, 41.0, 42.0], "host-a", "windows_local", T0),
    )

    second = InMemoryStorage()
    result = compare_periods(
        second,
        host_id="host-a",
        source="windows_local",
        current_start=T1,
        current_end=T1 + timedelta(hours=1),
        historical_start=T0,
        historical_end=T1 - timedelta(microseconds=1),
    )

    assert result.sufficient is False
    assert any("no historical" in reason.lower() for reason in result.reasons)


def test_duplicate_timestamps_are_not_double_counted_across_windows():
    storage = InMemoryStorage()
    shared = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    # Same observation instant stored twice: two distinct records.
    storage.save_metric(
        Metric(
            timestamp=shared,
            service="host-a",
            environment=Environment.PRODUCTION,
            metric_name="cpu_percent",
            value=42.0,
            unit="percent",
            source="windows_local",
            host_id="host-a",
        )
    )
    storage.save_metric(
        Metric(
            timestamp=shared,
            service="host-a",
            environment=Environment.PRODUCTION,
            metric_name="cpu_percent",
            value=42.0,
            unit="percent",
            source="windows_local",
            host_id="host-a",
        )
    )
    _store(
        storage,
        _metrics([85.0], "host-a", "windows_local", T1),
    )

    result = compare_periods(
        storage,
        host_id="host-a",
        source="windows_local",
        current_start=T1,
        current_end=T1 + timedelta(hours=1),
        historical_start=T0,
        historical_end=T1 - timedelta(microseconds=1),
    )

    assert result.historical_sample_counts[_bk("host-a")] == 2


def test_readiness_states_are_included():
    storage = InMemoryStorage()
    # host-a: one sample -> insufficient. host-b: enough -> ready.
    _store(
        storage,
        _metrics([42.0], "host-a", "windows_local", T0),
    )
    _store(
        storage,
        _metrics([42.0, 43.0, 41.0], "host-b", "windows_local", T0),
    )
    _store(
        storage,
        _metrics([90.0], "host-b", "windows_local", T1),
    )

    result = compare_periods(
        storage,
        host_id="host-b",
        source="windows_local",
        current_start=T1,
        current_end=T1 + timedelta(hours=1),
        historical_start=T0,
        historical_end=T1 - timedelta(microseconds=1),
    )

    assert result.sufficient is True
    assert (
        result.baseline_readiness[_bk("host-b")]
        == BaselineReadiness.READY
    )
