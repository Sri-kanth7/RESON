"""Tests for deterministic reconstruction of historical incident signals.

Phase 3.1 rebuilds the ``Signal`` objects behind a persisted incident's
evidence chain so the three signal-derived similarity factors are no longer
structurally forced to ``0.0`` by a signal set that was never loaded.

The suite runs fully offline: no Supabase, no ``.env``, no network.
"""

import pytest

from src.data.schemas import (
    Deployment,
    DeploymentStatus,
    Environment,
    EvidenceType,
    Incident,
)
from src.incidents.reconstruction import (
    HistoricalSignalContext,
    HistoricalSignalReconstructor,
    derive_deviation_evidence,
)
from src.incidents.service import IncidentIntelligenceService
from src.intelligence.models import (
    BaselineKey,
    BaselineStatistics,
    HistoricalBaseline,
    IntelligenceConfig,
)
from tests.incident_test_helpers import (
    FakeStorage,
    KEY,
    PAYMENT_KEY,
    fixed_uuid,
    make_event,
    make_metric,
    ts,
)

SCENARIO = "database_pressure"


def baseline_for(
    key: BaselineKey = KEY,
    *,
    mean: float = 100.0,
    std: float = 5.0,
) -> HistoricalBaseline:
    return HistoricalBaseline(
        key=key,
        start_time=ts(-600),
        end_time=ts(-1),
        statistics=BaselineStatistics(
            count=100,
            mean=mean,
            std=std,
        ),
        source_record_ids=[],
        explanation="test baseline",
    )


def scenario_deployment(
    seconds: int,
    *,
    service: str = "api-service",
    seed: str = "dep",
) -> Deployment:
    """A successful deployment carrying the scenario label under test."""
    return Deployment(
        id=fixed_uuid(seed),
        timestamp=ts(seconds),
        service=service,
        environment=Environment.DEVELOPMENT,
        version="2.0.0",
        status=DeploymentStatus.SUCCESS,
        metadata={"scenario": SCENARIO},
    )


def build_window(offset: int, seed: str):
    """Return telemetry describing one fully-evidenced incident at ``offset``.

    The shape is: two anomalous deviations, one escalation event, one
    successful deployment, then a normal deviation that resolves the incident.
    Every factor of the similarity contract is therefore genuinely available.
    """
    metrics = [
        make_metric(offset + 10, value=300.0, seed=f"{seed}-m1"),
        make_metric(offset + 20, value=305.0, seed=f"{seed}-m2"),
        make_metric(offset + 40, value=101.0, seed=f"{seed}-m3"),
    ]
    events = [make_event(offset + 25, seed=f"{seed}-e")]
    deployments = [scenario_deployment(offset + 30, seed=f"{seed}-dep")]
    return metrics, events, deployments


def analyze_window(
    service: IncidentIntelligenceService,
    *,
    offset: int = 0,
    seed: str = "hist",
    baselines=None,
    extra_metrics=(),
    window: tuple[int, int] = (0, 100),
):
    """Analyze one fully-evidenced incident window without persisting it."""
    baselines = baselines if baselines is not None else {KEY: baseline_for()}
    metrics, events, deployments = build_window(offset, seed)
    metrics = list(metrics) + list(extra_metrics)

    window_start, window_end = ts(window[0]), ts(window[1])
    deviations, change_points = service.derive_evidence(
        metrics,
        baselines,
        window_start=window_start,
        window_end=window_end,
    )
    result = service.analyze(
        deviations=deviations,
        change_points=change_points,
        events=events,
        deployments=deployments,
        window_start=window_start,
        window_end=window_end,
    )
    return result, metrics, events, deployments, baselines


def persist_historical_incident(
    service: IncidentIntelligenceService,
    **kwargs,
):
    """Analyze and persist one historical incident, returning its inputs."""
    result, metrics, events, deployments, baselines = analyze_window(
        service, **kwargs
    )
    service.persist(result)
    return result, metrics, events, deployments, baselines


# ----------------------------------------------------------------------
# Historical signal reconstruction
# ----------------------------------------------------------------------


