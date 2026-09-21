"""Deterministic behavior fingerprint construction for RESON.

A fingerprint is a structured, serializable summary of system behavior over a
window. It is not an embedding, a vector, or a probabilistic model; every
field is derived deterministically from telemetry and baseline evidence.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Iterable, Mapping

from src.data.schemas import Deployment, Event, Log
from src.intelligence.baseline import compute_statistics
from src.intelligence.models import (
    BaselineKey,
    BehaviorFingerprint,
    BehaviorState,
    ChangePoint,
    IntelligenceConfig,
    MetricBehavior,
    MetricDeviation,
)
from src.preprocessing.normalizer import MetricSeries


class FingerprintBuilder:
    """Build deterministic behavior fingerprints for an analysis window."""

    def __init__(self, config: IntelligenceConfig | None = None) -> None:
        self._config = config or IntelligenceConfig()

    def build(
        self,
        window_start: datetime,
        window_end: datetime,
        *,
        series_by_key: Mapping[BaselineKey, MetricSeries],
        deviations: Iterable[MetricDeviation] = (),
        logs: Iterable[Log] = (),
        events: Iterable[Event] = (),
        deployments: Iterable[Deployment] = (),
        change_points: Iterable[ChangePoint] = (),
    ) -> BehaviorFingerprint:
        """Build a fingerprint for the supplied window evidence."""
        deviation_list = list(deviations)
        change_point_list = list(change_points)
        log_list = list(logs)
        event_list = list(events)
        deployment_list = list(deployments)

        metric_behaviors = self._metric_behaviors(
            series_by_key=series_by_key,
            deviations=deviation_list,
        )

        services = sorted(
            {
                key.service
                for key in list(series_by_key) + [
                    deviation.key for deviation in deviation_list
                ]
            }
        )
        environments = {
            key.environment
            for key in list(series_by_key) + [
                deviation.key for deviation in deviation_list
            ]
        }

        log_summary = dict(sorted(Counter(log.level for log in log_list).items()))
        event_summary = dict(
            sorted(Counter(event.event_type for event in event_list).items())
        )

        source_ids = {
            observation.record_id
            for series in series_by_key.values()
            for observation in series.observations
        }
        source_ids.update(
            record_id for point in change_point_list for record_id in point.source_record_ids
        )

        return BehaviorFingerprint(
            start_time=window_start,
            end_time=window_end,
            services=services,
            environment=environments.pop() if len(environments) == 1 else None,
            metric_behaviors=metric_behaviors,
            log_summary=log_summary,
            event_summary=event_summary,
            deployment_count=len(deployment_list),
            change_point_count=len(change_point_list),
            behavior_state=self._behavior_state(
                change_points=change_point_list,
                deviations=deviation_list,
            ),
            source_record_ids=sorted(source_ids),
        )

    def _metric_behaviors(
        self,
        *,
        series_by_key: Mapping[BaselineKey, MetricSeries],
        deviations: Iterable[MetricDeviation],
    ) -> list[MetricBehavior]:
        deviation_list = list(deviations)

        behaviors: list[MetricBehavior] = []

        for key in sorted(
            series_by_key,
            key=lambda item: (item.service, item.environment, item.metric_name),
        ):
            series = series_by_key[key]
            values = [observation.value for observation in series.observations]
            statistics = compute_statistics(values)

            key_deviations = [d for d in deviation_list if d.key == key]
            anomaly_count = sum(d.is_anomalous for d in key_deviations)

            abs_z_scores = [
                abs(d.z_score) for d in key_deviations if d.z_score is not None
            ]
            peak_abs_z = max(abs_z_scores) if abs_z_scores else None

            behaviors.append(
                MetricBehavior(
                    key=key,
                    observation_count=statistics.count,
                    mean=statistics.mean,
                    std=statistics.std,
                    min=statistics.min,
                    max=statistics.max,
                    anomaly_count=anomaly_count,
                    peak_abs_z=peak_abs_z,
                )
            )

        return behaviors

    def _behavior_state(
        self,
        *,
        change_points: list[ChangePoint],
        deviations: list[MetricDeviation],
    ) -> BehaviorState:
        if change_points:
            return BehaviorState.CHANGING
        if any(deviations.is_anomalous for deviations in deviations):
            return BehaviorState.DEVIATED
        return BehaviorState.NORMAL