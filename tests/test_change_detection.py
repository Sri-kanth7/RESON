"""Tests for sustained behavioral change detection."""

from datetime import timedelta

from tests.conftest import DEFAULT_START

from src.intelligence.change_detection import (
    BatchEvaluation,
    ChangeDetector,
    group_deviations_by_timestamp,
)
from src.intelligence.models import (
    BaselineKey,
    DeviationStatus,
    IntelligenceConfig,
    MetricDeviation,
)

KEY_A = BaselineKey(service="api-service", environment="development", metric_name="latency_ms")
KEY_B = BaselineKey(service="api-service", environment="development", metric_name="cpu_usage")


def dev(key: BaselineKey, timestamp, anomalous: bool, record_id=None):
    """Build a deviation with controlled anomalous status."""
    from uuid import uuid4

    return MetricDeviation(
        record_id=record_id or uuid4(),
        timestamp=timestamp,
        key=key,
        value=100.0 if anomalous else 10.0,
        baseline_mean=10.0,
        absolute_deviation=90.0 if anomalous else 0.0,
        z_score=5.0 if anomalous else 0.0,
        status=DeviationStatus.ANOMALOUS if anomalous else DeviationStatus.NORMAL,
    )


def ts(offset_seconds):
    return DEFAULT_START + timedelta(seconds=offset_seconds)


def test_no_anomalies_returns_empty():
    detector = ChangeDetector()

    evaluations = [
        BatchEvaluation(timestamp=ts(0), deviations=(dev(KEY_A, ts(0), False),)),
        BatchEvaluation(timestamp=ts(10), deviations=(dev(KEY_A, ts(10), False),)),
    ]

    assert detector.detect(evaluations) == []


def test_isolated_anomaly_does_not_trigger_change_point():
    detector = ChangeDetector()

    evaluations = [
        BatchEvaluation(timestamp=ts(0), deviations=(dev(KEY_A, ts(0), False),)),
        BatchEvaluation(timestamp=ts(10), deviations=(dev(KEY_A, ts(10), True),)),
        BatchEvaluation(timestamp=ts(20), deviations=(dev(KEY_A, ts(20), False),)),
    ]

    assert detector.detect(evaluations) == []


def test_sustained_anomaly_produces_change_point():
    detector = ChangeDetector()

    evaluations = [
        BatchEvaluation(timestamp=ts(0), deviations=(dev(KEY_A, ts(0), True),)),
        BatchEvaluation(timestamp=ts(10), deviations=(dev(KEY_A, ts(10), True),)),
    ]

    points = detector.detect(evaluations)

    assert len(points) == 1
    point = points[0]
    assert point.consecutive_batches == 2
    assert point.start_time == ts(0)
    assert point.end_time == ts(10)


def test_exact_threshold_boundary_at_two_batches():
    detector = ChangeDetector()

    two = [
        BatchEvaluation(timestamp=ts(i * 10), deviations=(dev(KEY_A, ts(i * 10), True),))
        for i in range(2)
    ]

    one = [
        BatchEvaluation(timestamp=ts(0), deviations=(dev(KEY_A, ts(0), True),)),
    ]

    assert len(detector.detect(two)) == 1
    assert detector.detect(one) == []


def test_sustained_run_across_three_batches():
    detector = ChangeDetector()

    evaluations = [
        BatchEvaluation(timestamp=ts(i * 10), deviations=(dev(KEY_A, ts(i * 10), True),))
        for i in range(3)
    ]

    points = detector.detect(evaluations)

    assert len(points) == 1
    assert points[0].consecutive_batches == 3


def test_temporal_gap_breaks_continuity():
    detector = ChangeDetector(IntelligenceConfig(max_gap_seconds=20.0))

    evaluations = [
        BatchEvaluation(timestamp=ts(0), deviations=(dev(KEY_A, ts(0), True),)),
        BatchEvaluation(timestamp=ts(100), deviations=(dev(KEY_A, ts(100), True),)),
    ]

    points = detector.detect(evaluations)

    assert points == []


def test_normal_evaluation_breaks_anomalous_run():
    detector = ChangeDetector()

    evaluations = [
        BatchEvaluation(timestamp=ts(0), deviations=(dev(KEY_A, ts(0), True),)),
        BatchEvaluation(timestamp=ts(10), deviations=(dev(KEY_A, ts(10), False),)),
        BatchEvaluation(timestamp=ts(20), deviations=(dev(KEY_A, ts(20), True),)),
    ]

    points = detector.detect(evaluations)

    assert points == []


def test_multiple_affected_metrics_across_run():
    detector = ChangeDetector()

    evaluations = [
        BatchEvaluation(
            timestamp=ts(0),
            deviations=(dev(KEY_A, ts(0), True), dev(KEY_B, ts(0), True)),
        ),
        BatchEvaluation(
            timestamp=ts(10),
            deviations=(dev(KEY_A, ts(10), True), dev(KEY_B, ts(10), False)),
        ),
    ]

    points = detector.detect(evaluations)

    assert len(points) == 1
    assert set(points[0].affected_metric_keys) == {KEY_A, KEY_B}


def test_detect_deviations_groups_by_timestamp():
    detector = ChangeDetector()

    deviations = [
        dev(KEY_A, ts(0), True),
        dev(KEY_B, ts(0), True),
        dev(KEY_A, ts(10), True),
        dev(KEY_B, ts(10), False),
        dev(KEY_A, ts(20), True),
        dev(KEY_B, ts(20), False),
    ]

    points = detector.detect_deviations(deviations)

    assert len(points) == 1
    assert points[0].consecutive_batches == 3
    assert set(points[0].affected_metric_keys) == {KEY_A, KEY_B}


def test_change_point_preserves_source_record_ids():
    id_a, id_b = (dev(KEY_A, ts(0), True).record_id, dev(KEY_B, ts(0), True).record_id)

    detector = ChangeDetector()
    evaluations = [
        BatchEvaluation(
            timestamp=ts(0),
            deviations=(dev(KEY_A, ts(0), True, record_id=id_a), dev(KEY_B, ts(0), True, record_id=id_b)),
        ),
        BatchEvaluation(
            timestamp=ts(10),
            deviations=(dev(KEY_A, ts(10), True),),
        ),
    ]

    points = detector.detect(evaluations)

    assert id_a in points[0].source_record_ids
    assert id_b in points[0].source_record_ids


def test_group_deviations_by_timestamp_orders_deterministically():
    deviations = [
        dev(KEY_B, ts(10), False),
        dev(KEY_A, ts(0), False),
        dev(KEY_A, ts(10), False),
    ]

    batches = group_deviations_by_timestamp(deviations)

    assert [b.timestamp for b in batches] == [ts(0), ts(10)]


def test_empty_input_returns_empty():
    assert ChangeDetector().detect([]) == []
    assert ChangeDetector().detect_deviations([]) == []