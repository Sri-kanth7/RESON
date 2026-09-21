"""Tests for metric deviation detection."""

from tests.conftest import make_series

from src.intelligence.baseline import BaselineBuilder
from src.intelligence.deviation import DeviationDetector
from src.intelligence.models import (
    BaselineKey,
    DeviationStatus,
    IntelligenceConfig,
)
from src.preprocessing.normalizer import MetricObservation, group_metrics

KEY = BaselineKey(
    service="api-service",
    environment="development",
    metric_name="latency_ms",
)


def build_context(values):
    metrics = make_series(values)
    series = group_metrics(metrics)[0]
    baseline = BaselineBuilder().build_baseline(series)
    return series, baseline


def observation(value, series):
    return MetricObservation(
        record_id=series.observations[0].record_id,
        timestamp=series.observations[0].timestamp,
        value=value,
    )


def test_normal_observation_is_classified_normal():
    series, baseline = build_context([10.0, 20.0, 30.0])

    deviation = DeviationDetector().detect(KEY, observation(18.0, series), baseline)

    assert deviation.status == DeviationStatus.NORMAL
    assert deviation.is_anomalous is False
    assert deviation.z_score is not None
    assert abs(deviation.z_score) < 3.0


def test_anomalous_observation_is_classified_anomalous():
    series, baseline = build_context([10.0, 20.0, 30.0])

    deviation = DeviationDetector().detect(KEY, observation(80.0, series), baseline)

    assert deviation.status == DeviationStatus.ANOMALOUS
    assert deviation.is_anomalous is True
    assert deviation.z_score == 6.0
    assert deviation.absolute_deviation == 60.0


def test_anomalous_observation_below_threshold_is_normal():
    series, baseline = build_context([10.0, 20.0, 30.0])

    deviation = DeviationDetector().detect(KEY, observation(22.0, series), baseline)

    assert deviation.status == DeviationStatus.NORMAL
    assert deviation.z_score == 0.2


def test_anomalous_at_exact_z_threshold_boundary():
    series, baseline = build_context([10.0, 20.0, 30.0])

    deviation = DeviationDetector().detect(KEY, observation(50.0, series), baseline)

    assert deviation.z_score == 3.0
    assert deviation.status == DeviationStatus.ANOMALOUS


def test_relative_deviation_is_reported():
    series, baseline = build_context([10.0, 20.0, 30.0])

    deviation = DeviationDetector().detect(KEY, observation(20.0, series), baseline)

    assert deviation.relative_deviation == 0.0
    assert deviation.status == DeviationStatus.NORMAL
    assert deviation.absolute_deviation == 0.0


def test_constant_baseline_uses_relative_threshold():
    series, baseline = build_context([100.0, 100.0, 100.0])

    detector = DeviationDetector()

    normal = detector.detect(KEY, observation(110.0, series), baseline)
    elevated = detector.detect(KEY, observation(140.0, series), baseline)

    assert baseline.statistics.std == 0.0
    assert normal.status == DeviationStatus.CONSTANT_BASELINE
    assert normal.is_anomalous is False
    assert elevated.status == DeviationStatus.ANOMALOUS
    assert elevated.is_anomalous is True
    assert elevated.z_score is None
    assert elevated.relative_deviation == 0.4


def test_zero_baseline_is_explicit():
    series, baseline = build_context([0.0, 0.0])

    deviation = DeviationDetector().detect(KEY, observation(5.0, series), baseline)

    assert deviation.status == DeviationStatus.ZERO_BASELINE
    assert deviation.is_anomalous is False
    assert deviation.absolute_deviation == 5.0
    assert deviation.relative_deviation is None
    assert deviation.z_score is None


def test_zero_baseline_matching_value():
    series, baseline = build_context([0.0, 0.0])

    deviation = DeviationDetector().detect(KEY, observation(0.0, series), baseline)

    assert deviation.status == DeviationStatus.ZERO_BASELINE
    assert deviation.absolute_deviation == 0.0


def test_missing_baseline_is_insufficient_reference():
    series, _ = build_context([10.0, 20.0, 30.0])

    deviation = DeviationDetector().detect(KEY, observation(10.0, series), None)

    assert deviation.status == DeviationStatus.INSUFFICIENT_REFERENCE
    assert deviation.is_anomalous is False
    assert deviation.baseline_mean is None
    assert deviation.absolute_deviation is None
    assert deviation.relative_deviation is None


def test_short_baseline_is_insufficient_reference():
    config = IntelligenceConfig(min_observations_per_baseline=4)
    metrics = make_series([10.0, 20.0])
    series = group_metrics(metrics)[0]
    baseline = BaselineBuilder(config).build_baseline(series)

    deviation = DeviationDetector(config).detect(KEY, observation(10.0, series), baseline)

    assert deviation.status == DeviationStatus.INSUFFICIENT_REFERENCE


def test_deviation_preserves_source_identity():
    metrics = make_series([10.0, 20.0, 30.0])
    series = group_metrics(metrics)[0]
    baseline = BaselineBuilder().build_baseline(series)

    deviations = DeviationDetector().detect_series(series, {baseline.key: baseline})

    assert [d.record_id for d in deviations] == [m.id for m in metrics]
    assert [d.timestamp for d in deviations] == [m.timestamp for m in metrics]
    assert all(d.key.service == "api-service" for d in deviations)
    assert all(d.key.environment == "development" for d in deviations)
    assert all(d.key.metric_name == "latency_ms" for d in deviations)
    assert all(d.baseline_mean == 20.0 for d in deviations)


def test_detect_series_uses_matching_baselines_per_key():
    metrics = (
        make_series([10.0, 20.0, 30.0], metric_name="latency_ms")
        + make_series([1.0, 1.0, 1.0], metric_name="cpu_usage")
    )
    series = group_metrics(metrics)
    baselines = BaselineBuilder().build_baselines(series)

    deviations = [
        detection
        for one_series in series
        for detection in DeviationDetector().detect_series(one_series, baselines)
    ]

    assert len(deviations) == 6
    assert all(d.key.metric_name in {"latency_ms", "cpu_usage"} for d in deviations)