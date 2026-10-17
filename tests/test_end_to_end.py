"""Offline end-to-end workflow test for RESON (no Supabase, no network).

Simulator telemetry flows through the real production path:

    SystemSimulator
      -> TelemetryIngestionService + InMemoryStorage
      -> BehaviorIntelligenceService (baselines + analysis)
      -> IncidentIntelligenceService.derive_evidence (Phase 2 bridge)
      -> IncidentIntelligenceService.analyze (correlation/detection/lifecycle)
      -> persist + historical_candidates + find_historical_matches

Assertions are behavioral (evidence present, traceability intact,
deterministic, ranked) rather than exact counts, so simulator tuning stays
free to evolve without breaking the workflow contract.
"""

from datetime import datetime, timedelta, timezone

from src.data.ingestion import TelemetryIngestionService
from src.data.memory_storage import InMemoryStorage
from src.data.schemas import Deployment, Event, Log, Metric
from src.data.simulator import SystemSimulator
from src.data.supabase_client import get_supabase_client
from src.incidents.reconstruction import HistoricalSignalContext
from src.incidents.service import IncidentIntelligenceService
from src.intelligence.service import BehaviorIntelligenceService
from src.orchestrator import create_application

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
HISTORY_POINTS = 30
CURRENT_POINTS = 8


def split(records):
    metrics = [r for r in records if isinstance(r, Metric)]
    logs = [r for r in records if isinstance(r, Log)]
    events = [r for r in records if isinstance(r, Event)]
    deployments = [r for r in records if isinstance(r, Deployment)]
    return metrics, logs, events, deployments


def window_records(scenario, day_offset):
    simulator = SystemSimulator(seed=42)
    batches = simulator.generate_history(
        start_time=START + timedelta(days=day_offset),
        points=CURRENT_POINTS,
        scenario=scenario,
    )
    return [record for batch in batches for record in batch.records()]


def history_metrics():
    simulator = SystemSimulator(seed=42)
    batches = simulator.generate_history(
        start_time=START,
        points=HISTORY_POINTS,
        scenario="normal",
    )
    records = [record for batch in batches for record in batch.records()]
    return [r for r in records if isinstance(r, Metric)]


def run_workflow(storage, scenario, day_offset, baselines):
    """Ingest one window and run behavior + incident analysis on it."""
    metrics, logs, events, deployments = split(window_records(scenario, day_offset))

    ingestion = TelemetryIngestionService(storage)
    ingested = ingestion.ingest_batch(metrics + logs + events + deployments)
    assert ingested.total == len(metrics + logs + events + deployments)
    assert len(storage.get_metrics()) >= len(metrics)

    behavior = BehaviorIntelligenceService()
    analysis = behavior.analyze(
        baselines,
        metrics,
        logs=logs,
        events=events,
        deployments=deployments,
    )
    assert analysis.changed_services, f"expected behavior change for {scenario}"
    assert analysis.overall_score > 0.0

    incidents = IncidentIntelligenceService(storage=storage)
    deviations, change_points = incidents.derive_evidence(
        metrics,
        baselines,
        window_start=analysis.analysis_window_start,
        window_end=analysis.analysis_window_end,
    )
    assert any(d.is_anomalous for d in deviations)

    result = incidents.analyze(
        analysis=analysis,
        deviations=deviations,
        change_points=change_points,
        logs=logs,
        events=events,
        deployments=deployments,
        window_start=analysis.analysis_window_start,
        window_end=analysis.analysis_window_end,
    )
    assert len(result.researches) >= 1
    # Candidates are a strict superset of accepted evidence.
    assert len(result.correlation_candidates) >= len(result.correlation_evidence)

    for research in result.researches:
        assert research.severity_explanation
        assert research.timeline
        for item in research.timeline:
            assert item.source_id == research.incident.id
            assert item.target_id in {
                signal.source_id for signal in research.signals
            }

    # The pipeline is deterministic across runs.
    repeat = incidents.analyze(
        analysis=analysis,
        deviations=deviations,
        change_points=change_points,
        logs=logs,
        events=events,
        deployments=deployments,
        window_start=analysis.analysis_window_start,
        window_end=analysis.analysis_window_end,
    )
    assert repeat.model_dump() == result.model_dump()

    return result, (metrics, logs, events, deployments)


def test_offline_end_to_end_workflow_with_history():
    storage = InMemoryStorage()
    baselines = BehaviorIntelligenceService().build_baselines(history_metrics())
    assert baselines

    first, window1 = run_workflow(storage, "database_pressure", 1, baselines)
    incidents = IncidentIntelligenceService(storage=storage)
    incidents.persist(first)
    assert len(storage.get_incidents()) == len(first.researches)
    assert len(storage.get_evidence()) >= 1

    second, _ = run_workflow(storage, "latency_spike", 2, baselines)
    current = second.researches[0]

    context = HistoricalSignalContext(
        metrics=window1[0],
        logs=window1[1],
        events=window1[2],
        deployments=window1[3],
        baselines=baselines,
    )
    candidates = incidents.historical_candidates(signal_context=context)
    assert len(candidates) == len(first.researches)
    # The persisted chain round-trips through storage and rebuilds signals.
    assert all(candidate.evidence for candidate in candidates)
    assert any(candidate.signals for candidate in candidates)

    matches = incidents.find_historical_matches(current, candidates)
    assert len(matches) == len(candidates)
    historical_ids = {m.historical_incident_id for m in matches}
    assert historical_ids == {r.incident.id for r in first.researches}
    assert current.incident.id not in historical_ids
    scored = [m.similarity.overall_similarity for m in matches]
    assert scored == sorted(scored, reverse=True)
    assert scored[0] > 0.0

    enriched = incidents.with_history(current, candidates)
    assert len(enriched.historical_matches) == len(matches)


def test_end_to_end_via_application_factory(monkeypatch):
    """The composition root supplies the storage the workflow runs on."""
    from src.common.config import get_config

    get_config.cache_clear()
    get_supabase_client.cache_clear()
    monkeypatch.delenv("RESON_STORAGE_BACKEND", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
    monkeypatch.setattr(
        "src.orchestrator.get_supabase_client",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("no Supabase initialization in local mode")
        ),
    )
    try:
        application = create_application("memory")
        assert isinstance(application.storage, InMemoryStorage)

        metrics, logs, events, deployments = split(window_records("error_spike", 3))
        ingested = application.ingestion.ingest_batch(
            metrics + logs + events + deployments
        )
        assert ingested.total == len(metrics + logs + events + deployments)

        stored = application.storage.get_metrics()
        assert len(stored) == len(metrics)
        assert [m.timestamp for m in stored] == sorted(m.timestamp for m in stored)
    finally:
        get_config.cache_clear()
        get_supabase_client.cache_clear()
