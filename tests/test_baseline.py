"""Tests for historical baseline construction."""

from datetime import timedelta

from tests.conftest import DEFAULT_START, make_series

from src.intelligence.baseline import BaselineBuilder
from src.intelligence.models import BaselineKey, IntelligenceConfig
from src.preprocessing.normalizer import group_metrics


def build_baseline(values, config=None):
    series = group_metrics(make_series(values))[0]
    return BaselineBuilder(config).build_baseline(series)


def test_statistics_are_correct():
    baseline = build_baseline([10.0, 20.0, 30.0])

    assert baseline is not None
    assert baseline.statistics.count == 3
    assert baseline.statistics.mean == 20.0
    assert baseline.statistics.std == 10.0
    assert baseline.statistics.min == 10.0
    assert baseline.statistics.max == 30.0
    assert baseline.statistics.p25 == 15.0
    assert baseline.statistics.p50 == 20.0
    assert baseline.statistics.p75 == 25.0


def test_statistics_use_sample_standard_deviation():
    baseline = build_baseline([0.0, 10.0])

    assert baseline is not None
    assert baseline.statistics.std == (50.0 ** 0.5)


def test_baseline_identity_is_explicit():
    series = group_metrics(
        make_series(
            [1.0, 2.0],
            service="payment-service",
            metric_name="cpu_usage",
        )
    )[0]

    baseline = BaselineBuilder().build_baseline(series)

    assert baseline.key == BaselineKey(
        service="payment-service",
        environment="development",
        metric_name="cpu_usage",
    )


def test_baseline_window_covers_supplied_observations():
    baseline = build_baseline([1.0, 2.0, 3.0])

    assert baseline is not None
    assert baseline.start_time == DEFAULT_START
    assert baseline.end_time == DEFAULT_START + timedelta(seconds=20)
    assert baseline.start_time <= baseline.end_time


def test_baseline_output_is_deterministic():
    metrics = make_series([10.0, 20.0, 30.0, 40.0, 50.0])
    series = group_metrics(metrics)[0]

    builder = BaselineBuilder()

    first = builder.build_baseline(series)
    second = builder.build_baseline(series)

    assert first.model_dump() == second.model_dump()


def test_insufficient_history_returns_none():
    one_observation = group_metrics(make_series([5.0]))[0]

    assert BaselineBuilder().build_baseline(one_observation) is None


def test_insufficient_history_respects_configured_minimum():
    config = IntelligenceConfig(min_observations_per_baseline=4)

    three_observations = group_metrics(make_series([5.0, 6.0, 7.0]))[0]

    assert BaselineBuilder(config).build_baseline(three_observations) is None


def test_constant_values_baseline_has_zero_variance():
    baseline = build_baseline([7.0, 7.0, 7.0])

    assert baseline is not None
    assert baseline.statistics.mean == 7.0
    assert baseline.statistics.std == 0.0
    assert baseline.statistics.min == 7.0
    assert baseline.statistics.max == 7.0


def test_zero_values_baseline():
    baseline = build_baseline([0.0, 0.0])

    assert baseline is not None
    assert baseline.statistics.mean == 0.0
    assert baseline.statistics.std == 0.0
    assert baseline.statistics.min == 0.0
    assert baseline.statistics.max == 0.0


def test_baseline_preserves_source_record_ids():
    metrics = make_series([10.0, 20.0])
    series = group_metrics(metrics)[0]

    baseline = BaselineBuilder().build_baseline(series)

    assert sorted(str(record_id) for record_id in baseline.source_record_ids) == sorted(
        str(metric.id) for metric in metrics
    )


def test_baseline_explanation_is_present():
    baseline = build_baseline([1.0, 2.0])

    assert baseline is not None
    assert "api-service" in baseline.explanation
    assert "latency_ms" in baseline.explanation
    assert "2 observations" in baseline.explanation


def test_build_baselines_separates_metrics_and_services():
    metrics = (
        make_series([1.0, 2.0], service="api-service", metric_name="latency_ms")
        + make_series([5.0, 6.0], service="api-service", metric_name="cpu_usage")
        + make_series([9.0, 10.0], service="payment-service", metric_name="latency_ms")
    )

    baselines = BaselineBuilder().build_baselines(group_metrics(metrics))

    assert set(baselines) == {
        BaselineKey(service="api-service", environment="development", metric_name="latency_ms"),
        BaselineKey(service="api-service", environment="development", metric_name="cpu_usage"),
        BaselineKey(service="payment-service", environment="development", metric_name="latency_ms"),
    }


def test_build_baselines_skips_insufficient_series():
    metrics = make_series([1.0, 2.0]) + make_series([5.0])

    baselines = BaselineBuilder().build_baselines(group_metrics(metrics))

    assert len(baselines) == 1
    assert BaselineKey(service="api-service", environment="development", metric_name="latency_ms") in baselines


def test_baseline_is_serializable():
    baseline = build_baseline([1.0, 2.0, 3.0])

    payload = baseline.model_dump()

    assert payload["key"] == {
        "service": "api-service",
        "environment": "development",
        "metric_name": "latency_ms",
    }
    assert payload["statistics"]["count"] == 3
    assert payload["statistics"]["mean"] == 2.0