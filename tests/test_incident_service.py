"""Tests for the incident intelligence service orchestration."""

from src.data.schemas import (
    EvidenceType,
    IncidentStatus,
    LogLevel,
)
from src.intelligence.models import (
    BaselineKey,
    BaselineStatistics,
    HistoricalBaseline,
    IntelligenceConfig,
    MetricDeviation,
)
from src.incidents.models import IncidentIntelligenceConfig
from src.incidents.service import IncidentIntelligenceService
from src.preprocessing.normalizer import MetricSeries, MetricObservation
from tests.incident_test_helpers import (
    FakeStorage,
    KEY,
    all_evidence,
    make_deployment,
    make_deviation,
    make_event,
    make_log,
    make_metric,
)
from tests.incident_test_helpers import ts


def baseline_for(key: BaselineKey = KEY) -> HistoricalBaseline:
    return HistoricalBaseline(
        key=key,
        start_time=ts(-10),
        end_time=ts(0),
        statistics=BaselineStatistics(
            count=10,
            mean=100.0,
            std=5.0,
        ),
        source_record_ids=[],
        explanation="test baseline",
    )


def run_analyze(**kwargs):
    return IncidentIntelligenceService().analyze(**kwargs)


def test_end_to_end_analyze_produces_research_with_timeline():
    result = run_analyze(
        deviations=[
            make_deviation(10, seed="e2e-1"),
            make_deviation(20, seed="e2e-2"),
        ],
        events=[make_event(10, seed="e2e-e")],
    )
    assert len(result.researches) == 1
    research = result.researches[0]
    assert research.incident.affected_services == ["api-service"]
    assert research.incident.start_time == ts(10)
    assert len(research.timeline) == 3
    assert isinstance(research.severity_explanation, str)
    assert research.severity_explanation


def test_analyze_window_filters_evidence():
    result = run_analyze(
        deviations=[
            make_deviation(10, seed="wf-1"),
            make_deviation(20, seed="wf-2"),
            make_deviation(60_000, seed="wf-3"),
        ],
        window_start=ts(50),
        window_end=ts(61_000),
    )
    assert len(result.researches) == 1
    assert result.researches[0].incident.start_time == ts(60_000)
    assert result.window_start == ts(50)


def test_analyze_is_deterministic_across_runs():
    kwargs = dict(
        deviations=[
            make_deviation(10, seed="det-1"),
            make_deviation(30, seed="det-2"),
        ],
        logs=[make_log(15, level=LogLevel.ERROR, seed="det-l")],
    )
    first = run_analyze(**kwargs)
    second = run_analyze(**kwargs)
    assert first.model_dump() == second.model_dump()


def test_empty_input_produces_empty_result():
    result = run_analyze()
    assert result.researches == []
    assert result.correlation_evidence == []
    assert result.overall_shift == 0.0


def test_investigation_flag_marks_affected_service():
    result = run_analyze(
        deviations=[
            make_deviation(10, seed="inv-1"),
            make_deviation(20, seed="inv-2"),
        ],
        investigate_services=["api-service"],
    )
    assert result.researches[0].incident.status == IncidentStatus.INVESTIGATING.value


def test_recovery_normal_deviation_resolves_incident():
    from src.intelligence.models import DeviationStatus

    result = run_analyze(
        deviations=[
            make_deviation(10, seed="rec-1"),
            make_deviation(20, seed="rec-2"),
            make_deviation(40, status=DeviationStatus.NORMAL, seed="rec-3"),
        ],
    )
    research = result.researches[0]
    assert research.incident.status == IncidentStatus.RESOLVED.value
    # The normal deviation is supporting context (extends the end) and the same
    # deviation is the recovery evidence that resolves the incident.
    assert research.incident.end_time == ts(40)


def test_rolled_back_deployment_resolves_incident():
    from src.data.schemas import DeploymentStatus

    result = run_analyze(
        deviations=[make_deviation(10, seed="rb-1")],
        deployments=[
            make_deployment(130, service="api-service", status=DeploymentStatus.ROLLED_BACK, seed="rb-d")
        ],
    )
    research = result.researches[0]
    assert research.incident.status == IncidentStatus.RESOLVED.value
    assert research.incident.end_time == ts(130)


def test_success_deployment_then_further_anomaly_keeps_incident_open():
    # A successful deployment must never auto-close an incident whose signals
    # continue after it.
    result = run_analyze(
        deviations=[
            make_deviation(10, seed="na-1"),
            make_deviation(130, seed="na-2"),
        ],
        deployments=[make_deployment(70, service="api-service", seed="na-d")],
    )
    research = result.researches[0]
    assert research.incident.status == IncidentStatus.OPEN.value


def test_same_service_recovery_deployment_joins_incident_context():
    # A SUCCESS deployment for an affected service within the correlation
    # window becomes supporting context (extending the cluster), but without a
    # positive recovery signal the lifecycle keeps the incident OPEN.
    result = run_analyze(
        deviations=[
            make_deviation(10, seed="ext-1"),
            make_deviation(20, seed="ext-2"),
        ],
        deployments=[make_deployment(40, service="api-service", seed="ext-d")],
    )
    research = result.researches[0]
    assert research.incident.status == IncidentStatus.OPEN.value
    assert research.incident.end_time is None
    assert len(research.timeline) == 3  # 2 deviations + deployment context


