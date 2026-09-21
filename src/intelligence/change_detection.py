"""Sustained change detection for RESON behavioral intelligence.

A change point requires a temporally consecutive sequence of anomalous
evaluations. Consecutiveness is defined by timestamps (successive evaluations
within ``max_gap_seconds``) rather than list adjacency, so unrelated gaps are
never merged into a single sustained run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from src.intelligence.models import (
    ChangePoint,
    IntelligenceConfig,
    MetricDeviation,
)


@dataclass(frozen=True)
class BatchEvaluation:
    """All deviations observed at a single timestamp."""

    timestamp: datetime
    deviations: tuple[MetricDeviation, ...]

    @property
    def is_anomalous(self) -> bool:
        """Return whether any deviation at this timestamp is anomalous."""
        return any(deviations.is_anomalous for deviations in self.deviations)


def group_deviations_by_timestamp(
    deviations: Iterable[MetricDeviation],
) -> list[BatchEvaluation]:
    """Group deviations by timestamp, ordered deterministically."""
    grouped: dict[datetime, list[MetricDeviation]] = {}

    for deviation in deviations:
        grouped.setdefault(deviation.timestamp, []).append(deviation)

    return [
        BatchEvaluation(
            timestamp=timestamp,
            deviations=tuple(
                sorted(evaluations, key=lambda d: d.record_id)
            ),
        )
        for timestamp in sorted(grouped)
        for evaluations in [grouped[timestamp]]
    ]


class ChangeDetector:
    """Detect sustained anomalous runs as change points."""

    def __init__(self, config: IntelligenceConfig | None = None) -> None:
        self._config = config or IntelligenceConfig()

    def detect(
        self,
        evaluations: Iterable[BatchEvaluation],
    ) -> list[ChangePoint]:
        """Return change points for sustained anomalous sequences."""
        ordered = sorted(evaluations, key=lambda evaluation: evaluation.timestamp)

        change_points: list[ChangePoint] = []
        current: list[BatchEvaluation] = []

        for evaluation in ordered:
            if evaluation.is_anomalous:
                if current and not self._within_gap(current[-1], evaluation):
                    self._emit(current, change_points)
                    current = []
                current.append(evaluation)
            else:
                if current:
                    self._emit(current, change_points)
                    current = []

        if current:
            self._emit(current, change_points)

        change_points.sort(key=lambda point: point.start_time)
        return change_points

    def detect_deviations(
        self,
        deviations: Iterable[MetricDeviation],
    ) -> list[ChangePoint]:
        """Convenience wrapper that groups deviations before detection."""
        return self.detect(group_deviations_by_timestamp(deviations))

    def _within_gap(
        self,
        previous: BatchEvaluation,
        current: BatchEvaluation,
    ) -> bool:
        gap = (current.timestamp - previous.timestamp).total_seconds()
        return gap <= self._config.max_gap_seconds

    def _emit(
        self,
        run: list[BatchEvaluation],
        change_points: list[ChangePoint],
    ) -> None:
        if len(run) < self._config.consecutive_anomalous_batches:
            return

        affected_keys = sorted(
            {
                deviation.key
                for evaluation in run
                for deviation in evaluation.deviations
                if deviation.is_anomalous
            },
            key=lambda key: (key.service, key.environment, key.metric_name),
        )
        source_ids = sorted(
            {
                deviation.record_id
                for evaluation in run
                for deviation in evaluation.deviations
                if deviation.is_anomalous
            }
        )

        change_points.append(
            ChangePoint(
                start_time=run[0].timestamp,
                end_time=run[-1].timestamp,
                consecutive_batches=len(run),
                affected_metric_keys=affected_keys,
                source_record_ids=source_ids,
                explanation=(
                    f"Sustained anomalous behavior across "
                    f"{len(run)} temporally consecutive batches from "
                    f"{run[0].timestamp} to {run[-1].timestamp} involving "
                    f"{affected_keys}."
                ),
            )
        )