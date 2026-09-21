"""High-level orchestration for RESON behavioral intelligence (Phase 2).

The service composes normalization, baseline building, deviation detection,
change detection, fingerprinting, and the Behavior Shift Score. It does not
duplicate their calculations.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Mapping, Sequence

from src.data.schemas import Deployment, Event, Log, Metric
from src.intelligence.baseline import BaselineBuilder
from src.intelligence.change_detection import ChangeDetector
from src.intelligence.deviation import DeviationDetector
from src.intelligence.fingerprint import FingerprintBuilder
from src.intelligence.models import (
    BaselineKey,
    BehaviorAnalysis,
    HistoricalBaseline,
    IntelligenceConfig,
    ServiceBehavior,
)
from src.intelligence.shift import BehaviorShiftCalculator
from src.preprocessing.normalizer import MetricSeries, group_metrics, slice_series


class BehaviorIntelligenceService:
    """Coordinate the Phase 2 pipeline into a BehaviorAnalysis result."""

    def __init__(
        self,
        baseline_builder: BaselineBuilder | None = None,
        deviation_detector: DeviationDetector | None = None,
        change_detector: ChangeDetector | None = None,
        fingerprint_builder: FingerprintBuilder | None = None,
        shift_calculator: BehaviorShiftCalculator | None = None,
        config: IntelligenceConfig | None = None,
    ) -> None:
        self._config = config or IntelligenceConfig()
        self._baseline_builder = baseline_builder or BaselineBuilder(self._config)
        self._deviation_detector = deviation_detector or DeviationDetector(self._config)
        self._change_detector = change_detector or ChangeDetector(self._config)
        self._fingerprint_builder = fingerprint_builder or FingerprintBuilder(self._config)
        self._shift_calculator = shift_calculator or BehaviorShiftCalculator(self._config)

    def build_baselines(
        self,
        metrics: Iterable[Metric],
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> dict[BaselineKey, HistoricalBaseline]:
        """Build historical baselines from metrics in an explicit historical window."""
        historical = [
            slice_series(series, window_start, window_end)
            for series in group_metrics(metrics)
        ]

        return self._baseline_builder.build_baselines(historical)

    def analyze(
        self,
        baselines: Mapping[BaselineKey, HistoricalBaseline],
        metrics: Iterable[Metric],
        *,
        logs: Iterable[Log] = (),
        events: Iterable[Event] = (),
        deployments: Iterable[Deployment] = (),
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> BehaviorAnalysis:
        """Analyze a current observation window against supplied baselines.

        The analysis window is ``[window_start, window_end)``. When ``window_end``
        is omitted it is closed at the final observed timestamp (plus one
        microsecond) so the last observation is included.
        """
        current = [
            slice_series(series, window_start, window_end)
            for series in group_metrics(metrics)
        ]
        current = [series for series in current if series.count > 0]

        effective_start, effective_end = self._resolve_analysis_window(
            current,
            window_start,
            window_end,
        )

        series_by_key = {
            BaselineKey(
                service=series.service,
                environment=series.environment,
                metric_name=series.metric_name,
            ): series
            for series in current
        }

        deviations = [
            deviation
            for series in current
            for deviation in self._deviation_detector.detect_series(
                series,
                baselines,
            )
        ]

        change_points = self._change_detector.detect_deviations(deviations)

        log_list = list(logs)
        event_list = list(events)
        deployment_list = list(deployments)

        service_names = sorted({series.service for series in current})
        service_results: list[ServiceBehavior] = []

        for service in service_names:
            service_results.append(
                self._analyze_service(
                    service=service,
                    current=current,
                    baselines=baselines,
                    deviations=deviations,
                    change_points=change_points,
                    logs=log_list,
                    events=event_list,
                    deployments=deployment_list,
                    window_start=effective_start,
                    window_end=effective_end,
                )
            )

        overall_score = (
            sum(result.shift.score for result in service_results)
            / len(service_results)
            if service_results
            else 0.0
        )

        changed_services = sorted(
            {
                deviation.key.service
                for deviation in deviations
                if deviation.is_anomalous
            }
        )

        historical_start, historical_end = self._historical_window(baselines)

        return BehaviorAnalysis(
            historical_window_start=historical_start,
            historical_window_end=historical_end,
            analysis_window_start=effective_start,
            analysis_window_end=effective_end,
            service_results=service_results,
            overall_score=overall_score,
            changed_services=changed_services,
        )

    def analyze_history(
        self,
        *,
        historical_metrics: Iterable[Metric],
        metrics: Iterable[Metric],
        historical_window: tuple[datetime, datetime] | None = None,
        logs: Iterable[Log] = (),
        events: Iterable[Event] = (),
        deployments: Iterable[Deployment] = (),
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> BehaviorAnalysis:
        """Convenience entry point: build history baselines, then analyze.

        ``historical_window`` is ``(start, end)`` for the baseline window.
        This keeps the "Current vs Historical" distinction explicit.
        """
        historical_start = None
        historical_end = None

        if historical_window is not None:
            historical_start, historical_end = historical_window

        baselines = self.build_baselines(
            historical_metrics,
            window_start=historical_start,
            window_end=historical_end,
        )

        return self.analyze(
            baselines,
            metrics,
            logs=logs,
            events=events,
            deployments=deployments,
            window_start=window_start,
            window_end=window_end,
        )

    def _resolve_analysis_window(
        self,
        series: Sequence[MetricSeries],
        window_start: datetime | None,
        window_end: datetime | None,
    ) -> tuple[datetime | None, datetime | None]:
        timestamps = [
            observation.timestamp
            for one_series in series
            for observation in one_series.observations
        ]

        effective_start = window_start or (min(timestamps) if timestamps else None)
        effective_end = window_end or (
            max(timestamps) + timedelta(microseconds=1) if timestamps else None
        )

        return effective_start, effective_end

    def _analyze_service(
        self,
        *,
        service: str,
        current: Sequence[MetricSeries],
        baselines: Mapping[BaselineKey, HistoricalBaseline],
        deviations: list,
        change_points: list,
        logs: list[Log],
        events: list[Event],
        deployments: list[Deployment],
        window_start: datetime | None,
        window_end: datetime | None,
    ) -> ServiceBehavior:
        service_series = {
            BaselineKey(
                service=series.service,
                environment=series.environment,
                metric_name=series.metric_name,
            ): series
            for series in current
            if series.service == service
        }

        service_deviations = [
            deviation for deviation in deviations if deviation.key.service == service
        ]
        service_change_points = [
            point
            for point in change_points
            if any(key.service == service for key in point.affected_metric_keys)
        ]
        service_logs = [log for log in logs if log.service == service]
        service_events = [event for event in events if event.service == service]
        service_deployments = [
            deployment for deployment in deployments if deployment.service == service
        ]

        fingerprint = self._fingerprint_builder.build(
            window_start,
            window_end,
            series_by_key=service_series,
            deviations=service_deviations,
            logs=service_logs,
            events=service_events,
            deployments=service_deployments,
            change_points=service_change_points,
        )

        shift = self._shift_calculator.calculate(
            service_deviations,
            change_points=service_change_points,
        )

        return ServiceBehavior(
            service=service,
            fingerprint=fingerprint,
            shift=shift,
        )

    def _historical_window(
        self,
        baselines: Mapping[BaselineKey, HistoricalBaseline],
    ) -> tuple[datetime | None, datetime | None]:
        if not baselines:
            return None, None

        values = list(baselines.values())
        return (
            min(baseline.start_time for baseline in values),
            max(baseline.end_time for baseline in values),
        )