def test_historical_signals_are_reconstructed_from_the_stored_chain():
    storage = FakeStorage()
    service = IncidentIntelligenceService(storage=storage)
    _, metrics, events, deployments, baselines = persist_historical_incident(
        service
    )

    context = HistoricalSignalContext(
        metrics=metrics,
        events=events,
        deployments=deployments,
        baselines=baselines,
    )
    candidates = service.historical_candidates(signal_context=context)

    assert len(candidates) == 1
    signals = candidates[0].signals
    # 2 anomalous deviations + 1 change point + 1 event + 1 deployment
    # + 1 normal (recovery) deviation.
    assert len(signals) == 6
    assert {item.source_type for item in signals} == {
        EvidenceType.METRIC.value,
        EvidenceType.EVENT.value,
        EvidenceType.DEPLOYMENT.value,
    }


def test_reconstruction_includes_the_incident_final_signal():
    # Incident.end_time *is* the last supporting signal's timestamp, so a
    # naive half-open bound would silently drop it.
    storage = FakeStorage()
    service = IncidentIntelligenceService(storage=storage)
    result, metrics, events, deployments, baselines = persist_historical_incident(
        service
    )
    incident = result.researches[0].incident
    assert incident.end_time is not None

    context = HistoricalSignalContext(
        metrics=metrics,
        events=events,
        deployments=deployments,
        baselines=baselines,
    )
    signals = service.historical_candidates(
        signal_context=context
    )[0].signals

    assert any(item.timestamp == incident.end_time for item in signals)


def test_every_reconstructed_signal_is_referenced_by_the_chain():
    storage = FakeStorage()
    service = IncidentIntelligenceService(storage=storage)
    result, metrics, events, deployments, baselines = persist_historical_incident(
        service
    )

    context = HistoricalSignalContext(
        metrics=metrics,
        events=events,
        deployments=deployments,
        baselines=baselines,
    )
    candidate = service.historical_candidates(signal_context=context)[0]

    referenced = {
        item.target_id
        for item in candidate.evidence
        if item.source_id == candidate.incident.id
    }
    assert referenced
    assert {item.source_id for item in candidate.signals} <= referenced


def test_reconstruction_never_invents_unreferenced_signals():
    # Telemetry that falls inside the incident's window but that the stored
    # chain does not reference must not be admitted. The intruder is supplied
    # only to the reconstruction context, never to the analysis that produced
    # and persisted the incident, so it is genuinely unreferenced.
    storage = FakeStorage()
    service = IncidentIntelligenceService(storage=storage)
    intruder = make_metric(35, value=999.0, seed="intruder")
    _, metrics, events, deployments, baselines = persist_historical_incident(
        service
    )

    context = HistoricalSignalContext(
        metrics=metrics + [intruder],
        events=events,
        deployments=deployments,
        baselines=baselines,
    )
    signals = service.historical_candidates(signal_context=context)[0].signals

    assert signals
    assert all(item.source_id != intruder.id for item in signals)
    assert all(intruder.id not in item.source_record_ids for item in signals)


def test_incident_without_evidence_reconstructs_no_signals():
    reconstructor = HistoricalSignalReconstructor()
    incident = Incident(
        id=fixed_uuid("bare"),
        title="no evidence",
        start_time=ts(10),
        end_time=ts(20),
        severity="low",
        status="open",
    )
    context = HistoricalSignalContext(baselines={KEY: baseline_for()})
    assert reconstructor.reconstruct(incident, [], context=context) == []


def test_signals_unreproducible_from_context_are_omitted_not_approximated():
    # A chain that references signals the context cannot reproduce yields no
    # signals rather than a guess.
    storage = FakeStorage()
    service = IncidentIntelligenceService(storage=storage)
    persist_historical_incident(service)

    empty_context = HistoricalSignalContext(baselines={KEY: baseline_for()})
    candidates = service.historical_candidates(signal_context=empty_context)
    assert candidates[0].evidence
    assert candidates[0].signals == []


