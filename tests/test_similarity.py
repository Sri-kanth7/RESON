"""Tests for deterministic historical incident similarity and matching."""

import pytest

from src.data.schemas import EventType
from src.incidents.correlation import CorrelationEngine, SignalBuilder
from src.incidents.detector import IncidentDetector
from src.incidents.models import IncidentHistory, IncidentSimilarity
from src.incidents.similarity import IncidentMatcher
from tests.incident_test_helpers import (
    PAYMENT_KEY,
    make_deployment,
    make_deviation,
    make_event,
)
from tests.incident_test_helpers import ts


def build_incident(deviations=(), events=(), deployments=()):
    signals = SignalBuilder().build(
        deviations=deviations,
        events=events,
        deployments=deployments,
    )
    _, evidence = CorrelationEngine().correlate(signals)
    detector = IncidentDetector()
    incidents, chain, _ = detector.detect(signals, evidence)
    incident = incidents[0]
    return incident, signals, chain[incident.id]


def history_for(incident, signals, evidence):
    return IncidentHistory(
        incident=incident,
        evidence=evidence,
        signals=signals,
    )


def test_identical_incidents_have_high_similarity():
    matcher = IncidentMatcher()
    devs_a = [
        make_deviation(10, seed="id-a1"),
        make_deviation(20, seed="id-a2"),
    ]
    evs_a = [make_event(10, seed="id-e1")]
    current, current_signals, current_evidence = build_incident(devs_a, evs_a)
    # Identical structure at a different time.
    devs_b = [
        make_deviation(5000, seed="id-b1"),
        make_deviation(5010, seed="id-b2"),
    ]
    evs_b = [make_event(5000, seed="id-e2")]
    historical, historical_signals, historical_evidence = build_incident(devs_b, evs_b)

    similarity = matcher.similarity(
        current=current,
        historical=historical,
        current_signals=current_signals,
        historical_signals=historical_signals,
    )
    assert similarity.service_overlap == 1.0
    assert similarity.metric_similarity == 1.0
    assert similarity.event_sequence_similarity == 1.0
    assert similarity.temporal_similarity == 1.0
    # Deployment-free, scenario-free incidents share every measurable factor;
    # the two absent factors (deployment 0.10, scenario 0.10) leave 0.8 as the
    # documented maximum.
    assert similarity.overall_similarity == pytest.approx(0.8)


def test_partially_similar_incidents_have_intermediate_score():
    matcher = IncidentMatcher()
    current, current_signals, current_evidence = build_incident(
        deviations=[
            make_deviation(10, seed="ps-a1"),
            make_deviation(20, seed="ps-a2"),
        ],
        events=[make_event(10, seed="ps-e1")],
    )
    # Same service/metrics but a different event type and different duration.
    historical, historical_signals, historical_evidence = build_incident(
        deviations=[
            make_deviation(100, seed="ps-b1"),
            make_deviation(130, seed="ps-b2"),
        ],
        events=[make_event(100, event_type=EventType.DATABASE_PRESSURE, seed="ps-e2")],
    )
    similarity = matcher.similarity(
        current=current,
        historical=historical,
        current_signals=current_signals,
        historical_signals=historical_signals,
    )
    identical = matcher.similarity(
        current=current,
        historical=current,
        current_signals=current_signals,
        historical_signals=current_signals,
    )
    assert 0.0 < similarity.overall_similarity < identical.overall_similarity


def test_unrelated_incidents_have_low_similarity():
    matcher = IncidentMatcher()
    current, current_signals, _ = build_incident(
        deviations=[make_deviation(10, seed="un-a1")],
    )
    historical, historical_signals, _ = build_incident(
        deviations=[
            make_deviation(60_000, key=PAYMENT_KEY, seed="un-b1"),
            make_deviation(60_010, key=PAYMENT_KEY, seed="un-b2"),
        ],
    )
    similarity = matcher.similarity(
        current=current,
        historical=historical,
        current_signals=current_signals,
        historical_signals=historical_signals,
    )
    assert similarity.service_overlap == 0.0
    assert similarity.overall_similarity < 0.2


def test_similarity_is_deterministic():
    matcher = IncidentMatcher()
    current, current_signals, _ = build_incident(
        deviations=[make_deviation(10, seed="det-1")],
        events=[make_event(10, seed="det-e")],
    )
    historical, historical_signals, _ = build_incident(
        deviations=[make_deviation(400, seed="det-2")],
        events=[make_event(400, seed="det-f")],
    )
    first = matcher.similarity(
        current=current,
        historical=historical,
        current_signals=current_signals,
        historical_signals=historical_signals,
    )
    second = matcher.similarity(
        current=current,
        historical=historical,
        current_signals=current_signals,
        historical_signals=historical_signals,
    )
    assert first.model_dump() == second.model_dump()


def test_deployment_relationship_factor():
    matcher = IncidentMatcher()
    current, current_signals, _ = build_incident(
        deviations=[make_deviation(20, seed="dep-c2")],
        deployments=[make_deployment(10, seed="dep-c")],
    )
    historical, historical_signals, _ = build_incident(
        deviations=[make_deviation(500, seed="dep-h2")],
        deployments=[make_deployment(490, seed="dep-h")],
    )
    similarity = matcher.similarity(
        current=current,
        historical=historical,
        current_signals=current_signals,
        historical_signals=historical_signals,
    )
    assert similarity.deployment_relationship == 1.0


