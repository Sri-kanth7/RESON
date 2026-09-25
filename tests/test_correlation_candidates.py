"""Tests for preserved structured correlation candidates.

Phase 3.1 keeps the ``CorrelationCandidate`` objects the correlation engine
already produced instead of discarding them. These tests pin down that the
preserved output is complete, structured, deterministic, and that it agrees
with the evidence that was already being produced.
"""

import pytest

from src.incidents.models import (
    CorrelationCandidate,
    IncidentIntelligenceResult,
    content_id,
)
from src.incidents.service import IncidentIntelligenceService
from tests.incident_test_helpers import (
    fixed_uuid,
    make_deployment,
    make_deviation,
    make_event,
    make_log,
    ts,
)
from src.data.schemas import LogLevel


def analyze(**kwargs):
    return IncidentIntelligenceService().analyze(**kwargs)


def test_candidates_are_returned():
    result = analyze(
        deviations=[
            make_deviation(10, seed="cc-1"),
            make_deviation(20, seed="cc-2"),
        ],
    )
    assert result.correlation_candidates
    assert all(
        isinstance(item, CorrelationCandidate)
        for item in result.correlation_candidates
    )


def test_empty_input_returns_no_candidates():
    result = analyze()
    assert result.correlation_candidates == []
    assert result.correlation_evidence == []


def test_single_signal_evaluates_no_pairs():
    result = analyze(deviations=[make_deviation(10, seed="one-1")])
    assert result.correlation_candidates == []


def test_every_distinct_pair_is_evaluated_exactly_once():
    # Three signals -> three unordered pairs, none repeated.
    result = analyze(
        deviations=[
            make_deviation(10, seed="pair-1"),
            make_deviation(20, seed="pair-2"),
        ],
        events=[make_event(15, seed="pair-e")],
    )
    pairs = [
        frozenset((item.source.source_id, item.target.source_id))
        for item in result.correlation_candidates
    ]
    assert len(result.correlation_candidates) == 3
    assert len(set(pairs)) == 3


def test_structured_factors_are_preserved():
    result = analyze(
        deviations=[make_deviation(10, seed="f-1")],
        events=[make_event(20, seed="f-e")],
    )
    candidate = next(
        item for item in result.correlation_candidates if item.accepted
    )
    # Same service, same environment, 10 seconds apart over a 600s window.
    assert candidate.same_service == 1.0
    assert candidate.same_environment == 1.0
    assert candidate.temporal_proximity == pytest.approx(1.0 - 10.0 / 600.0)
    assert candidate.event_link == 1.0
    # No deployment participates, so deployment proximity is not computable.
    assert candidate.deployment_proximity == 0.0
    # The event carries no metric keys, so metric overlap is not computable.
    assert candidate.metric_overlap == 0.0
    assert candidate.service_relationship == "same"
    assert candidate.environment_relationship == "same"
    assert 0.0 <= candidate.strength <= 1.0
    assert candidate.explanation


def test_metric_overlap_is_preserved_when_both_sides_carry_keys():
    result = analyze(
        deviations=[
            make_deviation(10, seed="mo-1"),
            make_deviation(20, seed="mo-2"),
        ],
    )
    candidate = next(
        item for item in result.correlation_candidates if item.accepted
    )
    assert candidate.metric_overlap == 1.0


def test_rejected_candidates_are_retained_with_their_reason():
    # Same service and environment, but far outside the correlation window, so
    # the pair is rejected even though it shares full static context.
    result = analyze(
        deviations=[make_deviation(10, seed="r-1")],
        events=[make_event(60_000, seed="r-e")],
    )
    rejected = [
        item for item in result.correlation_candidates if not item.accepted
    ]
    assert rejected
    far = next(
        item
        for item in rejected
        if {item.source.source_id, item.target.source_id}
        == {fixed_uuid("r-1"), fixed_uuid("r-e")}
    )
    assert far.temporal_proximity == 0.0
    assert far.same_service == 1.0
    assert far.same_environment == 1.0
    assert "rejected" in far.explanation
    # A rejected pair is never materialized as evidence.
    materialized = {
        (item.source_id, item.target_id)
        for item in result.correlation_evidence
    }
    assert (far.source.source_id, far.target.source_id) not in materialized


