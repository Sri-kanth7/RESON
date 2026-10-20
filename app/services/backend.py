"""UI-facing services for the RESON Streamlit application.

This module adapts the existing backend (ingestion, storage, behavior and
incident intelligence) for page rendering. It contains no intelligence
algorithms of its own: every analytical result comes from the real services
in ``src/``. All demonstration telemetry is produced by the deterministic
simulator and is always labeled as simulated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable

from src.data.dataset import HistoricalDatasetBuilder
from src.data.dataset_service import HistoricalDatasetService
from src.data.ingestion import TelemetryIngestionService
from src.data.schemas import Deployment, Environment, Event, Incident, Log, Metric
from src.data.storage import Storage
from src.data.windows_collector import (
    SOURCE_WINDOWS_LOCAL,
    observations_to_metrics,
)
from src.incidents.lifecycle import IncidentLifecycle
from src.incidents.models import IncidentIntelligenceResult
from src.incidents.reconstruction import HistoricalSignalContext
from src.incidents.service import IncidentIntelligenceService
from src.intelligence.baseline import BaselineReadiness, assess_series_readiness
from src.intelligence.historical_comparison import compare_periods
from src.intelligence.models import (
    BaselineKey,
    BehaviorAnalysis,
    ChangePoint,
    HistoricalBaseline,
    MetricDeviation,
)
from src.intelligence.service import BehaviorIntelligenceService
from src.preprocessing.normalizer import group_metrics, slice_series
from src.orchestrator import RESONApplication, create_application

DEMO_START = datetime(2026, 1, 1, tzinfo=timezone.utc)

#: Batches 1-3 of the default dataset are normal telemetry used for history.
DEMO_HISTORY_BATCHES = 3


@dataclass
class Demonstration:
    """Results of one user-triggered local demonstration analysis."""

    simulated: bool = True
    batches: int = 0
    records: int = 0
    scenarios: list[str] = field(default_factory=list)
    baselines: dict[BaselineKey, HistoricalBaseline] = field(default_factory=dict)
    analysis: BehaviorAnalysis | None = None
    deviations: list[MetricDeviation] = field(default_factory=list)
    change_points: list[ChangePoint] = field(default_factory=list)
    incident_result: IncidentIntelligenceResult | None = None
    signal_context: HistoricalSignalContext | None = None


def build_application(backend: str) -> RESONApplication:
    """Build the application for an explicitly selected storage backend.

    Raises ``RuntimeError`` with an actionable message when the backend
    cannot be constructed (for example Supabase without credentials or
    unreachable). Never falls back to another backend silently.
    """
    try:
        return create_application(backend)
    except (ValueError, RuntimeError) as exc:
        raise RuntimeError(str(exc)) from exc
    except Exception as exc:
        raise RuntimeError(
            f"Storage backend {backend!r} could not be constructed: {exc}"
        ) from exc


def storage_counts(storage: Storage) -> dict[str, int]:
    """Return stored record counts by type for overview display."""
    metrics = storage.get_metrics()
    return {
        "metrics": len(metrics),
        "logs": len(storage.get_logs()),
        "events": len(storage.get_events()),
        "deployments": len(storage.get_deployments()),
        "incidents": len(storage.get_incidents()),
        "evidence": len(storage.get_evidence()),
        "services": len({metric.service for metric in metrics}),
    }


def split_records(records: list) -> tuple[list, list, list, list]:
    """Split telemetry records into metrics, logs, events, deployments."""
    metrics = [r for r in records if isinstance(r, Metric)]
    logs = [r for r in records if isinstance(r, Log)]
    events = [r for r in records if isinstance(r, Event)]
    deployments = [r for r in records if isinstance(r, Deployment)]
    return metrics, logs, events, deployments


def run_demonstration(application: RESONApplication) -> Demonstration:
    """Run the full deterministic pipeline on simulated telemetry.

    Builds the historical dataset, ingests every record through the real
    ingestion service, derives baselines from the leading normal batches,
    analyzes the remaining window with the real intelligence services, and
    persists incidents plus evidence through the selected backend.
    """
    storage = application.storage
    dataset = HistoricalDatasetBuilder().build(start_time=DEMO_START)

    dataset_service = HistoricalDatasetService(
        builder=HistoricalDatasetBuilder(),
        ingestion=TelemetryIngestionService(storage),
    )
    # Ingest through the same service the application uses (rebuild to keep
    # the dataset deterministic; ingestion itself is idempotent per record).
    dataset_service.build_and_persist(start_time=DEMO_START)

    history_records: list = []
    current_records: list = []
    for index, batch in enumerate(dataset.batches):
        if index < DEMO_HISTORY_BATCHES:
            history_records.extend(batch.records())
        else:
            current_records.extend(batch.records())

    history_metrics, _, _, _ = split_records(history_records)
    metrics, logs, events, deployments = split_records(current_records)

    behavior = BehaviorIntelligenceService()
    baselines = behavior.build_baselines(history_metrics)
    analysis = behavior.analyze(
        baselines, metrics, logs=logs, events=events, deployments=deployments
    )

    intelligence = IncidentIntelligenceService(storage=storage)
    deviations, change_points = intelligence.derive_evidence(
        metrics,
        baselines,
        window_start=analysis.analysis_window_start,
        window_end=analysis.analysis_window_end,
    )
    incident_result = intelligence.analyze(
        analysis=analysis,
        deviations=deviations,
        change_points=change_points,
        logs=logs,
        events=events,
        deployments=deployments,
        window_start=analysis.analysis_window_start,
        window_end=analysis.analysis_window_end,
    )
    intelligence.persist(incident_result)

    return Demonstration(
        simulated=True,
        batches=dataset.batch_count,
        records=dataset.record_count,
        scenarios=list(dataset.scenarios),
        baselines=baselines,
        analysis=analysis,
        deviations=deviations,
        change_points=change_points,
        incident_result=incident_result,
        signal_context=HistoricalSignalContext(
            metrics=metrics,
            logs=logs,
            events=events,
            deployments=deployments,
            baselines=baselines,
        ),
    )


def incident_label(incident: Incident) -> str:
    """Return a compact selector label for an incident."""
    severity = getattr(incident.severity, "value", incident.severity)
    return (
        f"{str(incident.id)[:8]} · {severity} · "
        f"{incident.start_time.strftime('%Y-%m-%d %H:%M UTC')}"
    )


def ordered_incidents(storage: Storage) -> list[Incident]:
    """Return stored incidents newest-first for selectors and registries."""
    return sorted(
        storage.get_incidents(),
        key=lambda item: (item.start_time, str(item.id)),
        reverse=True,
    )


def severity_explanation(
    demo: Demonstration | None, incident_id: object
) -> str | None:
    """Return the recorded severity explanation for an in-session analysis.

    Explanations are only available for incidents analyzed in this session;
    persistence stores the incident and its evidence chain, not the
    explanation text, so stored-only incidents return ``None``.
    """
    if demo is None or demo.incident_result is None:
        return None
    for research in demo.incident_result.researches:
        if research.incident.id == incident_id:
            return research.severity_explanation or None
    return None


def research_for(
    demo: Demonstration | None, incident_id: object
):
    """Return the in-session research unit for an incident, if present."""
    if demo is None or demo.incident_result is None:
        return None
    for research in demo.incident_result.researches:
        if research.incident.id == incident_id:
            return research
    return None


def historical_matches_for(
    demo: Demonstration | None,
    storage: Storage,
    incident_id: object,
    top_k: int = 5,
) -> list:
    """Rank stored historical incidents against one in-session incident."""
    research = research_for(demo, incident_id)
    if research is None or demo is None or demo.signal_context is None:
        return []
    service = IncidentIntelligenceService(storage=storage)
    candidates = service.historical_candidates(signal_context=demo.signal_context)
    return service.find_historical_matches(research, candidates, top_k=top_k)


def metric_keys(storage: Storage) -> list[BaselineKey]:
    """Return sorted metric identities present in stored telemetry."""
    keys = {
        BaselineKey(
            service=metric.service,
            environment=(
                getattr(metric.environment, "value", metric.environment)
            ),
            metric_name=metric.metric_name,
        )
        for metric in storage.get_metrics()
    }
    return sorted(keys, key=lambda k: (k.service, k.environment, k.metric_name))


def series_for_key(storage: Storage, key: BaselineKey) -> list[tuple]:
    """Return (timestamp, value) observations for one metric identity."""
    points = [
        (metric.timestamp, metric.value)
        for metric in storage.get_metrics()
        if metric.service == key.service
        and getattr(metric.environment, "value", metric.environment)
        == key.environment
        and metric.metric_name == key.metric_name
    ]
    return sorted(points, key=lambda point: point[0])


def mark_investigating(storage: Storage, incident: Incident) -> Incident:
    """Move an open incident to investigating and persist the change."""
    updated = IncidentLifecycle().investigate(incident)
    storage.save_incident(updated)
    return updated


def storage_health(storage: Storage) -> dict:
    """Report whether the storage backend is operational, separate from its mode.

    Returns ``{"operational": bool, "error": str | None, "counts": dict}``.
    A backend that can be read is treated as operational; a write failure is
    surfaced by the caller's write path. This never contacts Supabase itself
    -- it only exercises the abstraction the application already uses.
    """
    try:
        counts = storage_counts(storage)
        return {
            "operational": True,
            "error": None,
            "backend": type(storage).__name__,
            "counts": counts,
            **(
                {"database_path": str(storage.db_path)}
                if getattr(storage, "db_path", None) is not None
                else {}
            ),
        }
    except Exception as exc:  # backend-specific failure, reported as-is
        return {
            "operational": False,
            "error": str(exc),
            "counts": {},
            "backend": type(storage).__name__,
        }


def observation_publish(ingestion: TelemetryIngestionService):
    """Return a collector publish callback that ingests real measurements.

    The callback converts collector output to the canonical telemetry contract
    and ingests it through the existing ingestion service. Collection failures
    (exceptions raised by ingestion) propagate to the collector's error state
    rather than being swallowed.
    """

    def publish(host, processes) -> None:
        records = observations_to_metrics(host, processes)
        if records:
            ingestion.ingest(records)

    return publish


def host_readiness(
    storage: Storage,
    host_id: str,
    source: str | None = None,
    metric_names: Iterable[str] | None = None,
    reference_time: datetime | None = None,
) -> dict[BaselineKey, BaselineReadiness]:
    """Baseline readiness per metric for one host, from stored observations."""

    metrics = [
        metric
        for metric in storage.get_metrics(service=host_id)
        if (source is None or metric.source == source)
        and (metric_names is None or metric.metric_name in metric_names)
    ]

    readiness: dict[BaselineKey, list] = {}
    for metric in metrics:
        readiness.setdefault(
            BaselineKey(
                service=metric.service,
                environment=metric.environment,
                metric_name=metric.metric_name,
            ),
            [],
        ).append(metric)

    result: dict[BaselineKey, BaselineReadiness] = {}
    for key, records in readiness.items():
        series = group_metrics(records)[0]
        result[key] = assess_series_readiness(
            series, reference_time=reference_time
        )
    return result


def compare_today_vs_prior_day(
    storage: Storage,
    *,
    host_id: str,
    source: str | None = None,
    metric_names: Iterable[str] | None = None,
    reference_time: datetime | None = None,
) -> HistoricalComparisonResult:
    """Compare today's observations against the prior calendar day (UTC).

    The current window is ``[start_of_today, reference_time]`` and the
    historical window is the previous calendar day. Returns a result whose
    ``sufficient`` flag and ``reasons`` explain exactly when the comparison
    cannot be completed (for example no prior-day history in memory mode).
    """

    reference_time = reference_time or datetime.now(timezone.utc)
    today_start = reference_time.replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    yesterday_start = today_start - timedelta(days=1)
    # The historical window ends at today_start (exclusive in slice_series,
    # inclusive in the storage query). The combined [start, end) semantics
    # correctly include all of yesterday and none of today; subtracting a
    # microsecond here would drop the final microsecond of the historical day.
    yesterday_end = today_start

    # BehaviorIntelligenceService slices the analysis window half-open
    # [start, end), so extend the inclusive end by one microsecond to keep
    # the final observation (same idiom the service uses internally).
    current_end = reference_time + timedelta(microseconds=1)

    return compare_periods(
        storage,
        host_id=host_id,
        source=source,
        metric_names=metric_names,
        current_start=today_start,
        current_end=current_end,
        historical_start=yesterday_start,
        historical_end=yesterday_end,
    )
