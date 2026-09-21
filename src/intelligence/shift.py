"""Behavior Shift Score for RESON behavioral intelligence.

The score is a bounded, deterministic measure of behavioral deviation
magnitude. It is explicitly NOT a probability of any kind (not incident
probability, root-cause probability, failure likelihood, or AI confidence).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from src.intelligence.models import (
    BehaviorShift,
    ChangePoint,
    IntelligenceConfig,
    MetricContribution,
    MetricDeviation,
)


class BehaviorShiftCalculator:
    """Calculate a transparent, per-metric Behavior Shift Score."""

    def __init__(self, config: IntelligenceConfig | None = None) -> None:
        self._config = config or IntelligenceConfig()

    def calculate(
        self,
        deviations: Iterable[MetricDeviation],
        change_points: Iterable[ChangePoint] = (),
    ) -> BehaviorShift:
        """Compute the Behavior Shift Score from deviation evidence.

        For each metric key the contribution is ``anomaly_rate * peak_magnitude``
        where ``anomaly_rate`` is the share of anomalous observations and
        ``peak_magnitude`` is a bounded measure of the maximum deviation
        magnitude. The score is the mean of all metric contributions and is
        0.0 when no metrics contribute.
        """
        grouped: dict[object, list[MetricDeviation]] = defaultdict(list)

        for deviation in deviations:
            grouped[deviation.key].append(deviation)

        contributions: list[MetricContribution] = []

        for key in sorted(
            grouped,
            key=lambda item: (item.service, item.environment, item.metric_name),
        ):
            evaluations = grouped[key]
            total = len(evaluations)
            anomalous = [evaluation for evaluation in evaluations if evaluation.is_anomalous]

            anomaly_rate = len(anomalous) / total if total else 0.0
            peak_magnitude = self._peak_magnitude(evaluations, anomalous)
            contribution = anomaly_rate * peak_magnitude

            contributions.append(
                MetricContribution(
                    key=key,
                    anomaly_rate=anomaly_rate,
                    peak_magnitude=peak_magnitude,
                    contribution=contribution,
                )
            )

        score = (
            sum(contribution.contribution for contribution in contributions)
            / len(contributions)
            if contributions
            else 0.0
        )

        change_point_list = list(change_points)

        return BehaviorShift(
            score=score,
            contributions=contributions,
            change_detected=bool(change_point_list),
            explanation=self._explanation(score, contributions),
        )

    def _peak_magnitude(
        self,
        evaluations: list[MetricDeviation],
        anomalous: list[MetricDeviation],
    ) -> float:
        abs_z_scores = [
            abs(evaluation.z_score)
            for evaluation in evaluations
            if evaluation.z_score is not None
        ]

        if abs_z_scores:
            peak = max(abs_z_scores)
            return min(1.0, peak / self._config.max_z_magnitude)

        # No z-scores were available; an anomaly could only have been flagged
        # through the relative-deviation fallback, which we treat as fully
        # magnified.
        return 1.0 if anomalous else 0.0

    def _explanation(
        self,
        score: float,
        contributions: list[MetricContribution],
    ) -> str:
        if not contributions:
            return (
                "Behavior Shift Score is 0.0 because no metrics were "
                "available to evaluate."
            )

        ordered = sorted(
            contributions,
            key=lambda contribution: contribution.contribution,
            reverse=True,
        )
        top = ", ".join(
            f"{contribution.key.service}/{contribution.key.metric_name} "
            f"{contribution.contribution:.3f}"
            for contribution in ordered[:3]
        )

        return (
            f"Behavior Shift Score {score:.3f} is the mean per-metric "
            f"contribution (anomaly rate x peak magnitude) across "
            f"{len(contributions)} metric(s). Largest contributions: {top}."
        )