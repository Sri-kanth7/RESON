"""Day-over-day and period-over-period comparison of retained observations.

RESON compares real observation windows against eligible historical windows
using the existing intelligence engine only -- :class:`~src.intelligence.service.BehaviorIntelligenceService`
for baselines and deviation analysis, and :func:`~src.incidents.reconstruction.derive_deviation_evidence`
for the deviation stream. No detector, baseline, or comparison algorithm is
duplicated here.

The comparison is evidence-based: it reports the windows, sample counts, and
baseline readiness it actually observed, and it refuses to compare when there
is no usable history rather than fabricating a baseline or treating missing
history as unchanged behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from src.data.schemas import Metric
from src.data.storage import Storage
from src.intelligence.baseline import (
    BaselineBuilder,
    BaselineReadiness,
    assess_series_readiness,
)
from src.intelligence.models import (
    BaselineKey,
    BehaviorAnalysis,
    ChangePoint,
    IntelligenceConfig,
    MetricDeviation,
)
from src.intelligence.service import BehaviorIntelligenceService
from src.incidents.reconstruction import derive_deviation_evidence
from src.preprocessing.normalizer import group_metrics, slice_series


@dataclass
class HistoricalComparisonResult:
    """Outcome of comparing one observation window against a historical window."""

    sufficient: bool
    """Whether at least one metric has a READY baseline for the comparison."""

    current_window: tuple[datetime, datetime]
    historical_window: tuple[datetime, datetime]
    host_id: str
    source: str | None

    metrics_compared: list[BaselineKey] = field(default_factory=list)
    current_sample_counts: dict[BaselineKey, int] = field(default_factory=dict)
    historical_sample_counts: dict[BaselineKey, int] = field(default_factory=dict)
    baseline_readiness: dict[BaselineKey, BaselineReadiness] = field(default_factory=dict)

    analysis: BehaviorAnalysis | None = None
    deviations: list[MetricDeviation] = field(default_factory=list)
    change_points: list[ChangePoint] = field(default_factory=list)

    missing_metrics: list[str] = field(default_factory=list)
    """Requested metrics with no observations in either window."""

    incompatible_units: list[str] = field(default_factory=list)
    """Metrics whose current and historical units disagree."""

    reasons: list[str] = field(default_factory=list)
    """Human-readable reasons, including why a comparison could not complete."""


def _series_key(series) -> BaselineKey:
    return BaselineKey(
        service=series.service,
        environment=series.environment,
        metric_name=series.metric_name,
    )


def _key(service: str, environment: str, metric_name: str) -> BaselineKey:
    return BaselineKey(
        service=service,
        environment=environment,
        metric_name=metric_name,
    )


def _compatible_units(metrics: Iterable[Metric]) -> dict[str, str | None]:
    """Return the single unit used by each metric name, or None when mixed."""

    units_by_name: dict[str, set[str]] = {}
    for metric in metrics:
        units_by_name.setdefault(metric.metric_name, set()).add(metric.unit)

    resolved: dict[str, str | None] = {}
    for name, units in units_by_name.items():
        resolved[name] = next(iter(units)) if len(units) == 1 else None
    return resolved


def compare_periods(
    storage: Storage,
    *,
    host_id: str,
    source: str | None = None,
    metric_names: Iterable[str] | None = None,
    current_start: datetime,
    current_end: datetime,
    historical_start: datetime,
    historical_end: datetime,
    config: IntelligenceConfig | None = None,
) -> HistoricalComparisonResult:
    """Compare current observations with eligible historical observations.

    Uses observation timestamps (``Metric.timestamp``), never ingestion time.
    The historical window must end at or before the current window starts so
    observations are never counted twice and no look-ahead comparison happens.

    Only metrics belonging to ``host_id`` (carried in ``Metric.service``,
    following the existing telemetry convention) are considered. When
    ``source`` is supplied, observations whose ``source`` differs are excluded,
    so simulated and real telemetry are never compared as if they were the same
    source. Note: the Supabase backend does not persist ``source`` (its
    ``metrics`` table predates the column), so source filtering there applies
    to records already read; callers sharing one service between simulated and
    real telemetry should separate them by service instead.
    """

    intelligence_config = config or IntelligenceConfig()

    if historical_end > current_start:
        raise ValueError(
            "historical window must end at or before the current window starts "
            f"({historical_end.isoformat()} > {current_start.isoformat()}); "
            "refusing to count overlapping observations twice"
        )

    requested = (
        set(metric_names) if metric_names is not None else None
    )

    def _select(metrics: Iterable[Metric]) -> list[Metric]:
        selected = []
        for metric in metrics:
            if metric.service != host_id:
                continue
            if requested is not None and metric.metric_name not in requested:
                continue
            if source is not None and metric.source != source:
                continue
            selected.append(metric)
        return selected

    current_all = list(storage.get_metrics(
        service=host_id, start_time=current_start, end_time=current_end,
    ))
    historical_all = list(storage.get_metrics(
        service=host_id, start_time=historical_start, end_time=historical_end,
    ))

    current_metrics = _select(current_all)
    historical_metrics = _select(historical_all)

    result = HistoricalComparisonResult(
        sufficient=False,
        current_window=(current_start, current_end),
        historical_window=(historical_start, historical_end),
        host_id=host_id,
        source=source,
    )

    # Requested metrics that never appeared in either window, reported even
    # when one of the windows is empty.
    if requested is not None:
        observed = {
            metric.metric_name for metric in [*current_metrics, *historical_metrics]
        }
        result.missing_metrics = sorted(requested - observed)

    if not historical_metrics:
        result.reasons.append(
            f"no historical observations for host {host_id!r} in "
            f"{historical_start.isoformat()} to {historical_end.isoformat()}"
        )
        return result

    if not current_metrics:
        result.reasons.append(
            f"no current observations for host {host_id!r} in "
            f"{current_start.isoformat()} to {current_end.isoformat()}"
        )
        return result

    # Unit compatibility across both windows for every metric name involved.
    unit_map = _compatible_units([*current_metrics, *historical_metrics])
    for name, unit in unit_map.items():
        if unit is None:
            result.incompatible_units.append(name)
    compatible_names = {
        name for name, unit in unit_map.items() if unit is not None
    }

    current_by_key: dict[BaselineKey, list[Metric]] = {}
    historical_by_key: dict[BaselineKey, list[Metric]] = {}

    for metric in current_metrics:
        if metric.metric_name not in compatible_names:
            continue
        current_by_key.setdefault(
            _key(metric.service, metric.environment, metric.metric_name),
            [],
        ).append(metric)

    for metric in historical_metrics:
        if metric.metric_name not in compatible_names:
            continue
        historical_by_key.setdefault(
            _key(metric.service, metric.environment, metric.metric_name),
            [],
        ).append(metric)

    historical_series = [
        slice_series(
            series,
            historical_start,
            historical_end,
        )
        for series in group_metrics(historical_metrics)
        if series.service == host_id
        and (requested is None or series.metric_name in requested)
        and series.metric_name in compatible_names
    ]

    baselines = BaselineBuilder(intelligence_config).build_baselines(historical_series)

    series_by_key = {_series_key(series): series for series in historical_series}
    readiness: dict[BaselineKey, BaselineReadiness] = {}
    for key, series in series_by_key.items():
        # Readiness for comparison is sample-count based: is there enough
        # history to build a usable baseline? Recency staleness is a separate
        # UI concern (assess_series_readiness with a reference time) and is
        # not applied here, because a prior-day baseline is exactly the
        # reference a cross-day comparison is meant to use.
        readiness[key] = assess_series_readiness(series, intelligence_config)

    result.metrics_compared = sorted(
        compatibility_key
        for compatibility_key in {
            _key(series.service, series.environment, series.metric_name)
            for series in historical_series
        }
    )
    result.current_sample_counts = {
        key: len(series) for key, series in current_by_key.items()
    }
    result.historical_sample_counts = {
        key: len(series) for key, series in historical_by_key.items()
    }
    result.baseline_readiness = readiness

    ready_keys = [
        key for key, state in readiness.items() if state == BaselineReadiness.READY
    ]
    if not ready_keys:
        result.reasons.append(
            "no baseline is ready: every metric has insufficient or stale "
            f"history (minimum {intelligence_config.min_observations_per_baseline} observations)"
        )
        return result

    current_analysis_metrics = [
        metric
        for metric in current_metrics
        if _key(
            metric.service, metric.environment, metric.metric_name
        )
        in ready_keys
    ]

    service = BehaviorIntelligenceService()
    result.analysis = service.analyze(
        baselines,
        current_analysis_metrics,
        window_start=current_start,
        window_end=current_end,
    )
    result.deviations, result.change_points = derive_deviation_evidence(
        current_analysis_metrics,
        baselines,
        window_start=current_start,
        window_end=current_end,
    )
    result.sufficient = True
    result.reasons.append(
        f"compiled from {len(result.historical_sample_counts)} historical "
        f"metric(s) and {len(result.current_sample_counts)} current metric(s)"
    )
    return result
