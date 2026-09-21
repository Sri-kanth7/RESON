"""Tests for deterministic behavior fingerprint construction."""

from datetime import timedelta

from tests.conftest import DEFAULT_START, make_deployment, make_event, make_log, make_series

from src.intelligence.baseline import BaselineBuilder
from src.intelligence.change_detection import ChangeDetector
from src.intelligence.deviation import DeviationDetector
from src.intelligence.fingerprint import FingerprintBuilder
from src.intelligence.models import (
    BaselineKey,
    BehaviorState,
    DeviationStatus,
)
from src.preprocessing.normalizer import group_metrics

KEY = BaselineKey(service="api-service", environment="development", metric_name="latency_ms")
WINDOW_START = DEFAULT_START
WINDOW_END = DEFAULT_START + timedelta(seconds=40)


def build_inputs(history_values, current_values, change_points=()):
    history_series = group_metrics(make_series(history_values))[0]
    baseline = BaselineBuilder().build_baseline(history_series)

    current_series = group_metrics(make_series(current_values))[0]
    deviations = DeviationDetector().detect_series(current_series, {baseline.key: baseline})

    return {
        "series_by_key": {baseline.key: current_series},
        "deviations": deviations,
        "change_points": change_points,
    }


def test_fingerprint_is_deterministic():
    inputs = build_inputs([10.0, 20.0, 30.0], [10.0, 20.0, 30.0])

    first = FingerprintBuilder().build(WINDOW_START, WINDOW_END, **inputs)
    second = FingerprintBuilder().build(WINDOW_START, WINDOW_END, **inputs)

    assert first.model_dump() == second.model_dump()


def test_fingerprint_is_serializable():
    inputs = build_inputs([10.0, 20.0, 30.0], [10.0, 20.0, 30.0])

    payload = FingerprintBuilder().build(WINDOW_START, WINDOW_END, **inputs).model_dump()

    assert payload["start_time"] is not None
    assert payload["end_time"] is not None
    assert payload["services"] == ["api-service"]
    assert payload["environment"] == "development"
    assert payload["behavior_state"] == "normal"
    assert len(payload["metric_behaviors"]) == 1


def test_fingerprint_contains_metric_statistics():
    inputs = build_inputs([10.0, 20.0, 30.0], [10.0, 20.0, 30.0])

    fingerprint = FingerprintBuilder().build(WINDOW_START, WINDOW_END, **inputs)

    behavior = fingerprint.metric_behaviors[0]
    assert behavior.key == KEY
    assert behavior.observation_count == 3
    assert behavior.mean == 20.0
    assert behavior.anomaly_count == 0
    assert behavior.peak_abs_z == 1.0


def test_fingerprint_reports_anomalies_and_peak_z():
    inputs = build_inputs([10.0, 20.0, 30.0], [20.0, 80.0, 20.0])

    fingerprint = FingerprintBuilder().build(WINDOW_START, WINDOW_END, **inputs)

    behavior = fingerprint.metric_behaviors[0]
    assert behavior.anomaly_count == 1
    assert behavior.peak_abs_z == 6.0
    assert fingerprint.behavior_state == BehaviorState.DEVIATED


def test_fingerprint_behavior_state_changing_with_change_point():
    from src.intelligence.models import ChangePoint, MetricDeviation

    inputs = build_inputs([10.0, 20.0, 30.0], [80.0, 80.0, 80.0])
    point = ChangePoint(
        start_time=WINDOW_START,
        end_time=WINDOW_START + timedelta(seconds=10),
        consecutive_batches=2,
        affected_metric_keys=[KEY],
        source_record_ids=[
            inputs["deviations"][0].record_id,
            inputs["deviations"][1].record_id,
        ],
        explanation="Sustained anomalous behavior.",
    )

    fingerprint = FingerprintBuilder().build(
        WINDOW_START,
        WINDOW_END,
        series_by_key=inputs["series_by_key"],
        deviations=inputs["deviations"],
        change_points=[point],
    )

    assert fingerprint.behavior_state == BehaviorState.CHANGING
    assert fingerprint.change_point_count == 1


def test_fingerprint_includes_log_event_and_deployment_summaries():
    inputs = build_inputs([10.0, 20.0, 30.0], [10.0, 20.0, 30.0])

    fingerprint = FingerprintBuilder().build(
        WINDOW_START,
        WINDOW_END,
        **inputs,
        logs=[
            make_log(WINDOW_START, level="ERROR"),
            make_log(WINDOW_START + timedelta(seconds=10), level="INFO"),
            make_log(WINDOW_START + timedelta(seconds=20), level="ERROR"),
        ],
        events=[
            make_event(WINDOW_START, event_type="restart"),
        ],
        deployments=[
            make_deployment(WINDOW_START),
        ],
    )

    assert fingerprint.log_summary == {"ERROR": 2, "INFO": 1}
    assert fingerprint.event_summary == {"restart": 1}
    assert fingerprint.deployment_count == 1


def test_fingerprint_is_traceable_to_source_telemetry():
    current = make_series([20.0, 80.0, 20.0])
    history = make_series([10.0, 20.0, 30.0])

    history_series = group_metrics(history)[0]
    baseline = BaselineBuilder().build_baseline(history_series)

    current_series = group_metrics(current)[0]
    deviations = DeviationDetector().detect_series(current_series, {baseline.key: baseline})

    fingerprint = FingerprintBuilder().build(
        WINDOW_START,
        WINDOW_END,
        series_by_key={baseline.key: current_series},
        deviations=deviations,
    )

    assert set(fingerprint.source_record_ids) == {m.id for m in current}
    assert fingerprint.source_record_ids
    assert all(record_id in {m.id for m in current} for record_id in fingerprint.source_record_ids)


def test_fingerprint_multiple_services_and_environments():
    from src.intelligence.models import BaselineKey as BK

    current_metrics = (
        make_series([10.0, 20.0, 30.0], metric_name="latency_ms")
        + make_series([5.0, 6.0, 7.0], metric_name="cpu_usage", service="payment-service")
    )

    mapped = {
        BK(service=s.service, environment=s.environment, metric_name=s.metric_name): s
        for s in group_metrics(current_metrics)
    }

    fingerprint = FingerprintBuilder().build(
        WINDOW_START,
        WINDOW_END,
        series_by_key=mapped,
    )

    assert fingerprint.services == ["api-service", "payment-service"]
    assert fingerprint.environment == "development"