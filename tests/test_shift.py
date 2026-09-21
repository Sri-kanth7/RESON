"""Tests for the Behavior Shift Score."""

from datetime import timedelta

from tests.conftest import DEFAULT_START

from src.intelligence.models import (
    BaselineKey,
    DeviationStatus,
    MetricDeviation,
)
from src.intelligence.shift import BehaviorShiftCalculator

KEY_A = BaselineKey(service="api-service", environment="development", metric_name="latency_ms")
KEY_B = BaselineKey(service="api-service", environment="development", metric_name="error_rate")


def dev(key, anomalous=False, z_score=None, record_id=None):
    from uuid import uuid4

    status = DeviationStatus.ANOMALOUS if anomalous else DeviationStatus.NORMAL
    return MetricDeviation(
        record_id=record_id or uuid4(),
        timestamp=DEFAULT_START,
        key=key,
        value=100.0 if anomalous else 10.0,
        baseline_mean=10.0,
        absolute_deviation=90.0 if anomalous else 0.0,
        z_score=z_score,
        status=status,
    )


def test_normal_behavior_scores_zero():
    shift = BehaviorShiftCalculator().calculate(
        [dev(KEY_A), dev(KEY_B)]
    )

    assert shift.score == 0.0
    assert shift.change_detected is False


def test_empty_deviations_scores_zero():
    shift = BehaviorShiftCalculator().calculate([])

    assert shift.score == 0.0
    assert shift.contributions == []


def test_score_is_bounded_between_zero_and_one():
    calculator = BehaviorShiftCalculator()

    mild = calculator.calculate([dev(KEY_A, anomalous=True, z_score=3.0)])
    extreme = calculator.calculate([dev(KEY_A, anomalous=True, z_score=99.0)])

    assert 0.0 <= mild.score <= 1.0
    assert 0.0 <= extreme.score <= 1.0


def test_increasing_anomaly_count_does_not_decrease_score():
    calculator = BehaviorShiftCalculator()

    one_anomaly = calculator.calculate(
        [dev(KEY_A, anomalous=True, z_score=3.0), dev(KEY_A, z_score=0.0)]
    )
    two_anomalies = calculator.calculate(
        [dev(KEY_A, anomalous=True, z_score=3.0), dev(KEY_A, anomalous=True, z_score=3.0)]
    )

    assert two_anomalies.score >= one_anomaly.score


def test_increasing_deviation_magnitude_does_not_decrease_score():
    calculator = BehaviorShiftCalculator()

    small = calculator.calculate([dev(KEY_A, anomalous=True, z_score=3.0)])
    large = calculator.calculate([dev(KEY_A, anomalous=True, z_score=6.0)])

    assert large.score >= small.score


def test_peak_magnitude_caps_at_one():
    outer = BehaviorShiftCalculator().calculate(
        [dev(KEY_A, anomalous=True, z_score=4.0)]
    )
    capped = BehaviorShiftCalculator().calculate(
        [dev(KEY_A, anomalous=True, z_score=12.0)]
    )

    assert outer.score == 1.0
    assert capped.score == 1.0


def test_contribution_accounting_equals_score():
    calculator = BehaviorShiftCalculator()

    deviations = [
        dev(KEY_A, anomalous=True, z_score=3.0),
        dev(KEY_A, anomalous=True, z_score=6.0),
        dev(KEY_B, anomalous=True, z_score=3.0),
        dev(KEY_B, z_score=0.0),
    ]

    shift = calculator.calculate(deviations)

    assert shift.score == 0.0 or shift.score == (
        sum(c.contribution for c in shift.contributions) / len(shift.contributions)
    )
    assert sum(c.contribution for c in shift.contributions) / len(shift.contributions) == shift.score


def test_contributions_are_per_metric():
    shift = BehaviorShiftCalculator().calculate([dev(KEY_A, anomalous=True, z_score=3.0)])

    assert len(shift.contributions) == 1
    contribution = shift.contributions[0]
    assert contribution.key == KEY_A
    assert contribution.anomaly_rate == 1.0
    assert contribution.peak_magnitude == 0.75
    assert contribution.contribution == 0.75


def test_anomaly_rate_uses_total_observations_per_metric():
    shift = BehaviorShiftCalculator().calculate(
        [
            dev(KEY_A, anomalous=True, z_score=3.0),
            dev(KEY_A, anomalous=True, z_score=3.0),
            dev(KEY_A, z_score=0.0),
            dev(KEY_A, z_score=0.0),
        ]
    )

    contribution = shift.contributions[0]
    assert contribution.anomaly_rate == 0.5
    assert contribution.peak_magnitude == 0.75
    assert contribution.contribution == 0.375


def test_change_detected_flag_reflects_change_points():
    from src.intelligence.models import ChangePoint

    point = ChangePoint(
        start_time=DEFAULT_START,
        end_time=DEFAULT_START + timedelta(seconds=10),
        consecutive_batches=2,
        affected_metric_keys=[KEY_A],
        source_record_ids=[],
        explanation="Sustained anomalous behavior.",
    )

    shift = BehaviorShiftCalculator().calculate([], change_points=[point])

    assert shift.change_detected is True
    assert shift.score == 0.0


def test_score_is_reproducible():
    deviations = [dev(KEY_A, anomalous=True, z_score=3.0), dev(KEY_A, z_score=0.0)]

    first = BehaviorShiftCalculator().calculate(deviations)
    second = BehaviorShiftCalculator().calculate(deviations)

    assert first.model_dump() == second.model_dump()


def test_explanation_reports_sources():
    shift = BehaviorShiftCalculator().calculate([dev(KEY_A, anomalous=True, z_score=3.0)])

    assert "Behavior Shift Score" in shift.explanation
    assert "api-service" in shift.explanation
    assert "latency_ms" in shift.explanation