def test_candidates_superset_of_accepted_evidence():
    result = analyze(
        deviations=[
            make_deviation(10, seed="sup-1"),
            make_deviation(20, seed="sup-2"),
        ],
        events=[
            make_event(15, seed="sup-e"),
            # Far outside the correlation window, so this pair is evaluated and
            # rejected rather than silently never considered.
            make_event(60_000, seed="sup-e2"),
        ],
    )
    accepted_evidence = {
        (item.source_id, item.target_id)
        for item in result.correlation_evidence
    }
    accepted_candidates = {
        (item.source.source_id, item.target.source_id)
        for item in result.correlation_candidates
        if item.accepted
    }
    assert accepted_candidates
    assert accepted_candidates == accepted_evidence
    # Rejected pairs add information the evidence list alone cannot express.
    assert len(result.correlation_candidates) > len(accepted_candidates)
    assert any(
        not item.accepted for item in result.correlation_candidates
    )


def test_preserved_candidates_agree_with_existing_evidence_identifiers():
    # Proves no calculation, threshold or identifier changed: each accepted
    # candidate reproduces the exact Evidence id and strength already emitted.
    result = analyze(
        deviations=[
            make_deviation(10, seed="ag-1"),
            make_deviation(20, seed="ag-2"),
        ],
        events=[make_event(15, seed="ag-e")],
    )
    evidence_by_id = {item.id: item for item in result.correlation_evidence}
    matched = 0
    for candidate in result.correlation_candidates:
        if not candidate.accepted:
            continue
        expected = content_id(
            "evidence",
            candidate.relationship_type,
            candidate.source.source_id,
            candidate.target.source_id,
            candidate.timestamp,
        )
        assert expected in evidence_by_id
        assert evidence_by_id[expected].strength == pytest.approx(
            candidate.strength
        )
        matched += 1
    assert matched == len(evidence_by_id)


def test_candidate_ordering_is_deterministic_across_runs():
    kwargs = dict(
        deviations=[
            make_deviation(10, seed="d-1"),
            make_deviation(20, seed="d-2"),
        ],
        events=[make_event(15, seed="d-e")],
        logs=[make_log(18, level=LogLevel.ERROR, seed="d-l")],
    )
    first = analyze(**kwargs).correlation_candidates
    second = analyze(**kwargs).correlation_candidates
    assert [item.model_dump() for item in first] == [
        item.model_dump() for item in second
    ]


def test_candidate_ordering_is_independent_of_input_order():
    deviations = [
        make_deviation(10, seed="o-1"),
        make_deviation(20, seed="o-2"),
    ]
    events = [make_event(15, seed="o-e")]
    deployments = [make_deployment(22, service="api-service", seed="o-d")]

    forward = analyze(
        deviations=deviations,
        events=events,
        deployments=deployments,
    ).correlation_candidates
    shuffled = analyze(
        deviations=list(reversed(deviations)),
        events=list(reversed(events)),
        deployments=list(reversed(deployments)),
    ).correlation_candidates

    assert [item.model_dump() for item in forward] == [
        item.model_dump() for item in shuffled
    ]


def test_candidates_carry_the_signal_payload_needed_for_explanation():
    result = analyze(
        deviations=[make_deviation(10, seed="pl-1")],
        deployments=[make_deployment(15, service="api-service", seed="pl-d")],
    )
    candidate = next(
        item for item in result.correlation_candidates if item.accepted
    )
    # Each candidate embeds the full source and target signals, so a consumer
    # can explain the decision without a second lookup.
    assert candidate.source.source_type
    assert candidate.source.service == "api-service"
    assert candidate.target.source_type
    assert candidate.timestamp == min(
        candidate.source.timestamp,
        candidate.target.timestamp,
    )


def test_candidates_default_to_empty_on_bare_construction():
    result = IncidentIntelligenceResult()
    assert result.correlation_candidates == []
    assert result.correlation_evidence == []


def test_candidates_are_independent_of_the_detection_window():
    # Correlation runs over the full signal set; the analysis window is applied
    # later, at detection. Preserving candidates must not move that boundary,
    # so the candidate list is identical with and without a window.
    kwargs = dict(
        deviations=[
            make_deviation(10, seed="w-1"),
            make_deviation(20, seed="w-2"),
        ],
        events=[make_event(60_000, seed="w-e")],
    )
    unwindowed = analyze(**kwargs).correlation_candidates
    windowed = analyze(
        **kwargs,
        window_start=ts(15),
        window_end=ts(21),
    ).correlation_candidates

    assert [item.model_dump() for item in unwindowed] == [
        item.model_dump() for item in windowed
    ]
    # The window still governs which signals reach the incident corpus.
    assert len(unwindowed) == 3
    windowed_research = analyze(
        **kwargs, window_start=ts(15), window_end=ts(21)
    ).researches[0]
    assert windowed_research.incident.start_time == ts(20)
