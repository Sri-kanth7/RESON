"""Tests for the deterministic incident detector and severity policy."""

from src.data.schemas import IncidentStatus, LogLevel
from src.intelligence.models import BaselineKey, ChangePoint
from src.incidents.correlation import CorrelationEngine, SignalBuilder
from src.incidents.detector import IncidentDetector, SeverityPolicy
from src.incidents.models import IncidentIntelligenceConfig, Signal
from tests.incident_test_helpers import (
    PAYMENT_KEY,
    make_deployment,
    make_deviation,
    make_event,
    make_log,
)
from tests.incident_test_helpers import ts


def detect(deviations=(), logs=(), events=(), deployments=(), change_points=None, window_start=None, window_end=None):
    signals = SignalBuilder().build(
        deviations=deviations,
        events=events,
        logs=logs,
        deployments=deployments,
        change_points=change_points or (),
    )
    _, evidence = CorrelationEngine().correlate(signals)
    return IncidentDetector().detect(
        signals,
        evidence,
        window_start=window_start,
        window_end=window_end,
    )


def test_single_incident_from_related_evidence():
    incidents, chain, _ = detect(
        deviations=[
            make_deviation(10, seed="si-1"),
            make_deviation(20, seed="si-2"),
        ],
        window_start=ts(0),
        window_end=ts(60),
    )
    assert len(incidents) == 1
    incident = incidents[0]
    assert incident.affected_services == ["api-service"]
    assert incident.start_time == ts(10)
    assert incident.end_time == ts(20)
    assert incident.status == IncidentStatus.OPEN.value
    assert len(chain[incident.id]) == 2


def test_multiple_separate_incidents_stay_separate():
    incidents, _, _ = detect(
        deviations=[
            make_deviation(10, seed="ms-1"),
            make_deviation(20, seed="ms-2"),
            make_deviation(60_000, key=PAYMENT_KEY, seed="ms-3"),
            make_deviation(60_010, key=PAYMENT_KEY, seed="ms-4"),
        ],
    )
    assert len(incidents) == 2
    starts = sorted(incident.start_time for incident in incidents)
    assert starts == [ts(10), ts(60_000)]


def test_multi_service_incident():
    incidents, chain, _ = detect(
        deviations=[
            make_deviation(10, seed="multi-1"),
            make_deviation(20, key=PAYMENT_KEY, seed="multi-2"),
        ],
    )
    assert len(incidents) == 1
    assert incidents[0].affected_services == ["api-service", "payment-service"]


def test_isolated_anomaly_remains_a_standalone_incident():
    incidents, chain, _ = detect(
        deviations=[
            make_deviation(30, seed="iso-1"),
        ],
    )
    assert len(incidents) == 1
    assert len(chain[incidents[0].id]) == 1


def test_far_apart_same_service_anomalies_stay_separate():
    # Hours-apart anomalies on the same service share context but no temporal
    # relationship; they must not become a single day-long incident.
    incidents, _, _ = detect(
        deviations=[
            make_deviation(10, seed="far-1"),
            make_deviation(7210, seed="far-2"),
        ],
    )
    assert len(incidents) == 2
    starts = sorted(incident.start_time for incident in incidents)
    assert starts == [ts(10), ts(7210)]


def test_transitive_correlation_merges_into_one_incident():
    # A-B correlated, B-C correlated, A-C not directly correlated: union-find
    # must transitively group A, B, C into one incident.
    incidents, chain, _ = detect(
        deviations=[
            make_deviation(10, seed="tr-a"),
            make_deviation(60, seed="tr-b"),
            make_deviation(110, seed="tr-c"),
        ],
    )
    assert len(incidents) == 1
    assert len(chain[incidents[0].id]) == 3
    assert incidents[0].start_time == ts(10)
    assert incidents[0].end_time == ts(110)


def test_unrelated_cluster_stays_separate():
    # A-B correlated; C is hours away and shares no temporal relationship.
    incidents, _, _ = detect(
        deviations=[
            make_deviation(10, seed="sep-a"),
            make_deviation(50, seed="sep-b"),
            make_deviation(7210, seed="sep-c"),
        ],
    )
    assert len(incidents) == 2
    starts = sorted(incident.start_time for incident in incidents)
    assert starts == [ts(10), ts(7210)]


def test_duplicate_evidence_does_not_duplicate_incidents():
    signals = SignalBuilder().build(
        deviations=[
            make_deviation(10, seed="dup-1"),
            make_deviation(20, seed="dup-2"),
        ],
    )
    _, evidence = CorrelationEngine().correlate(signals)
    repeated = evidence + [evidence[0]]  # a stray duplicate edge
    incidents, chain, _ = IncidentDetector().detect(signals, repeated)
    assert len(incidents) == 1
    assert len(chain[incidents[0].id]) == 2