def test_historical_candidates_without_context_keep_signals_empty():
    storage = FakeStorage()
    service = IncidentIntelligenceService(storage=storage)
    result, *_ = persist_historical_incident(service)

    candidates = service.historical_candidates()
    assert len(candidates) == 1
    assert candidates[0].incident.id == result.researches[0].incident.id
    assert candidates[0].signals == []
    # Timeline evidence is still restored exactly as before.
    assert candidates[0].evidence


def test_open_incident_accepts_an_explicit_window_end():
    storage = FakeStorage()
    service = IncidentIntelligenceService(storage=storage)
    metrics, events, deployments = build_window(0, "open")
    baselines = {KEY: baseline_for()}
    deviations, change_points = service.derive_evidence(
        metrics,
        baselines,
        window_start=ts(0),
        window_end=ts(100),
    )
    # No recovery evidence, so the incident stays open with end_time None.
    result = service.analyze(
        deviations=deviations[:2],
        change_points=[],
        events=events,
        deployments=deployments,
        window_start=ts(0),
        window_end=ts(100),
    )
    assert result.researches[0].incident.end_time is None
    service.persist(result)

    context = HistoricalSignalContext(
        metrics=metrics,
        events=events,
        deployments=deployments,
        baselines=baselines,
        window_end=ts(100),
    )
    candidates = service.historical_candidates(signal_context=context)
    assert candidates[0].signals
    assert all(
        item.timestamp <= ts(100) for item in candidates[0].signals
    )


# ----------------------------------------------------------------------
# Historical / current separation
# ----------------------------------------------------------------------


def test_current_window_telemetry_never_leaks_into_historical_signals():
    storage = FakeStorage()
    service = IncidentIntelligenceService(storage=storage)
    _, hist_metrics, hist_events, hist_deployments, baselines = (
        persist_historical_incident(service)
    )

    cur_metrics, cur_events, cur_deployments = build_window(5000, "cur")
    current_ids = (
        {item.id for item in cur_metrics}
        | {item.id for item in cur_events}
        | {item.id for item in cur_deployments}
    )

    # A single context deliberately holding BOTH windows at once.
    context = HistoricalSignalContext(
        metrics=hist_metrics + cur_metrics,
        events=hist_events + cur_events,
        deployments=hist_deployments + cur_deployments,
        baselines=baselines,
    )
    candidate = service.historical_candidates(signal_context=context)[0]
    signals = candidate.signals
    assert signals

    incident = candidate.incident
    for signal in signals:
        assert incident.start_time <= signal.timestamp <= incident.end_time
        assert signal.source_id not in current_ids
        assert not set(signal.source_record_ids) & current_ids


def test_current_and_historical_windows_produce_distinct_signals():
    storage = FakeStorage()
    service = IncidentIntelligenceService(storage=storage)
    _, hist_metrics, hist_events, hist_deployments, baselines = (
        persist_historical_incident(service)
    )
    cur_metrics, cur_events, cur_deployments = build_window(5000, "cur")

    context = HistoricalSignalContext(
        metrics=hist_metrics + cur_metrics,
        events=hist_events + cur_events,
        deployments=hist_deployments + cur_deployments,
        baselines=baselines,
    )
    signals = service.historical_candidates(signal_context=context)[0].signals

    cur_deviations, cur_change_points = service.derive_evidence(
        cur_metrics,
        baselines,
        window_start=ts(5000),
        window_end=ts(5100),
    )
    cur_signals = service._signal_builder.build(
        deviations=cur_deviations,
        change_points=cur_change_points,
        events=cur_events,
        deployments=cur_deployments,
    )
    historical_ids = {item.source_id for item in signals}
    current_ids = {item.source_id for item in cur_signals}
    # No identifier is shared between the two windows.
    assert not historical_ids & current_ids


