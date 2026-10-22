"""UI-facing services for the RESON Streamlit application.

This module adapts the existing backend (ingestion, storage, behavior and
incident intelligence) for page rendering. It contains no intelligence
algorithms of its own: every analytical result comes from the real services
in ``src/``. All demonstration telemetry is produced by the deterministic
simulator and is always labeled as simulated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Iterable

from src.common.timeutil import (
    chart_axis_title,
    format_clock,
    format_timestamp,
    resolve_timezone,
    timezone_label,
    to_chart_time,
    zone_offset_label,
)
from src.data.collector import CollectionResult, collection_to_metrics
from src.data.demonstration import (
    DEMO_HISTORY_BATCHES,
    DEMO_START,
    FULL_SCENARIOS,
    QUICK_SCENARIOS,
    TIMELINE_PROFILES,
    Demonstration,
    resolve_scenarios,
    run_demonstration,
    timeline_label,
)
from src.data.ingestion import TelemetryIngestionService
from src.data.monitoring import (
    CollectorStatus,
    ModeContext,
    MonitoringMode,
    StatusReport,
    derive_status,
    host_inventory,
    latest_metric_values,
    latest_observation,
    metric_series,
    process_inventory,
    resolve_mode,
    source_inventory,
)
from src.data.schemas import Event, Incident
from src.data.storage import Storage
from src.incidents.lifecycle import IncidentLifecycle
from src.incidents.service import IncidentIntelligenceService
from src.intelligence.baseline import BaselineReadiness, assess_series_readiness
from src.intelligence.historical_comparison import (
    HistoricalComparisonResult,
    compare_periods,
)
from src.intelligence.models import BaselineKey
from src.preprocessing.normalizer import group_metrics
from src.orchestrator import RESONApplication, create_application

DEMO_START = datetime(2026, 1, 1, tzinfo=timezone.utc)

#: Batches 1-3 of the default dataset are normal telemetry used for history.
DEMO_HISTORY_BATCHES = 3

#: Public surface of this module. The pages import from here; the monitoring
#: helpers are re-exported so a page never needs to reach into ``src/``
#: directly.
__all__ = [
    "DEMO_START",
    "DEMO_HISTORY_BATCHES",
    "FULL_SCENARIOS",
    "QUICK_SCENARIOS",
    "TIMELINE_PROFILES",
    "Demonstration",
    "DashboardContext",
    "MonitoringMode",
    "ModeContext",
    "StatusReport",
    "CollectorStatus",
    "build_application",
    "build_context",
    "monitoring_context",
    "storage_counts",
    "storage_health",
    "source_inventory",
    "host_inventory",
    "latest_observation",
    "latest_metric_values",
    "metric_series",
    "process_inventory",
    "resolve_mode",
    "resolve_scenarios",
    "derive_status",
    "run_demonstration",
    "split_records",
    "timeline_label",
    "incident_label",
    "ordered_incidents",
    "severity_explanation",
    "research_for",
    "historical_matches_for",
    "metric_keys",
    "series_for_key",
    "recent_events",
    "mark_investigating",
    "observation_publish",
    "host_readiness",
    "compare_today_vs_prior_day",
    "format_timestamp",
    "format_clock",
    "timezone_label",
    "to_chart_time",
    "chart_axis_title",
    "resolve_timezone",
]


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


def storage_counts(
    storage: Storage,
    source: str | None = None,
) -> dict[str, int]:
    """Return stored record counts by type for overview display.

    When ``source`` is given, only records carrying that telemetry source are
    counted, so a live dashboard never mixes demonstration records into its
    headline numbers. The counts are produced by database-side aggregation
    where the backend supports it, so the cost does not grow with the
    retained history.
    """
    counts = storage.count_records(source=source)
    return {
        "metrics": counts.get("metrics", 0),
        "logs": counts.get("logs", 0),
        "events": counts.get("events", 0),
        "deployments": counts.get("deployments", 0),
        "incidents": len(storage.get_incidents(source=source)),
        "evidence": len(storage.get_evidence(source=source)),
        "services": len(storage.list_hosts(source=source)),
    }


def split_records(records: list) -> tuple[list, list, list, list]:
    """Split telemetry records into metrics, logs, events, deployments."""
    from src.data.demonstration import split_records as _split_records

    return _split_records(records)


def recent_events(
    storage: Storage,
    source: str | None = None,
    host_id: str | None = None,
    limit: int = 25,
) -> list[Event]:
    """Return the most recent stored events, newest first.

    Events are filtered by telemetry source so a demonstration view never
    shows live system events (or the reverse).
    """
    events = storage.get_events(source=source, host_id=host_id)
    events.sort(key=lambda item: item.timestamp, reverse=True)
    return events[: max(1, limit)]


def incident_label(incident: Incident, tz: tzinfo | None = None) -> str:
    """Return a compact selector label for an incident.

    The timestamp is rendered in ``tz`` (the display timezone) so the
    selector matches the clock shown on the page.
    """
    severity = getattr(incident.severity, "value", incident.severity)
    return (
        f"{str(incident.id)[:8]} · {severity} · "
        f"{format_timestamp(incident.start_time, tz, fmt='%Y-%m-%d %H:%M')}"
    )


def ordered_incidents(
    storage: Storage,
    source: str | None = None,
) -> list[Incident]:
    """Return stored incidents newest-first for selectors and registries.

    When ``source`` is given, only incidents detected from that telemetry
    source are returned, so a demonstration view never shows live incidents
    (or the reverse). Incidents stored before provenance was recorded carry
    no source and are therefore only visible when no source filter applies.
    """
    return sorted(
        storage.get_incidents(source=source),
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

    ``provenance`` reports whether the backend can store the source and host
    of a record. A backend that cannot is reported so the interface can say
    so instead of implying every record is attributable.
    """
    try:
        counts = storage_counts(storage)
        result = {
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
        result = {
            "operational": False,
            "error": str(exc),
            "counts": {},
            "backend": type(storage).__name__,
        }

    try:
        result["provenance"] = storage.provenance_capability()
    except Exception as exc:  # capability probing must never break the UI
        result["provenance"] = {
            "supported": False,
            "tables": {},
            "notes": [f"Provenance capability could not be determined: {exc}"],
        }
    return result


def observation_publish(ingestion: TelemetryIngestionService):
    """Return a collector publish callback that ingests real measurements.

    The callback converts the collection result (host, processes, disk,
    network, battery, sensors) into the canonical telemetry contract and
    ingests it through the existing ingestion service. Collection failures
    (exceptions raised by ingestion) propagate to the collector's error state
    rather than being swallowed.

    Both callback shapes are supported: the full
    :class:`~src.data.collector.CollectionResult` (one argument) and the
    legacy ``(host, processes)`` pair (two arguments).
    """

    def publish(*args) -> None:
        if len(args) == 1:
            result = args[0]
        else:
            host, processes = args[0], args[1]
            result = CollectionResult(host=host, processes=tuple(processes))
        records = collection_to_metrics(result)
        if records:
            ingestion.ingest_batch(records)

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


# ---------------------------------------------------------------------------
# Monitoring context (mode + honest status)
# ---------------------------------------------------------------------------


@dataclass
class DashboardContext:
    """Everything a page needs to render truthfully.

    Pages receive this object instead of a loose set of arguments so that the
    operating mode, the telemetry source in view, the derived collector
    status, and the available hosts are always consistent across pages.
    """

    mode: ModeContext
    status: StatusReport
    storage: Storage | None
    collector: object | None
    demo: Demonstration | None = None
    backend_name: str = "unknown"
    source: str | None = None
    sources: list = field(default_factory=list)
    hosts: list = field(default_factory=list)
    host_id: str | None = None
    display_tz: tzinfo = field(default_factory=resolve_timezone)
    """The timezone every timestamp in this view is rendered in.

    Storage is always UTC; this only affects presentation. The value is
    never ``None`` so a page can format a timestamp without guessing.
    """

    @property
    def timezone_name(self) -> str:
        """Return the human-readable name of the display timezone."""
        return timezone_label(self.display_tz)

    def format_timestamp(
        self,
        value: datetime | None,
        *,
        fmt: str = "%Y-%m-%d %H:%M:%S",
        with_zone: bool = True,
    ) -> str:
        """Format a stored UTC timestamp in the display timezone."""
        return format_timestamp(value, self.display_tz, fmt=fmt, with_zone=with_zone)

    def format_clock(self, value: datetime | None) -> str:
        """Format a stored UTC timestamp as a clock time in the display zone."""
        return format_clock(value, self.display_tz)

    def chart_axis_title(self) -> str:
        """Return the chart axis title naming the display timezone."""
        return chart_axis_title(self.display_tz)

    def zone_offset(self, value: datetime | None = None) -> str:
        """Return the UTC offset of the display timezone.

        The offset is computed for the current instant when no reference
        timestamp is given, so a zone that changes offset across a
        daylight-saving boundary reports the offset in force right now.
        """
        return zone_offset_label(value or datetime.now(timezone.utc), self.display_tz)

    def to_chart_time(self, value: datetime) -> datetime:
        """Convert a stored UTC timestamp to a chart-ready local value."""
        return to_chart_time(value, self.display_tz)

    @property
    def simulated(self) -> bool:
        """Whether the current view displays synthetic data."""
        return self.mode.simulated

    @property
    def live(self) -> bool:
        """Whether the current view may be described as live."""
        return self.status.is_live

    @property
    def available(self) -> bool:
        """Whether the current mode has any data to display."""
        return self.mode.available


def build_context(
    storage: Storage | None,
    collector,
    mode: MonitoringMode | str,
    demo: Demonstration | None = None,
    backend_name: str = "unknown",
    display_tz: tzinfo | str | None = None,
) -> DashboardContext:
    """Build the dashboard context for the current mode and collector.

    ``display_tz`` is the timezone timestamps are rendered in. It never
    affects what is stored or queried — only how it is presented.
    """
    context, status = monitoring_context(
        storage, collector, mode, demo_active=demo is not None
    )
    sources = source_inventory(storage) if storage is not None else []
    hosts = host_inventory(storage, source=context.source) if storage else []
    if isinstance(display_tz, str) or display_tz is None:
        display_tz = resolve_timezone(display_tz)
    return DashboardContext(
        mode=context,
        status=status,
        storage=storage,
        collector=collector,
        demo=demo,
        backend_name=backend_name,
        source=context.source,
        sources=sources,
        hosts=hosts,
        host_id=getattr(collector, "host_id", None),
        display_tz=display_tz,
    )


def monitoring_context(
    storage: Storage | None,
    collector,
    mode: MonitoringMode | str,
    demo_active: bool = False,
) -> tuple[ModeContext, StatusReport]:
    """Resolve the operating mode and the derived collector status.

    The two are computed together because they share inputs: the mode decides
    which telemetry source is in view, and the status must be derived from
    that same source so a demonstration mode can never be reported as a live
    pipeline.
    """
    context = resolve_mode(
        mode,
        collector=collector,
        storage=storage,
        demo_active=demo_active,
    )
    if context.simulated:
        status = StatusReport(
            status=CollectorStatus.DEMONSTRATION,
            detail=(
                "Displaying deterministic demonstration data. This is not "
                "live system telemetry."
            ),
            last_observation=(
                latest_observation(storage, source=context.source)
                if storage is not None
                else None
            ),
            stale=False,
        )
        return context, status

    status = derive_status(
        collector=collector,
        storage=storage,
        source=context.source,
        host_id=getattr(collector, "host_id", None),
    )
    return context, status
