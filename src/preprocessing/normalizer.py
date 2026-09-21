"""Telemetry normalization for RESON behavioral intelligence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable
from uuid import UUID

from src.data.schemas import Metric


@dataclass(frozen=True)
class MetricObservation:
    """A single normalized metric value with its source identity preserved."""

    record_id: UUID
    timestamp: datetime
    value: float


@dataclass(frozen=True)
class MetricSeries:
    """Deterministically ordered observations for one metric identity."""

    service: str
    environment: str
    metric_name: str
    observations: tuple[MetricObservation, ...]

    @property
    def count(self) -> int:
        """Return the number of observations in this series."""
        return len(self.observations)


def _observation_sort_key(observation: MetricObservation) -> tuple[datetime, UUID]:
    return (observation.timestamp, observation.record_id)


def _series_sort_key(series: MetricSeries) -> tuple[str, str, str]:
    return (series.service, series.environment, series.metric_name)


def group_metrics(metrics: Iterable[Metric]) -> list[MetricSeries]:
    """Group telemetry metrics into deterministic per-identity series.

    Records are grouped by ``(service, environment, metric_name)`` and each
    resulting series is sorted by timestamp (record ID as a deterministic
    tie-breaker). Source telemetry objects are never mutated.
    """
    grouped: dict[tuple[str, str, str], list[MetricObservation]] = {}

    for metric in metrics:
        key = (metric.service, metric.environment, metric.metric_name)
        grouped.setdefault(key, []).append(
            MetricObservation(
                record_id=metric.id,
                timestamp=metric.timestamp,
                value=metric.value,
            )
        )

    series: list[MetricSeries] = []

    for key in sorted(grouped):
        observations = sorted(grouped[key], key=_observation_sort_key)
        series.append(
            MetricSeries(
                service=key[0],
                environment=key[1],
                metric_name=key[2],
                observations=tuple(observations),
            )
        )

    return series


def slice_series(
    series: MetricSeries,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> MetricSeries:
    """Return a new series restricted to the interval [window_start, window_end).

    Boundaries are inclusive on the start and exclusive on the end. An omitted
    boundary is unbounded. The input series is never mutated.
    """
    observations = [
        observation
        for observation in series.observations
        if (window_start is None or observation.timestamp >= window_start)
        and (window_end is None or observation.timestamp < window_end)
    ]

    return MetricSeries(
        service=series.service,
        environment=series.environment,
        metric_name=series.metric_name,
        observations=tuple(observations),
    )