def test_multi_service_historical_incident_reconstructs_each_service():
    storage = FakeStorage()
    service = IncidentIntelligenceService(storage=storage)

    payment_metrics = [
        make_metric(12, value=500.0, service="payment-service", seed="pm-1"),
        make_metric(22, value=505.0, service="payment-service", seed="pm-2"),
        make_metric(42, value=181.0, service="payment-service", seed="pm-3"),
    ]
    baselines = {
        KEY: baseline_for(),
        PAYMENT_KEY: baseline_for(PAYMENT_KEY, mean=180.0, std=6.0),
    }
    result, metrics, events, deployments, _ = persist_historical_incident(
        service,
        baselines=baselines,
        extra_metrics=payment_metrics,
    )
    incident = result.researches[0].incident
    assert incident.affected_services == ["api-service", "payment-service"]

    context = HistoricalSignalContext(
        metrics=metrics,
        events=events,
        deployments=deployments,
        baselines=baselines,
    )
    signals = service.historical_candidates(signal_context=context)[0].signals

    assert {item.service for item in signals} == {
        "api-service",
        "payment-service",
    }
    metric_pairs = {
        (item.service, item.payload.get("metric_name"))
        for item in signals
        if item.source_type == EvidenceType.METRIC.value
        and item.payload.get("metric_name")
    }
    assert metric_pairs == {
        ("api-service", "latency_ms"),
        ("payment-service", "latency_ms"),
    }


# ----------------------------------------------------------------------
# Traceability
# ----------------------------------------------------------------------


def test_every_reconstructed_signal_is_traceable_to_raw_records():
    storage = FakeStorage()
    service = IncidentIntelligenceService(storage=storage)
    _, metrics, events, deployments, baselines = persist_historical_incident(
        service
    )

    context = HistoricalSignalContext(
        metrics=metrics,
        events=events,
        deployments=deployments,
        baselines=baselines,
    )
    signals = service.historical_candidates(signal_context=context)[0].signals

    metric_ids = {item.id for item in metrics}
    event_ids = {item.id for item in events}
    deployment_ids = {item.id for item in deployments}

    for signal in signals:
        # No generated signal exists without real source ids.
        assert signal.source_record_ids
        assert all(signal.source_record_ids)
        if signal.source_type == EvidenceType.EVENT.value:
            assert set(signal.source_record_ids) <= event_ids
        elif signal.source_type == EvidenceType.DEPLOYMENT.value:
            assert set(signal.source_record_ids) <= deployment_ids
        elif signal.payload.get("metric_name"):
            assert set(signal.source_record_ids) <= metric_ids

    # A change point aggregates the ids of the deviations it spans.
    change_points = [
        item
        for item in signals
        if item.sustained and item.source_type == EvidenceType.METRIC.value
    ]
    assert change_points
    for point in change_points:
        assert set(point.source_record_ids) <= metric_ids
        assert point.payload["consecutive_batches"] >= 2


def test_reconstructed_chain_resolves_incident_to_evidence_to_record():
    storage = FakeStorage()
    service = IncidentIntelligenceService(storage=storage)
    _, metrics, events, deployments, baselines = persist_historical_incident(
        service
    )

    context = HistoricalSignalContext(
        metrics=metrics,
        events=events,
        deployments=deployments,
        baselines=baselines,
    )
    candidate = service.historical_candidates(signal_context=context)[0]
    incident = candidate.incident
    signals = {item.source_id: item for item in candidate.signals}
    all_records = (
        {item.id for item in metrics}
        | {item.id for item in events}
        | {item.id for item in deployments}
    )

    chain = [
        item
        for item in candidate.evidence
        if item.source_id == incident.id
        and item.source_type == EvidenceType.INCIDENT.value
    ]
    assert chain
    for entry in chain:
        assert entry.target_id in signals
        assert entry.relationship_type == "correlation"
        assert entry.explanation
        signal = signals[entry.target_id]
        assert set(signal.source_record_ids) <= all_records


# ----------------------------------------------------------------------
# Determinism
# ----------------------------------------------------------------------


