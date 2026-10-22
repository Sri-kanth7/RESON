"""The deterministic local demonstration pipeline.

The demonstration runs the **real** ingestion, intelligence and persistence
path over **simulated** telemetry. Every record it writes is tagged
``source="simulation"``, so demonstration data can never be mistaken for live
host telemetry — at storage, query, or dashboard time.

The pipeline lives in ``src/`` (not in the dashboard layer) so that the
Streamlit UI and the standalone agent can run exactly the same demonstration:

* the dashboard offers a *full* and a *quick* timeline;
* ``python -m src.data.agent --demonstrate`` runs the same pipeline from the
  command line, with an optional explicit scenario list.

A shorter timeline is a smaller, faster run of the *same* pipeline: it is
still ingested, still baselined, still analysed and still persisted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from src.data.dataset import HistoricalDatasetBuilder
from src.data.dataset_service import HistoricalDatasetService
from src.data.ingestion import TelemetryIngestionService
from src.data.schemas import Deployment, Event, Log, Metric
from src.incidents.models import IncidentIntelligenceResult
from src.incidents.reconstruction import HistoricalSignalContext
from src.incidents.service import IncidentIntelligenceService
from src.intelligence.models import (
    BaselineKey,
    BehaviorAnalysis,
    ChangePoint,
    HistoricalBaseline,
    MetricDeviation,
)
from src.intelligence.service import BehaviorIntelligenceService

#: Start of the simulated timeline. Fixed, so a demonstration is reproducible.
DEMO_START = datetime(2026, 1, 1, tzinfo=timezone.utc)

#: Batches 1-3 of the default dataset are normal telemetry used for history.
DEMO_HISTORY_BATCHES = 3

#: The full default timeline (15 batches).
FULL_SCENARIOS: tuple[str, ...] = HistoricalDatasetBuilder.DEFAULT_SCENARIOS

#: A short timeline that still produces an incident.
#:
#: Three normal batches build the baseline, then an error spike and a normal
#: batch provide the analysis window. It runs the identical pipeline over far
#: fewer signals, so it is fast enough for an interactive "try it now".
QUICK_SCENARIOS: tuple[str, ...] = (
    "normal",
    "normal",
    "normal",
    "error_spike",
    "normal",
)

#: Timeline profiles offered by the user interface and the command line.
TIMELINE_PROFILES: dict[str, tuple[str, ...] | None] = {
    "full": None,
    "quick": QUICK_SCENARIOS,
}


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
    timeline: str = "full"
    """Which timeline profile produced this run (``full``, ``quick`` or
    ``custom``). The user interface labels the result accordingly."""


def resolve_scenarios(
    scenarios: tuple[str, ...] | list[str] | None = None,
    profile: str | None = None,
) -> tuple[str, ...] | None:
    """Return the scenario list for a requested timeline.

    ``profile`` selects a named profile (``full`` or ``quick``). An explicit
    ``scenarios`` list always wins, and ``None`` means the full default
    timeline.
    """
    if scenarios is not None:
        selected = tuple(scenarios)
        if not selected:
            raise ValueError("At least one scenario is required.")
        return selected
    if profile is None:
        return None
    if profile not in TIMELINE_PROFILES:
        raise ValueError(
            f"Unknown timeline profile {profile!r}. "
            f"Supported profiles: {', '.join(sorted(TIMELINE_PROFILES))}."
        )
    return TIMELINE_PROFILES[profile]


def timeline_label(scenarios: tuple[str, ...] | list[str] | None) -> str:
    """Return a short label describing the timeline that was run."""
    if scenarios is None:
        return "full"
    selected = tuple(scenarios)
    if selected == QUICK_SCENARIOS:
        return "quick"
    if selected == FULL_SCENARIOS:
        return "full"
    return "custom"


def run_demonstration(
    application,
    scenarios: tuple[str, ...] | list[str] | None = None,
) -> Demonstration:
    """Run the full deterministic pipeline on simulated telemetry.

    Builds the historical dataset, ingests every record through the real
    ingestion service, derives baselines from the leading normal batches,
    analyzes the remaining window with the real intelligence services, and
    persists incidents plus evidence through the selected backend.

    ``scenarios`` selects the simulation timeline. ``None`` uses the full
    default timeline; a shorter list produces a smaller, faster analysis that
    is still a complete run of the same pipeline.
    """
    storage = application.storage
    dataset = HistoricalDatasetBuilder().build(
        start_time=DEMO_START, scenarios=scenarios
    )

    dataset_service = HistoricalDatasetService(
        builder=HistoricalDatasetBuilder(),
        ingestion=TelemetryIngestionService(storage),
    )
    # Persist the very dataset that is analyzed below. The service tags every
    # record with source='simulation' and returns those same objects, so the
    # incidents and evidence derived from them carry the same provenance as
    # the stored telemetry. Analyzing a separate, untagged copy would leave
    # every incident without an attributable source.
    dataset_service.build_and_persist(start_time=DEMO_START, dataset=dataset)

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
        timeline=timeline_label(scenarios),
    )


def split_records(records: list) -> tuple[list, list, list, list]:
    """Split telemetry records into metrics, logs, events, deployments."""
    metrics = [r for r in records if isinstance(r, Metric)]
    logs = [r for r in records if isinstance(r, Log)]
    events = [r for r in records if isinstance(r, Event)]
    deployments = [r for r in records if isinstance(r, Deployment)]
    return metrics, logs, events, deployments


__all__ = [
    "DEMO_HISTORY_BATCHES",
    "DEMO_START",
    "FULL_SCENARIOS",
    "QUICK_SCENARIOS",
    "TIMELINE_PROFILES",
    "Demonstration",
    "resolve_scenarios",
    "run_demonstration",
    "split_records",
    "timeline_label",
]
