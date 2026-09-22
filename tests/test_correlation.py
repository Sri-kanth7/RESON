"""Tests for the deterministic evidence correlation engine."""

from src.data.schemas import RelationshipType
from src.intelligence.models import BaselineKey
from src.incidents.correlation import CorrelationEngine, SignalBuilder
from src.incidents.models import IncidentIntelligenceConfig
from tests.incident_test_helpers import (
    PAYMENT_KEY,
    make_deployment,
    make_deviation,
    make_event,
)
from tests.incident_test_helpers import T0, ts

OTHER_KEY = BaselineKey(
    service="worker-service",
    environment="development",
    metric_name="latency_ms",
)

EVENT_TYPE_MAKERS = []


def build_signals(deviations=(), logs=(), events=(), deployments=()):
    return SignalBuilder().build(
        deviations=deviations,
        logs=logs,
        events=events,
        deployments=deployments,
    )


def accepted(engine, signals):
    _, evidence = engine.correlate(signals)
    return evidence


def test_temporal_correlation_accepted():
    engine = CorrelationEngine()
    signals = build_signals(
        deviations=[
            make_deviation(10, seed="tc-a"),
            make_deviation(20, seed="tc-b"),
        ]
    )
    evidence = accepted(engine, signals)
    assert len(evidence) == 1
    assert evidence[0].relationship_type == RelationshipType.CORRELATION.value


def test_same_service_correlation_is_correlation_type():
    engine = CorrelationEngine()
    signals = build_signals(
        deviations=[
            make_deviation(10, seed="ss-a"),
            make_deviation(30, seed="ss-b"),
        ]
    )
    candidate, _ = engine.correlate(signals)
    pair = candidate[0]
    assert pair.relationship_type == RelationshipType.CORRELATION
    assert pair.service_relationship == "same"
    assert pair.temporal_proximity > 0.0


def test_cross_service_temporal_association():
    engine = CorrelationEngine()
    signals = build_signals(
        deviations=[
            make_deviation(10, seed="xs-a"),
        ],
        events=[
            make_event(30, service="database-service", environment="production", seed="xs-b"),
        ],
    )
    candidate, evidence = engine.correlate(signals)
    assert len(evidence) == 1
    assert candidate[0].relationship_type == RelationshipType.TEMPORAL
    assert candidate[0].service_relationship == "cross"


def test_unrelated_evidence_not_correlated():
    engine = CorrelationEngine()
    signals = build_signals(
        deviations=[
            make_deviation(10, seed="un-a"),
            make_deviation(60_000, key=OTHER_KEY, seed="un-b"),
        ]
    )
    candidate, evidence = engine.correlate(signals)
    assert evidence == []
    assert all(item.accepted is False for item in candidate)


def test_boundary_timestamps_have_zero_temporal_proximity():
    window = IncidentIntelligenceConfig().correlation_window_seconds
    engine = CorrelationEngine()
    signals = build_signals(
        events=[
            make_event(0, seed="bd-a"),
            # A cross-service, cross-context pair at exactly the window boundary
            # has no temporal proximity and no shared context.
            make_event(
                int(window),
                service="database-service",
                environment="production",
                seed="bd-b",
            ),
        ]
    )
    candidate, evidence = engine.correlate(signals)
    assert evidence == []
    assert candidate[0].temporal_proximity == 0.0


def test_far_apart_same_service_is_not_associated():
    # Same service, same environment, same metric — but hours apart. Static
    # context alone must never associate evidence that is not temporally close.
    engine = CorrelationEngine()
    signals = build_signals(
        deviations=[
            make_deviation(10, seed="far-a"),
            make_deviation(7210, seed="far-b"),
        ]
    )
    candidate, evidence = engine.correlate(signals)
    assert evidence == []
    assert all(item.accepted is False for item in candidate)
    assert candidate[0].temporal_proximity == 0.0
    assert candidate[0].strength >= 0.3  # strength alone is not enough


def test_far_apart_same_service_different_metric_is_not_associated():
    engine = CorrelationEngine()
    signals = build_signals(
        deviations=[
            make_deviation(10, seed="far-c"),
            make_deviation(7210, key=OTHER_KEY, seed="far-d"),
        ]
    )
    _, evidence = engine.correlate(signals)
    assert evidence == []


def test_duplicate_signal_ids_are_deduplicated():
    engine = CorrelationEngine()
    signals = build_signals(
        deviations=[
            make_deviation(10, seed="dup-a"),
            make_deviation(10, seed="dup-a"),
            make_deviation(25, seed="dup-b"),
        ]
    )
    assert len(signals) == 2
    _, evidence = engine.correlate(signals)
    assert len(evidence) == 1


def test_duplicate_evidence_prevention_materializes_pairs_once():
    engine = CorrelationEngine()
    signals = build_signals(
        deviations=[
            make_deviation(10, seed="de-a"),
            make_deviation(20, seed="de-b"),
            make_deviation(30, seed="de-c"),
        ]
    )
    _, evidence = engine.correlate(signals)
    pair_keys = {
        (str(item.source_id), str(item.target_id), item.relationship_type)
        for item in evidence
    }
    assert len(pair_keys) == len(evidence)
    assert len(pair_keys) == 3


def test_event_pair_semantic_is_event_sequence():
    engine = CorrelationEngine()
    signals = build_signals(
        events=[
            make_event(10, seed="es-a"),
            make_event(20, seed="es-b"),
        ]
    )
    candidate, evidence = engine.correlate(signals)
    assert len(evidence) == 1
    assert candidate[0].relationship_type == RelationshipType.EVENT_SEQUENCE


def test_deployment_proximity_binds_release_to_following_deviation():
    engine = CorrelationEngine()
    signals = build_signals(
        deployments=[make_deployment(10, seed="dep-a")],
        deviations=[
            make_deviation(30, key=PAYMENT_KEY, seed="de-p"),
        ],
    )
    _, evidence = engine.correlate(signals)
    assert len(evidence) == 1
    assert any(
        item.source_type == "deployment" or item.target_type == "deployment"
        for item in evidence
    )


def test_anomalous_deviation_is_a_trigger_signal():
    signals = build_signals(deviations=[make_deviation(10, seed="tr-a")])
    assert signals[0].trigger is True
    assert signals[0].strength == 1.0


def test_correlation_is_deterministic():
    engine = CorrelationEngine()
    signals = build_signals(
        deviations=[
            make_deviation(10, seed="det-a"),
            make_deviation(20, seed="det-b"),
            make_deviation(40, seed="det-c"),
        ]
    )
    _, first = engine.correlate(signals)
    _, second = engine.correlate(signals)
    assert [item.model_dump() for item in first] == [item.model_dump() for item in second]