def test_reconstruction_is_deterministic_across_runs():
    storage = FakeStorage()
    service = IncidentIntelligenceService(storage=storage)
    _, metrics, events, deployments, baselines = persist_historical_incident(
        service
    )
    context = HistoricalSignalContext(
        metrics=metrics,
        events=events,
        deployments=deployments,
        baselines=baselines,
    )
    first = service.historical_candidates(signal_context=context)
    second = service.historical_candidates(signal_context=context)
    assert [item.model_dump() for item in first] == [
        item.model_dump() for item in second
    ]


def test_reconstruction_is_independent_of_input_ordering():
    storage = FakeStorage()
    service = IncidentIntelligenceService(storage=storage)
    _, metrics, events, deployments, baselines = persist_historical_incident(
        service
    )

    forward = HistoricalSignalContext(
        metrics=list(metrics),
        events=list(events),
        deployments=list(deployments),
        baselines=baselines,
    )
    reversed_ = HistoricalSignalContext(
        metrics=list(reversed(metrics)),
        events=list(reversed(events)),
        deployments=list(reversed(deployments)),
        baselines=baselines,
    )

    first = service.historical_candidates(signal_context=forward)[0]
    second = service.historical_candidates(signal_context=reversed_)[0]
    assert [item.model_dump() for item in first.signals] == [
        item.model_dump() for item in second.signals
    ]


def test_reconstructed_signals_are_ordered_deterministically():
    storage = FakeStorage()
    service = IncidentIntelligenceService(storage=storage)
    _, metrics, events, deployments, baselines = persist_historical_incident(
        service
    )
    context = HistoricalSignalContext(
        metrics=metrics,
        events=events,
        deployments=deployments,
        baselines=baselines,
    )
    signals = service.historical_candidates(signal_context=context)[0].signals
    keys = [(item.timestamp, str(item.source_id)) for item in signals]
    assert keys == sorted(keys)


# ----------------------------------------------------------------------
# Similarity semantics
# ----------------------------------------------------------------------


def test_identical_current_and_historical_incidents_reach_overall_one():
    # All six factors are genuinely available and identical, so the weighted
    # combination reaches 1.0 - the documented ceiling.
    storage = FakeStorage()
    service = IncidentIntelligenceService(storage=storage)
    _, hist_metrics, hist_events, hist_deployments, baselines = (
        persist_historical_incident(service)
    )

    context = HistoricalSignalContext(
        metrics=hist_metrics,
        events=hist_events,
        deployments=hist_deployments,
        baselines=baselines,
    )
    candidates = service.historical_candidates(signal_context=context)
    assert candidates[0].signals

    cur_metrics, cur_events, cur_deployments = build_window(5000, "cur")
    cur_deviations, cur_change_points = service.derive_evidence(
        cur_metrics,
        baselines,
        window_start=ts(5000),
        window_end=ts(5100),
    )
    current = service.analyze(
        deviations=cur_deviations,
        change_points=cur_change_points,
        events=cur_events,
        deployments=cur_deployments,
        window_start=ts(5000),
        window_end=ts(5100),
    ).researches[0]

    enriched = service.with_history(current, candidates)
    assert len(enriched.historical_matches) == 1
    similarity = enriched.historical_matches[0].similarity

    assert similarity.service_overlap == 1.0
    assert similarity.metric_similarity == 1.0
    assert similarity.event_sequence_similarity == 1.0
    assert similarity.deployment_relationship == 1.0
    assert similarity.temporal_similarity == 1.0
    assert similarity.scenario_match == 1.0
    assert similarity.overall_similarity == pytest.approx(1.0)