def test_sustained_anomaly_change_point_produces_incident():
    key = BaselineKey(
        service="api-service",
        environment="development",
        metric_name="latency_ms",
    )
    point = ChangePoint(
        start_time=ts(10),
        end_time=ts(30),
        consecutive_batches=2,
        affected_metric_keys=[key],
        source_record_ids=[make_deviation(10, seed="cp-1").record_id],
        explanation="sustained anomalous behavior",
    )
    incidents, chain, _ = detect(change_points=[point])
    assert len(incidents) == 1
    # The change-point node is timestamped at its start; the incident end is
    # the latest supporting evidence timestamp (the sustained window is
    # preserved in the signal payload).
    assert incidents[0].start_time == ts(10)
    assert incidents[0].end_time == ts(10)
    assert len(chain[incidents[0].id]) == 1


def test_overlapping_evidence_merges_into_one_incident():
    incidents, _, _ = detect(
        deviations=[
            make_deviation(10, seed="ov-1"),
            make_deviation(25, seed="ov-2"),
            make_deviation(40, seed="ov-3"),
        ],
    )
    assert len(incidents) == 1
    assert incidents[0].start_time == ts(10)
    assert incidents[0].end_time == ts(40)


def test_context_only_evidence_never_forms_incident():
    incidents, _, _ = detect(
        deployments=[make_deployment(10, seed="ctx-1")],
        logs=[
            make_log(20, level=LogLevel.INFO, seed="ctx-2"),
        ],
    )
    assert incidents == []


def test_incident_within_half_open_window_boundary():
    incidents, _, _ = detect(
        deviations=[
            make_deviation(10, seed="wb-1"),
            make_deviation(30, seed="wb-2"),
        ],
        window_start=ts(20),
        window_end=ts(60),
    )
    # ts(10) is outside [ts(20), ts(60)) and must be excluded.
    assert len(incidents) == 1
    assert incidents[0].start_time == ts(30)


def test_severity_deterministic_and_explainable():
    policy = SeverityPolicy()
    signals = [
        Signal(
            source_id=make_deviation(10, seed="sv-1").record_id,
            source_type="metric",
            timestamp=ts(10),
            service="api-service",
            environment="development",
            description="metric spike",
            strength=1.0,
            trigger=True,
            sustained=False,
        )
    ]
    first = policy.classify(signals, shift_magnitude=0.0)
    second = policy.classify(signals, shift_magnitude=0.0)
    assert first == second
    assert first[0].value == "low"
    assert "base severity LOW" in first[1]


def test_severity_shift_boundary():
    policy = SeverityPolicy()
    threshold = IncidentIntelligenceConfig().severity_shift_threshold
    signals = [
        Signal(
            source_id=make_deviation(10, seed="sb-1").record_id,
            source_type="metric",
            timestamp=ts(10),
            service="api-service",
            environment="development",
            description="metric spike",
            strength=1.0,
            trigger=True,
        )
    ]
    at_threshold = policy.classify(signals, shift_magnitude=threshold)
    below = policy.classify(signals, shift_magnitude=threshold - 0.01)
    assert at_threshold[0].value == "medium"
    assert below[0].value == "low"


def test_severity_escalates_for_multi_service_and_critical_evidence():
    policy = SeverityPolicy()
    signals = [
        Signal(
            source_id=make_deviation(10, seed="se-1").record_id,
            source_type="metric",
            timestamp=ts(10),
            service="api-service",
            environment="development",
            description="metric spike",
            strength=1.0,
            trigger=True,
            sustained=True,
        ),
        Signal(
            source_id=make_deviation(20, key=PAYMENT_KEY, seed="se-2").record_id,
            source_type="metric",
            timestamp=ts(20),
            service="payment-service",
            environment="development",
            description="metric spike",
            strength=1.0,
            trigger=True,
            sustained=True,
        ),
    ]
    severity, explanation = policy.classify(signals, shift_magnitude=0.7)
    assert severity.value == "critical"
    assert "behavior shift magnitude" in explanation
    assert "affected services" in explanation
    assert "change-point" in explanation


def test_incident_end_is_latest_supporting_evidence():
    incidents, chain, _ = detect(
        deviations=[
            make_deviation(5, seed="en-1"),
            make_deviation(50, seed="en-2"),
        ],
    )
    incident = incidents[0]
    assert incident.end_time == ts(50)
    assert incident.duration_seconds == 45.0