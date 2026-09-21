"""Historical baseline construction for RESON behavioral intelligence.

Baselines are deterministic calculations over a supplied historical window.
They never include the current observation window; window selection is the
caller's responsibility.
"""

from __future__ import annotations

from statistics import mean, stdev
from typing import Iterable, Sequence

from src.intelligence.models import (
    BaselineKey,
    BaselineStatistics,
    HistoricalBaseline,
    IntelligenceConfig,
)
from src.preprocessing.normalizer import MetricSeries


def _percentile(sorted_values: list[float], percentile: float) -> float:
    """Compute a percentile using linear interpolation between closest ranks.

    For a sorted series of length ``n`` the rank position is
    ``(n - 1) * percentile`` and the result is a weighted blend of the bounding
    values. This is deterministic and documented.
    """
    if not sorted_values:
        raise ValueError("cannot compute a percentile of an empty series")

    if len(sorted_values) == 1:
        return sorted_values[0]

    position = (len(sorted_values) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = position - lower

    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def compute_statistics(values: Sequence[float]) -> BaselineStatistics:
    """Compute deterministic statistics for a sequence of values.

    Uses the sample standard deviation (``n - 1`` denominator). Percentiles
    use closest-rank linear interpolation. ``std`` is ``None`` when fewer than
    two values are provided.
    """
    ordered = sorted(values)

    return BaselineStatistics(
        count=len(values),
        mean=mean(values) if values else None,
        std=stdev(values) if len(values) >= 2 else None,
        min=ordered[0] if ordered else None,
        max=ordered[-1] if ordered else None,
        p25=_percentile(ordered, 0.25) if ordered else None,
        p50=_percentile(ordered, 0.50) if ordered else None,
        p75=_percentile(ordered, 0.75) if ordered else None,
    )


class BaselineBuilder:
    """Build deterministic historical baselines from normalized series."""

    def __init__(self, config: IntelligenceConfig | None = None) -> None:
        self._config = config or IntelligenceConfig()

    def build_baseline(
        self,
        series: MetricSeries,
    ) -> HistoricalBaseline | None:
        """Build a baseline for one series.

        Returns ``None`` when the series has fewer observations than
        ``min_observations_per_baseline`` so callers can represent that
        condition naturally instead of raising.
        """
        if series.count < self._config.min_observations_per_baseline:
            return None

        statistics = compute_statistics(
            [observation.value for observation in series.observations]
        )

        first = series.observations[0]
        last = series.observations[-1]

        return HistoricalBaseline(
            key=BaselineKey(
                service=series.service,
                environment=series.environment,
                metric_name=series.metric_name,
            ),
            start_time=first.timestamp,
            end_time=last.timestamp,
            statistics=statistics,
            source_record_ids=[
                observation.record_id for observation in series.observations
            ],
            explanation=(
                f"Baseline for {series.service}/{series.environment}/"
                f"{series.metric_name} built from {statistics.count} "
                f"observations spanning {first.timestamp} to "
                f"{last.timestamp}."
            ),
        )

    def build_baselines(
        self,
        series: Iterable[MetricSeries],
    ) -> dict[BaselineKey, HistoricalBaseline]:
        """Build baselines for every supplied series.

        Only series with enough observations are included.
        """
        baselines: dict[BaselineKey, HistoricalBaseline] = {}

        for one_series in series:
            baseline = self.build_baseline(one_series)
            if baseline is not None:
                baselines[baseline.key] = baseline

        return baselines