def test_signal_derived_factors_are_no_longer_structurally_zero():
    # The Phase 3.1 defect: with no signals loaded, metric/event/deployment
    # similarity are forced to 0.0 regardless of the real evidence.
    storage = FakeStorage()
    service = IncidentIntelligenceService(storage=storage)
    _, hist_metrics, hist_events, hist_deployments, baselines = (
        persist_historical_incident(service)
    )
    # A structurally identical incident in a different window, not persisted,
    # so the matcher has a genuine current/historical pair to score.
    current_research = analyze_window(
        service, offset=5000, seed="cur", window=(5000, 5100)
    )[0].researches[0]

    without_signals = service.with_history(
        current_research,
        service.historical_candidates(),
    ).historical_matches[0].similarity
    with_signals = service.with_history(
        current_research,
        service.historical_candidates(
            signal_context=HistoricalSignalContext(
                metrics=hist_metrics,
                events=hist_events,
                deployments=hist_deployments,
                baselines=baselines,
            )
        ),
    ).historical_matches[0].similarity

    assert without_signals.metric_similarity == 0.0
    assert without_signals.event_sequence_similarity == 0.0
    assert without_signals.deployment_relationship == 0.0

    assert with_signals.metric_similarity == 1.0
    assert with_signals.event_sequence_similarity == 1.0
    assert with_signals.deployment_relationship == 1.0
    assert with_signals.overall_similarity > without_signals.overall_similarity


def test_unavailable_evidence_is_penalised_not_fabricated():
    # With genuinely absent evidence the documented evidence penalty remains:
    # factors score 0.0 with their full weight and nothing is invented.
    storage = FakeStorage()
    service = IncidentIntelligenceService(storage=storage)
    persist_historical_incident(service)
    current_research = analyze_window(
        service, offset=5000, seed="cur", window=(5000, 5100)
    )[0].researches[0]

    empty_context = HistoricalSignalContext(baselines={KEY: baseline_for()})
    candidates = service.historical_candidates(signal_context=empty_context)
    assert candidates[0].signals == []

    similarity = service.with_history(
        current_research, candidates
    ).historical_matches[0].similarity
    assert similarity.metric_similarity == 0.0
    assert similarity.event_sequence_similarity == 0.0
    assert similarity.deployment_relationship == 0.0
    # Service overlap and scenario remain measurable, but the three absent
    # factors still cost their full weight.
    assert similarity.service_overlap == 1.0
    assert similarity.scenario_match == 1.0
    assert similarity.overall_similarity == pytest.approx(0.50)
    assert similarity.overall_similarity < 1.0


def test_open_incident_temporal_factor_stays_neutral():
    # An incident with no resolved end exposes no temporal structure, so the
    # documented neutral 0.5 applies rather than a measured comparison.
    service = IncidentIntelligenceService()
    deviations, _change_points = service.derive_evidence(
        [make_metric(7000, value=300.0, seed="tp-1")],
        {KEY: baseline_for()},
        window_start=ts(7000),
        window_end=ts(7100),
    )
    open_research = service.analyze(deviations=deviations).researches[0]
    assert open_research.incident.end_time is None

    similarity = service._matcher.similarity(
        current=open_research.incident,
        historical=open_research.incident,
        current_signals=open_research.signals,
        historical_signals=open_research.signals,
    )
    assert similarity.temporal_similarity == 0.5


# ----------------------------------------------------------------------
# Shared Phase 2 evidence bridge
# ----------------------------------------------------------------------


def test_shared_bridge_matches_service_derive_evidence():
    service = IncidentIntelligenceService()
    metrics, _events, _deployments = build_window(0, "bridge")
    baselines = {KEY: baseline_for()}

    deviations, change_points = service.derive_evidence(
        metrics, baselines, window_start=ts(0), window_end=ts(100)
    )
    bridged = derive_deviation_evidence(
        metrics,
        baselines,
        config=IntelligenceConfig(),
        window_start=ts(0),
        window_end=ts(100),
    )
    assert [item.model_dump() for item in deviations] == [
        item.model_dump() for item in bridged[0]
    ]
    assert [item.model_dump() for item in change_points] == [
        item.model_dump() for item in bridged[1]
    ]


def test_shared_bridge_respects_the_half_open_window():
    metrics, _events, _deployments = build_window(0, "halfopen")
    metrics = metrics + [make_metric(500, value=300.0, seed="far-m")]

    deviations, _change_points = derive_deviation_evidence(
        metrics,
        {KEY: baseline_for()},
        window_start=ts(0),
        window_end=ts(100),
    )
    assert all(item.timestamp < ts(100) for item in deviations)
    assert all(item.timestamp >= ts(0) for item in deviations)
    assert len(deviations) == 3