def test_scenario_match_is_an_explicit_factor():
    matcher = IncidentMatcher()
    current, current_signals, _ = build_incident(
        deviations=[make_deviation(10, seed="sc-1")],
    )
    historical, historical_signals, _ = build_incident(
        deviations=[make_deviation(100, seed="sc-2")],
    )
    similarity = matcher.similarity(
        current=current,
        historical=historical,
        current_signals=current_signals,
        historical_signals=historical_signals,
    )
    assert isinstance(similarity, IncidentSimilarity)
    assert 0.0 <= similarity.scenario_match <= 1.0
    assert 0.0 <= similarity.overall_similarity <= 1.0


def test_all_factors_identical_reaches_overall_one():
    # Case A: every factor available and identical -> the weighted combination
    # is mathematically capable of 1.0 (deployment and scenario are NOT absent).
    from src.data.schemas import Deployment, DeploymentStatus
    from tests.incident_test_helpers import fixed_uuid

    def with_deployment_scenario(seconds, seed):
        incident, signals, evidence = build_incident(
            deviations=[
                make_deviation(seconds, seed=f"{seed}-d1"),
                make_deviation(seconds + 10, seed=f"{seed}-d2"),
            ],
            events=[make_event(seconds, seed=f"{seed}-e")],
            deployments=[
                Deployment(
                    id=fixed_uuid(f"{seed}-dep"),
                    timestamp=ts(seconds),
                    service="api-service",
                    environment="development",
                    version="2.0.0",
                    status=DeploymentStatus.SUCCESS,
                    metadata={"scenario": "deployment_regression"},
                )
            ],
        )
        return incident, signals, evidence

    current, current_signals, _ = with_deployment_scenario(10, "id-a")
    historical, historical_signals, _ = with_deployment_scenario(5000, "id-b")

    matcher = IncidentMatcher()
    similarity = matcher.similarity(
        current=current,
        historical=historical,
        current_signals=current_signals,
        historical_signals=historical_signals,
    )
    assert similarity.service_overlap == 1.0
    assert similarity.metric_similarity == 1.0
    assert similarity.event_sequence_similarity == 1.0
    assert similarity.deployment_relationship == 1.0
    assert similarity.temporal_similarity == 1.0
    assert similarity.scenario_match == 1.0
    assert similarity.overall_similarity == pytest.approx(1.0)


def test_matches_exclude_current_incident():
    matcher = IncidentMatcher()
    current, current_signals, current_evidence = build_incident(
        deviations=[make_deviation(10, seed="ex-1")],
    )
    other, other_signals, other_evidence = build_incident(
        deviations=[make_deviation(5000, seed="ex-2")],
    )
    candidates = [
        history_for(current, current_signals, current_evidence),
        history_for(other, other_signals, other_evidence),
    ]
    matches = matcher.find_matches(
        current=current,
        current_signals=current_signals,
        current_evidence=current_evidence,
        candidates=candidates,
    )
    assert len(matches) == 1
    assert matches[0].historical_incident_id == other.id


def test_matches_order_by_overall_then_stable_id():
    matcher = IncidentMatcher()
    main, main_signals, main_evidence = build_incident(
        deviations=[make_deviation(10, seed="ord-1")],
    )
    similar, similar_signals, similar_evidence = build_incident(
        deviations=[make_deviation(500, seed="ord-2")],
    )
    distant, distant_signals, distant_evidence = build_incident(
        deviations=[make_deviation(60_000, seed="ord-3")],
        events=[make_event(60_000, event_type=EventType.DATABASE_PRESSURE, seed="ord-e")],
    )
    matches = matcher.find_matches(
        current=main,
        current_signals=main_signals,
        current_evidence=main_evidence,
        candidates=[
            history_for(distant, distant_signals, distant_evidence),
            history_for(similar, similar_signals, similar_evidence),
        ],
    )
    scores = [match.similarity.overall_similarity for match in matches]
    assert scores == sorted(scores, reverse=True)
    assert matches[0].historical_incident_id == similar.id


def test_no_historical_matches_returns_empty():
    matcher = IncidentMatcher()
    current, current_signals, current_evidence = build_incident(
        deviations=[make_deviation(10, seed="none-1")],
    )
    matches = matcher.find_matches(
        current=current,
        current_signals=current_signals,
        current_evidence=current_evidence,
        candidates=[],
    )
    assert matches == []


def test_historical_ids_and_timeline_are_preserved():
    matcher = IncidentMatcher()
    current, current_signals, current_evidence = build_incident(
        deviations=[make_deviation(10, seed="pres-1")],
        events=[make_event(10, seed="pres-e")],
    )
    historical, historical_signals, historical_evidence = build_incident(
        deviations=[make_deviation(500, seed="pres-2")],
        events=[make_event(500, seed="pres-f")],
    )
    matches = matcher.find_matches(
        current=current,
        current_signals=current_signals,
        current_evidence=current_evidence,
        candidates=[
            history_for(historical, historical_signals, historical_evidence),
        ],
    )
    match = matches[0]
    assert match.historical_incident_id == historical.id
    assert match.historical_incident.id == historical.id
    assert match.historical_timeline == historical_evidence