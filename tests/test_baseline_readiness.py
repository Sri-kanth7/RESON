"""Tests for evidence-based baseline readiness (no Supabase, no network)."""

from datetime import datetime, timedelta, timezone

from tests.conftest import DEFAULT_START, make_series

from src.intelligence.baseline import (
    BaselineBuilder,
    BaselineReadiness,
    assess_series_readiness,
)
from src.intelligence.models import IntelligenceConfig
from src.preprocessing.normalizer import group_metrics


def series(values, start=DEFAULT_START, interval_seconds=10):
    return group_metrics(make_series(values, start=start, interval_seconds=interval_seconds))[0]


def test_unavailable_when_no_observations():
    from src.preprocessing.normalizer import MetricSeries
    from src.intelligence.models import BaselineKey

    empty = MetricSeries(
        service="api-service",
        environment="development",
        metric_name="latency_ms",
        observations=(),
    )
    assert assess_series_readiness(empty) is BaselineReadiness.UNAVAILABLE


def test_insufficient_data_when_below_minimum():
    assert (
        assess_series_readiness(series([5.0]))
        is BaselineReadiness.INSUFFICIENT_DATA
    )


def test_ready_when_minimum_met():
    assert (
        assess_series_readiness(series([5.0, 6.0]))
        is BaselineReadiness.READY
    )


def test_readiness_respects_configured_minimum():
    config = IntelligenceConfig(min_observations_per_baseline=4)
    assert (
        assess_series_readiness(series([1.0, 2.0, 3.0]), config=config)
        is BaselineReadiness.INSUFFICIENT_DATA
    )
    assert (
        assess_series_readiness(series([1.0, 2.0, 3.0, 4.0]), config=config)
        is BaselineReadiness.READY
    )


def test_stale_when_history_predates_reference():
    # Baseline window ended long before the reference time -> stale even
    # though there are enough samples.
    old_series = series(
        [1.0, 2.0],
        start=DEFAULT_START - timedelta(days=2),
        interval_seconds=10,
    )
    assert (
        assess_series_readiness(
            old_series,
            reference_time=DEFAULT_START,
        )
        is BaselineReadiness.STALE
    )


def test_ready_at_its_own_latest_observation():
    # Without a reference time (or with reference at the last observation),
    # a sufficient series is ready.
    assert (
        assess_series_readiness(series([1.0, 2.0, 3.0]))
        is BaselineReadiness.READY
    )


def test_readiness_uses_max_gap_seconds_from_config():
    config = IntelligenceConfig(max_gap_seconds=5.0)
    recent = series([1.0, 2.0], start=DEFAULT_START - timedelta(seconds=2))
    assert assess_series_readiness(recent, config=config, reference_time=DEFAULT_START) is BaselineReadiness.READY

    stale = series([1.0, 2.0], start=DEFAULT_START - timedelta(seconds=60))
    assert (
        assess_series_readiness(stale, config=config, reference_time=DEFAULT_START)
        is BaselineReadiness.STALE
    )


def test_readiness_enum_values_are_explicit():
    assert BaselineReadiness.UNAVAILABLE == "unavailable"
    assert BaselineReadiness.INSUFFICIENT_DATA == "insufficient_data"
    assert BaselineReadiness.READY == "ready"
    assert BaselineReadiness.STALE == "stale"


def test_builder_still_returns_none_for_insufficient_series():
    assert BaselineBuilder().build_baseline(series([5.0])) is None


def test_builder_returns_baseline_for_sufficient_series():
    baseline = BaselineBuilder().build_baseline(series([1.0, 2.0, 3.0]))
    assert baseline is not None
    assert baseline.statistics.count == 3
