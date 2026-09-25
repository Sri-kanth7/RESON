"""Deterministic reconstruction of an incident's supporting ``Signal`` objects.

Phase 3 persists an ``Incident`` and its evidence chain, but not the
``Signal`` objects that produced that chain. :class:`IncidentMatcher` derives
three of its six similarity factors (``metric_similarity``,
``event_sequence_similarity`` and ``deployment_relationship``) exclusively from
``IncidentHistory.signals``, so a historical incident reloaded without signals
scored a structural ``0.0`` on those three factors even when it genuinely
carried metric, event and deployment evidence. A zero that means "evidence was
never loaded" is indistinguishable from a zero that means "no match", which is
exactly the ambiguity Phase 4 must not visualize.

This module rebuilds those signals from the raw telemetry and baselines that
produced them, composing only components that already exist:

- :func:`src.preprocessing.normalizer.group_metrics` and ``slice_series`` for
  grouping and the half-open ``[start, end)`` window,
- the unmodified Phase 2 :class:`~src.intelligence.deviation.DeviationDetector`
  and :class:`~src.intelligence.change_detection.ChangeDetector`,
- the unmodified Phase 3
  :class:`~src.incidents.correlation.SignalBuilder` projection.

No Phase 2 or Phase 3 algorithm is duplicated or re-implemented here.

Traceability
------------
A rebuilt signal is admitted only when the incident's own evidence chain
references it: an ``Evidence`` row whose ``source_id`` is the incident id and
whose ``target_id`` is the signal's ``source_id``. The stored chain is
therefore the authority on *which* signals belong to the incident, so a signal
is never synthesized that the evidence does not already point at. Every
admitted signal keeps the real ``source_record_ids`` of the telemetry it came
from, keeping ``Incident -> Evidence -> source record`` resolvable end to end.

Historical / current separation
-------------------------------
The scan window is derived from the incident itself: its span
``[start_time, end_time]``, expressed as a half-open interval by advancing the
inclusive end by one microsecond (the same idiom ``BehaviorIntelligenceService``
uses to keep its final observation), with ``window_end`` optionally supplied on
the context for an incident that is still open. Admission is decided by chain
membership rather than by the window, so telemetry from a different analysis
window can never leak into an incident's signals:

- a metric deviation's ``source_id`` is its ``Metric`` record id, which is
  unique per stored record;
- a change point's ``source_id`` is ``content_id("changepoint", point)``, so an
  identically derived change point in another window is by construction the
  same change point, and admits an identical signal.

Wording follows the rest of Phase 3: reconstructed signals are evidence that
was *correlated with* an incident, never a cause of it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Mapping, Sequence
from uuid import UUID

from pydantic import Field

from src.data.schemas import (
    Deployment,
    Event,
    Evidence,
    Incident,
    Log,
    Metric,
)
from src.incidents.correlation import SignalBuilder
from src.incidents.models import IntelligenceModel, Signal
from src.intelligence.change_detection import ChangeDetector
from src.intelligence.deviation import DeviationDetector
from src.intelligence.models import (
    BaselineKey,
    ChangePoint,
    HistoricalBaseline,
    IntelligenceConfig,
    MetricDeviation,
)
from src.preprocessing.normalizer import group_metrics, slice_series


def derive_deviation_evidence(
    metrics: Iterable[Metric],
    baselines: Mapping[BaselineKey, HistoricalBaseline],
    *,
    config: IntelligenceConfig | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> tuple[list[MetricDeviation], list[ChangePoint]]:
    """Rebuild deviation and change-point streams from raw metric telemetry.

    This is the single Phase 2 evidence bridge. It is shared by
    :meth:`~src.incidents.service.IncidentIntelligenceService.derive_evidence`
    and :class:`HistoricalSignalReconstructor` so there is exactly one
    implementation of "raw metrics in, deviation and change-point evidence
    out" and therefore exactly one set of windowing rules.

    Metric series are grouped and windowed exactly like
    ``BehaviorIntelligenceService.analyze`` does, using the half-open
    ``[window_start, window_end)`` interval, and are evaluated with the
    unmodified Phase 2 detectors under the supplied ``config``. Results are
    consistent with a ``BehaviorAnalysis`` only when the same ``baselines``,
    the same ``IntelligenceConfig`` and the same window are supplied, which is
    a precondition of the caller's responsibility in both call sites.
    """
    intelligence_config = config or IntelligenceConfig()
    deviation_detector = DeviationDetector(intelligence_config)

    series = group_metrics(metrics)

    if window_start is not None or window_end is not None:
        series = [
            slice_series(one_series, window_start, window_end)
            for one_series in series
        ]

    deviations = [
        deviation
        for one_series in series
        for deviation in deviation_detector.detect_series(
            one_series,
            baselines,
        )
    ]

    change_points = ChangeDetector(intelligence_config).detect_deviations(
        deviations
    )

    return deviations, change_points


class HistoricalSignalContext(IntelligenceModel):
    """Telemetry and baselines required to reconstruct incident signals.

    The caller supplies the same inputs that produced the historical
    incidents: the raw telemetry covering them, and the historical baselines
    the Phase 2 detectors were run against. Nothing here is inferred, guessed,
    or generated by this module.
    """

    metrics: list[Metric] = Field(default_factory=list)
    logs: list[Log] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    deployments: list[Deployment] = Field(default_factory=list)
    baselines: dict[BaselineKey, HistoricalBaseline] = Field(default_factory=dict)
    intelligence_config: IntelligenceConfig | None = None
    window_end: datetime | None = None
    """Bound applied when the incident is still open and has no ``end_time``."""


class HistoricalSignalReconstructor:
    """Rebuild the supporting signals of one persisted incident.

    The reconstructor is stateless: every call derives its result purely from
    its arguments, so repeated execution with identical inputs produces
    identical signals, and input ordering has no effect.
    """

    def __init__(self) -> None:
        self._signal_builder = SignalBuilder()

    def reconstruct(
        self,
        incident: Incident,
        evidence: Iterable[Evidence],
        *,
        context: HistoricalSignalContext,
    ) -> list[Signal]:
        """Return the signals this incident's evidence chain already points at.

        An incident whose chain is empty has no supporting evidence to
        reconstruct, so an empty list is returned rather than a signal set
        inferred from the surrounding telemetry. Signals that the chain
        references but that cannot be reproduced from ``context`` are omitted
        as well; they are never approximated.
        """
        referenced = self._referenced_ids(incident, evidence)

        if not referenced:
            return []

        window_start, window_end = self._window(incident, context)

        deviations, change_points = derive_deviation_evidence(
            context.metrics,
            context.baselines,
            config=context.intelligence_config,
            window_start=window_start,
            window_end=window_end,
        )

        signals = self._signal_builder.build(
            deviations=deviations,
            change_points=change_points,
            logs=self._within(context.logs, window_start, window_end),
            events=self._within(context.events, window_start, window_end),
            deployments=self._within(
                context.deployments,
                window_start,
                window_end,
            ),
        )

        return sorted(
            (
                signal
                for signal in signals
                if signal.source_id in referenced
            ),
            key=lambda signal: (signal.timestamp, str(signal.source_id)),
        )

    @staticmethod
    def _referenced_ids(
        incident: Incident,
        evidence: Iterable[Evidence],
    ) -> set[UUID]:
        """Return the signal ids the incident's own chain references.

        Only rows whose ``source_id`` is the incident id are considered, so
        signal-to-signal correlation evidence is never mistaken for chain
        membership.
        """
        return {
            item.target_id
            for item in evidence
            if item.source_id == incident.id
        }

    @staticmethod
    def _window(
        incident: Incident,
        context: HistoricalSignalContext,
    ) -> tuple[datetime, datetime | None]:
        """Return the incident's span as a half-open scan window.

        An incident's extent is *inclusive* of ``end_time``: that value is the
        timestamp of its last supporting signal, so a plain half-open
        ``[start_time, end_time)`` bound would drop that very signal and leave
        the reconstruction silently incomplete. The end is therefore advanced
        by one microsecond, which is the same idiom
        ``BehaviorIntelligenceService`` uses to keep its final observation
        inside a half-open analysis window.

        A still-open incident has no ``end_time``, so the caller may supply
        ``context.window_end``; it is interpreted the same inclusive way. With
        neither available the scan stays open-ended, which is slower but still
        cannot admit a foreign signal, because admission is decided by chain
        membership rather than by the window.
        """
        last = incident.end_time or context.window_end

        if last is None:
            return incident.start_time, None

        return incident.start_time, last + timedelta(microseconds=1)

    @staticmethod
    def _within(
        records: Sequence[
            Metric | Log | Event | Deployment
        ],
        window_start: datetime,
        window_end: datetime | None,
    ) -> list:
        """Return records inside the half-open ``[window_start, window_end)``."""
        return [
            record
            for record in records
            if record.timestamp >= window_start
            and (window_end is None or record.timestamp < window_end)
        ]
