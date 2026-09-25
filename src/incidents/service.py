"""Incident intelligence orchestration for RESON (Phase 3).

``IncidentIntelligenceService`` composes the independent Phase 3 modules
(correlation, detection, lifecycle, timeline, similarity) into one thin
orchestration surface. It depends on the ``Storage`` abstraction when
persistence or historical retrieval is needed, never on Supabase directly.

Phase 3 consumes the Phase 2 contracts: ``BehaviorAnalysis`` for the overall
shift / changed services / per-service shift magnitudes, plus the
``MetricDeviation`` and ``ChangePoint`` lists. ``BehaviorAnalysis`` hides the
deviation/change-point streams internally, so the service exposes
:meth:`derive_evidence` to reconstruct them with the *existing* Phase 2
detectors (no Phase 2 code is touched) when only metrics + baselines are at
hand.

Two outputs exist purely so that Phase 4 can present the pipeline truthfully:

- ``analyze`` preserves every :class:`CorrelationCandidate` the engine
  evaluated (accepted and rejected alike) on the result, so the per-factor
  reasoning behind a relationship is available as structured data.
- :meth:`historical_candidates` can rebuild the ``Signal`` objects behind a
  persisted historical incident's evidence chain, so the similarity factors
  that depend on signals are not structurally forced to ``0.0`` by a signal
  set that was simply never loaded.

No causality is inferred anywhere; wording is limited to "correlated with",
"temporally associated with", and "supporting evidence".
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Mapping

from src.data.schemas import Deployment, Event, Log, Metric
from src.data.storage import Storage
from src.intelligence.baseline import HistoricalBaseline
from src.intelligence.models import (
    BaselineKey,
    BehaviorAnalysis,
    IntelligenceConfig,
    MetricDeviation,
)
from src.incidents.correlation import CorrelationEngine, SignalBuilder
from src.incidents.detector import IncidentDetector
from src.incidents.lifecycle import IncidentLifecycle
from src.incidents.models import (
    IncidentHistory,
    IncidentIntelligenceConfig,
    IncidentIntelligenceResult,
    IncidentResearch,
    Signal,
)
from src.incidents.reconstruction import (
    HistoricalSignalContext,
    HistoricalSignalReconstructor,
    derive_deviation_evidence,
)
from src.incidents.similarity import IncidentMatcher
from src.incidents.timeline import TimelineBuilder


class IncidentIntelligenceService:
    """Compose the Phase 3 incident intelligence pipeline."""

    def __init__(
        self,
        config: IncidentIntelligenceConfig | None = None,
        *,
        signal_builder: SignalBuilder | None = None,
        correlator: CorrelationEngine | None = None,
        detector: IncidentDetector | None = None,
        lifecycle: IncidentLifecycle | None = None,
        timeline: TimelineBuilder | None = None,
        matcher: IncidentMatcher | None = None,
        storage: Storage | None = None,
    ) -> None:
        self._config = config or IncidentIntelligenceConfig()
        self._signal_builder = signal_builder or SignalBuilder()
        self._correlator = correlator or CorrelationEngine(self._config)
        self._detector = detector or IncidentDetector(self._config)
        self._lifecycle = lifecycle or IncidentLifecycle()
        self._timeline = timeline or TimelineBuilder()
        self._matcher = matcher or IncidentMatcher()
        self._reconstructor = HistoricalSignalReconstructor()
        self._storage = storage

    # ------------------------------------------------------------------
    # Phase 2 evidence bridge
    # ------------------------------------------------------------------

    def derive_evidence(
        self,
        metrics: Iterable[Metric],
        baselines: Mapping[BaselineKey, HistoricalBaseline],
        *,
        intelligence_config: IntelligenceConfig | None = None,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> tuple[list[MetricDeviation], list]:
        """Recompute deviations and change points using the Phase 2 detectors.

        ``BehaviorAnalysis`` does not expose its internal deviation/change-point
        streams, so this helper reconstructs them with the unmodified Phase 2
        ``DeviationDetector`` and ``ChangeDetector``. Callers that already hold
        the deviations may pass them straight to :meth:`analyze`.

        For results to be consistent with a ``BehaviorAnalysis``, the same
        preconditions must hold as when it was produced:

        - the same ``baselines`` mapping,
        - the same ``IntelligenceConfig`` thresholds (``intelligence_config``),
        - the same analysis window. Metric series are windowed with
          :func:`~src.preprocessing.normalizer.slice_series` exactly like
          ``BehaviorIntelligenceService.analyze`` (half-open
          ``[window_start, window_end)``) before detection.

        The work is delegated to the single shared bridge
        :func:`~src.incidents.reconstruction.derive_deviation_evidence`, which
        :class:`~src.incidents.reconstruction.HistoricalSignalReconstructor`
        also uses, so both paths are guaranteed to window and detect
        identically.
        """
        return derive_deviation_evidence(
            metrics,
            baselines,
            config=intelligence_config,
            window_start=window_start,
            window_end=window_end,
        )

    # ------------------------------------------------------------------
    # Main analysis
    # ------------------------------------------------------------------

    def analyze(
        self,
        *,
        analysis: BehaviorAnalysis | None = None,
        deviations: Iterable[MetricDeviation] = (),
        change_points: Iterable = (),
        logs: Iterable[Log] = (),
        events: Iterable[Event] = (),
        deployments: Iterable[Deployment] = (),
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        investigate_services: Iterable[str] = (),
    ) -> IncidentIntelligenceResult:
        """Run the Phase 3 pipeline for a single analysis window."""
        signals = self._signal_builder.build(
            deviations=deviations,
            change_points=change_points,
            logs=logs,
            events=events,
            deployments=deployments,
        )

        candidates, correlation_evidence = self._correlator.correlate(signals)

        overall_shift = analysis.overall_score if analysis is not None else 0.0
        changed_services = (
            list(analysis.changed_services)
            if analysis is not None
            else []
        )
        service_shifts = self._service_shifts(analysis)

        incidents, chain, severity_notes = self._detector.detect(
            signals,
            correlation_evidence,
            overall_shift=overall_shift,
            service_shifts=service_shifts,
            window_start=window_start,
            window_end=window_end,
        )

        investigation = set(investigate_services)

        researches: list[IncidentResearch] = []
        for incident in incidents:
            incident_signals = [
                signal
                for signal in signals
                if signal.source_id in {
                    item.target_id for item in chain[incident.id]
                }
            ]
            finalized = self._lifecycle.finalize(
                incident,
                signals=incident_signals,
                deviations=deviations,
                deployments=deployments,
                investigate=bool(investigation & set(incident.affected_services)),
            )
            researches.append(
                IncidentResearch(
                    incident=finalized,
                    signals=incident_signals,
                    timeline=self._timeline.from_chain(chain[incident.id]),
                    severity_explanation=severity_notes[incident.id],
                )
            )

        researches.sort(
            key=lambda research: (
                research.incident.start_time,
                research.incident.id,
            )
        )

        return IncidentIntelligenceResult(
            window_start=window_start,
            window_end=window_end,
            overall_shift=overall_shift,
            changed_services=changed_services,
            researches=researches,
            correlation_evidence=correlation_evidence,
            correlation_candidates=candidates,
        )

    # ------------------------------------------------------------------
    # Historical matching
    # ------------------------------------------------------------------

    def find_historical_matches(
        self,
        research: IncidentResearch,
        candidates: Iterable[IncidentHistory],
        *,
        top_k: int | None = None,
    ) -> list:
        """Rank historical incidents against one current incident."""
        return self._matcher.find_matches(
            current=research.incident,
            current_signals=research.signals,
            current_evidence=research.timeline,
            candidates=candidates,
            top_k=top_k,
        )

    def with_history(
        self,
        research: IncidentResearch,
        candidates: Iterable[IncidentHistory],
        *,
        top_k: int | None = None,
    ) -> IncidentResearch:
        """Return the research enriched with ranked historical matches."""
        matches = self.find_historical_matches(
            research,
            candidates,
            top_k=top_k,
        )
        return research.model_copy(
            update={"historical_matches": matches}
        )

    # ------------------------------------------------------------------
    # Persistence (Storage abstraction only)
    # ------------------------------------------------------------------

    def persist(self, result: IncidentIntelligenceResult) -> None:
        """Persist incidents and evidence through the Storage abstraction.

        Incident -> Signal chain evidence and accepted correlation evidence are
        both persisted. No Phase 2 internal models are persisted and no
        Supabase call happens here.
        """
        if self._storage is None:
            return

        saved_evidence_ids: set[object] = set()

        for research in result.researches:
            self._storage.save_incident(research.incident)
            for item in research.timeline:
                if item.id in saved_evidence_ids:
                    continue
                self._storage.save_evidence(item)
                saved_evidence_ids.add(item.id)

        for item in result.correlation_evidence:
            if item.id in saved_evidence_ids:
                continue
            self._storage.save_evidence(item)
            saved_evidence_ids.add(item.id)

    def historical_candidates(
        self,
        *,
        severity: str | None = None,
        status: str | None = None,
        scenario: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        signal_context: HistoricalSignalContext | None = None,
    ) -> list[IncidentHistory]:
        """Load historical incidents plus their timelines from storage.

        ``signal_context`` supplies the raw telemetry and historical baselines
        needed to rebuild each incident's supporting signals from its stored
        evidence chain. Supplying it is what keeps ``metric_similarity``,
        ``event_sequence_similarity`` and ``deployment_relationship`` from
        scoring a structural ``0.0`` merely because signals were never
        reloaded; see :mod:`src.incidents.reconstruction`.

        Without it, ``IncidentHistory.signals`` stays empty and the documented
        evidence penalty applies unchanged. Either way no signal is invented:
        signals are admitted only when the stored chain already references them.
        """
        if self._storage is None:
            return []

        candidates: list[IncidentHistory] = []
        for incident in self._storage.get_incidents(
            severity=severity,
            status=status,
            scenario=scenario,
            start_time=start_time,
            end_time=end_time,
        ):
            evidence = self._storage.get_evidence(
                source_id=str(incident.id),
            )
            signals: list[Signal] = []
            if signal_context is not None:
                signals = self._reconstructor.reconstruct(
                    incident,
                    evidence,
                    context=signal_context,
                )
            candidates.append(
                IncidentHistory(
                    incident=incident,
                    evidence=evidence,
                    signals=signals,
                )
            )
        return candidates

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _service_shifts(
        analysis: BehaviorAnalysis | None,
    ) -> dict[str, float]:
        if analysis is None:
            return {}
        return {
            result.service: result.shift.score
            for result in analysis.service_results
        }