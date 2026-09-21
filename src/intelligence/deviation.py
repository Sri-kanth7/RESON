"""Metric deviation detection for RESON behavioral intelligence.

A deviation compares one observation against its historical baseline. The
detector handles ordinary reference conditions (missing baseline, zero
baseline, constant baseline) as explicit deviation states rather than errors.
"""

from __future__ import annotations

from typing import Mapping

from src.intelligence.models import (
    BaselineKey,
    DeviationStatus,
    HistoricalBaseline,
    IntelligenceConfig,
    MetricDeviation,
)
from src.preprocessing.normalizer import MetricObservation, MetricSeries


class DeviationDetector:
    """Compare current observations against their historical baselines."""

    def __init__(self, config: IntelligenceConfig | None = None) -> None:
        self._config = config or IntelligenceConfig()

    def detect(
        self,
        key: BaselineKey,
        observation: MetricObservation,
        baseline: HistoricalBaseline | None,
    ) -> MetricDeviation:
        """Evaluate a single observation against an optional baseline.

        When no usable baseline exists the deviation is reported with status
        ``insufficient_reference`` instead of raising.
        """
        if not self._usable_baseline(baseline):
            return MetricDeviation(
                record_id=observation.record_id,
                timestamp=observation.timestamp,
                key=key,
                value=observation.value,
                status=DeviationStatus.INSUFFICIENT_REFERENCE,
            )

        assert baseline is not None
        assert baseline.statistics.mean is not None

        mean = baseline.statistics.mean
        std = baseline.statistics.std

        absolute_deviation = observation.value - mean

        relative_deviation = None
        if mean != 0.0:
            relative_deviation = absolute_deviation / abs(mean)

        z_score = None
        if std is not None and std > 0.0:
            z_score = absolute_deviation / std

        status = self._classify(mean=mean, std=std, z_score=z_score, relative_deviation=relative_deviation)

        return MetricDeviation(
            record_id=observation.record_id,
            timestamp=observation.timestamp,
            key=key,
            value=observation.value,
            baseline_mean=mean,
            absolute_deviation=absolute_deviation,
            relative_deviation=relative_deviation,
            z_score=z_score,
            status=status,
        )

    def detect_series(
        self,
        series: MetricSeries,
        baselines: Mapping[BaselineKey, HistoricalBaseline],
    ) -> list[MetricDeviation]:
        """Evaluate every observation in a series against matching baselines."""
        key = BaselineKey(
            service=series.service,
            environment=series.environment,
            metric_name=series.metric_name,
        )
        baseline = baselines.get(key)

        return [
            self.detect(key, observation, baseline)
            for observation in series.observations
        ]

    def _usable_baseline(self, baseline: HistoricalBaseline | None) -> bool:
        if baseline is None:
            return False
        if baseline.statistics.count < self._config.min_observations_per_baseline:
            return False
        return baseline.statistics.mean is not None

    def _classify(
        self,
        *,
        mean: float,
        std: float | None,
        z_score: float | None,
        relative_deviation: float | None,
    ) -> DeviationStatus:
        if std is not None and std > 0.0:
            assert z_score is not None
            if abs(z_score) >= self._config.z_score_threshold:
                return DeviationStatus.ANOMALOUS
            return DeviationStatus.NORMAL

        if mean == 0.0:
            return DeviationStatus.ZERO_BASELINE

        # Constant baseline (no variance) with a non-zero mean.
        assert relative_deviation is not None
        if abs(relative_deviation) >= self._config.relative_deviation_threshold:
            return DeviationStatus.ANOMALOUS
        return DeviationStatus.CONSTANT_BASELINE