def test_derive_evidence_reuses_phase_two_detectors():
    service = IncidentIntelligenceService()
    metrics = [
        make_metric(10, value=300.0, seed="br-1"),
        make_metric(20, value=305.0, seed="br-2"),
        make_metric(30, value=295.0, seed="br-3"),
    ]
    deviations, change_points = service.derive_evidence(metrics, {KEY: baseline_for()})
    assert all(isinstance(item, MetricDeviation) for item in deviations)
    assert len(deviations) == 3
    assert all(item.status.value == "anomalous" for item in deviations)
    assert len(change_points) == 1


def test_derive_evidence_plugs_into_analyze():
    service = IncidentIntelligenceService()
    metrics = [
        make_metric(10, value=300.0, seed="br-full-1"),
        make_metric(20, value=305.0, seed="br-full-2"),
    ]
    deviations, change_points = service.derive_evidence(metrics, {KEY: baseline_for()})
    result = service.analyze(deviations=deviations, change_points=change_points)
    assert len(result.researches) == 1


def test_derive_evidence_respects_analysis_window():
    # Mirror BehaviorIntelligenceService.analyze: metrics must be windowed to
    # [window_start, window_end) before detection so out-of-window observations
    # never leak into the incident corpus.
    service = IncidentIntelligenceService()
    metrics = [
        make_metric(10, value=300.0, seed="w-1"),
        make_metric(20, value=305.0, seed="w-2"),
        make_metric(30, value=295.0, seed="w-3"),
        make_metric(500, value=300.0, seed="w-4"),  # outside [Ts(0), Ts(100))
    ]
    with_window, _ = service.derive_evidence(
        metrics,
        {KEY: baseline_for()},
        window_start=ts(0),
        window_end=ts(100),
    )
    without_window, _ = service.derive_evidence(metrics, {KEY: baseline_for()})
    assert len(with_window) == 3
    assert len(without_window) == 4
    assert all(deviation.timestamp < ts(100) for deviation in with_window)


def test_historical_matches_enrich_research():
    service = IncidentIntelligenceService()
    current = service.analyze(
        deviations=[make_deviation(10, seed="hm-1")],
    ).researches[0]
    historical = service.analyze(
        deviations=[make_deviation(5000, seed="hm-2")],
    ).researches[0]
    from src.incidents.models import IncidentHistory

    candidate = IncidentHistory(
        incident=historical.incident,
        evidence=historical.timeline,
        signals=historical.signals,
    )
    enriched = service.with_history(current, [candidate])
    assert len(enriched.historical_matches) == 1
    assert enriched.historical_matches[0].historical_incident_id == historical.incident.id
    # The enriched copy must not leak state into the original research.
    assert current.historical_matches == []


def test_persist_writes_incidents_and_evidence_through_storage():
    storage = FakeStorage()
    service = IncidentIntelligenceService(storage=storage)
    result = service.analyze(
        deviations=[
            make_deviation(10, seed="p-1"),
            make_deviation(20, seed="p-2"),
        ],
    )
    service.persist(result)
    assert len(storage.incidents) == 1
    stored_evidence = all_evidence(storage)
    assert len(stored_evidence) == 3  # 2 timeline links + 1 correlation link
    assert any(item.source_type == EvidenceType.INCIDENT.value for item in stored_evidence)
    assert all(item.id and item.timestamp for item in stored_evidence)


def test_persist_is_noop_without_storage():
    service = IncidentIntelligenceService()
    result = service.analyze(deviations=[make_deviation(10, seed="np-1")])
    service.persist(result)  # must not raise


def test_persist_avoids_duplicate_evidence():
    storage = FakeStorage()
    service = IncidentIntelligenceService(storage=storage)
    result = service.analyze(
        deviations=[make_deviation(10, seed="dup-p1")],
    )
    service.persist(result)
    service.persist(result)
    assert len(storage.evidence) == len(all_evidence(storage))
    assert len(all_evidence(storage)) == 1


def test_historical_candidates_are_reloaded_from_storage():
    storage = FakeStorage()
    service = IncidentIntelligenceService(storage=storage)
    result = service.analyze(
        deviations=[
            make_deviation(10, seed="rc-1"),
            make_deviation(20, seed="rc-2"),
        ],
    )
    service.persist(result)
    candidates = service.historical_candidates()
    assert len(candidates) == 1
    assert candidates[0].incident.id == result.researches[0].incident.id
    # Timeline evidence is restored alongside the incident.
    assert len(candidates[0].evidence) == 2


def test_custom_config_flow_through_all_modules():
    config = IncidentIntelligenceConfig(
        correlation_window_seconds=60.0,
        deployment_window_seconds=30.0,
        minimum_correlation_strength=0.6,
    )
    service = IncidentIntelligenceService(config=config)
    # Narrow settings still keep every signal within a bounded temporal
    # relationship: deployment (10s) -> deviation (20s, 40s).
    result = service.analyze(
        deployments=[make_deployment(10, seed="cfg-d")],
        deviations=[
            make_deviation(20, seed="cfg-1"),
            make_deviation(40, seed="cfg-2"),
        ],
    )
    assert len(result.researches) == 1
    assert len(result.researches[0].timeline) >= 2