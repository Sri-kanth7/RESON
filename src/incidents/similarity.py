"""Deterministic historical incident similarity for RESON incident
intelligence.

Similarity factors (each in ``[0, 1]``, all documented, all computed from
available data):

- ``service_overlap`` (weight 0.30): Jaccard index of the two incidents'
  ``affected_services`` sets.
- ``metric_similarity`` (weight 0.25): Jaccard index of the ``(service,
  metric_name)`` pairs among metric signals. Absent on either side -> 0.0 (no
  measurable metric evidence).
- ``event_sequence_similarity`` (weight 0.15): longest-common-subsequence
  ratio of the chronological ``event_type`` sequences of event signals.
  Absent on either side -> 0.0.
- ``deployment_relationship`` (weight 0.10): Jaccard index of the
  ``(service, status, version)`` triples among deployment signals. Absent on
  either side -> 0.0.
- ``temporal_similarity`` (weight 0.10): duration ratio
  ``1 - |d1 - d2| / max(d1, d2)`` over the incidents' measured durations.
  Open incidents (no end time) have no temporal structure -> neutral 0.5,
  documented below.
- ``scenario_match`` (weight 0.10): 1.0 when both incidents carry the same
  scenario label, else 0.0.

``overall_similarity`` is the weighted combination of the factors. It is a
deterministic comparison over incident structure and evidence; it is NOT a
probability and never a confidence percentage.

Absent evidence is scored as an *explicit evidence penalty* rather than
renormalized away: a factor for which neither incident carries evidence
(deployment signals, events, metric signals, scenario label, resolution) is
scored 0.0 with its full weight. This keeps "no evidence of X" distinct from
"matching evidence of X". Consequently 1.0 is reachable only when *every*
factor is available and identical; two deployment- and scenario-free incidents
cap at 0.80. The penalty affects the absolute magnitude, not the deterministic
ranking, because every candidate is evaluated against the same factor set.

Matching
--------
Matches exclude the current incident by id, preserve the historical incident
object and its IDs, keep every factor, and order deterministically by
descending ``overall_similarity`` with stable tie-breaking on the historical
incident id. This ordering is purely an internal algorithmic ranking for
retrieving comparable history.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from src.data.schemas import Evidence
from src.incidents.evidence import order_timeline
from src.incidents.models import (
    EvidenceType,
    HistoricalIncidentMatch,
    Incident,
    IncidentHistory,
    IncidentSimilarity,
    Signal,
)

FACTOR_WEIGHTS: dict[str, float] = {
    "service_overlap": 0.30,
    "metric_similarity": 0.25,
    "event_sequence_similarity": 0.15,
    "deployment_relationship": 0.10,
    "temporal_similarity": 0.10,
    "scenario_match": 0.10,
}

_SUPPORTING_TARGET_TYPES = {
    EvidenceType.METRIC,
    EvidenceType.EVENT,
    EvidenceType.DEPLOYMENT,
}


def _jaccard(left: set, right: set) -> float:
    if not left and not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _lcs_ratio(left: Sequence[str], right: Sequence[str]) -> float:
    if not left or not right:
        return 0.0
    previous = [0] * (len(right) + 1)
    for item_left in left:
        current = [0] * (len(right) + 1)
        for index_right, item_right in enumerate(right, start=1):
            if item_left == item_right:
                current[index_right] = previous[index_right - 1] + 1
            else:
                current[index_right] = max(
                    previous[index_right],
                    current[index_right - 1],
                )
        previous = current
    lcs = previous[-1]
    return lcs / max(len(left), len(right))


class IncidentMatcher:
    """Compute similarity factors and rank historical incident matches."""

    def similarity(
        self,
        *,
        current: Incident,
        historical: Incident,
        current_signals: Iterable[Signal] = (),
        historical_signals: Iterable[Signal] = (),
    ) -> IncidentSimilarity:
        current_signal_list = list(current_signals)
        historical_signal_list = list(historical_signals)

        service_overlap = _jaccard(
            set(current.affected_services),
            set(historical.affected_services),
        )

        metric_similarity = _jaccard(
            self._metric_pairs(current_signal_list),
            self._metric_pairs(historical_signal_list),
        )

        event_sequence_similarity = _lcs_ratio(
            self._event_types(current_signal_list),
            self._event_types(historical_signal_list),
        )

        deployment_relationship = _jaccard(
            self._deployment_triples(current_signal_list),
            self._deployment_triples(historical_signal_list),
        )

        temporal_similarity = self._temporal_similarity(current, historical)

        scenario_match = self._scenario_match(current, historical)

        factors = {
            "service_overlap": service_overlap,
            "metric_similarity": metric_similarity,
            "event_sequence_similarity": event_sequence_similarity,
            "deployment_relationship": deployment_relationship,
            "temporal_similarity": temporal_similarity,
            "scenario_match": scenario_match,
        }
        overall = sum(
            FACTOR_WEIGHTS[name] * value
            for name, value in factors.items()
        )

        return IncidentSimilarity(
            **factors,
            overall_similarity=overall,
        )

    def find_matches(
        self,
        *,
        current: Incident,
        current_signals: Iterable[Signal] = (),
        current_evidence: Iterable[Evidence] = (),
        candidates: Iterable[IncidentHistory] = (),
        exclude_current: bool = True,
        top_k: int | None = None,
    ) -> list[HistoricalIncidentMatch]:
        """Rank historical candidates against a current incident.

        The current incident is excluded by id when ``exclude_current`` is
        set. Matches are ordered by descending overall similarity, with a
        stable tie-break on ``(historical_incident_id, incident_id)``.
        """
        current_signal_list = list(current_signals)
        current_evidence_list = order_timeline(current_evidence)

        supporting = [
            item
            for item in current_evidence_list
            if item.target_type in (
                target.value for target in _SUPPORTING_TARGET_TYPES
            )
        ]

        matches: list[HistoricalIncidentMatch] = []

        for history in candidates:
            if exclude_current and history.incident.id == current.id:
                continue

            similarity = self.similarity(
                current=current,
                historical=history.incident,
                current_signals=current_signal_list,
                historical_signals=history.signals,
            )

            matches.append(
                HistoricalIncidentMatch(
                    incident_id=current.id,
                    historical_incident_id=history.incident.id,
                    historical_incident=history.incident,
                    historical_timeline=order_timeline(history.evidence),
                    similarity=similarity,
                    supporting_evidence=supporting,
                    explanation=self._explanation(similarity, history.incident),
                )
            )

        matches.sort(
            key=lambda match: (
                -match.similarity.overall_similarity,
                str(match.historical_incident_id),
                str(match.incident_id),
            )
        )

        if top_k is not None and top_k > 0:
            matches = matches[:top_k]
        return matches

    @staticmethod
    def _metric_pairs(signals: Sequence[Signal]) -> set[tuple[str, str]]:
        return {
            (signal.service, signal.payload.get("metric_name"))
            for signal in signals
            if signal.source_type == EvidenceType.METRIC
            and signal.payload.get("metric_name")
        }

    @staticmethod
    def _event_types(signals: Sequence[Signal]) -> list[str]:
        events = [
            signal
            for signal in signals
            if signal.source_type == EvidenceType.EVENT
        ]
        events.sort(key=lambda signal: (signal.timestamp, signal.source_id))
        return [
            signal.payload.get("event_type")
            for signal in events
            if signal.payload.get("event_type")
        ]

    @staticmethod
    def _deployment_triples(signals: Sequence[Signal]) -> set[tuple[str, str, str]]:
        return {
            (
                signal.service,
                signal.payload.get("status"),
                signal.payload.get("version"),
            )
            for signal in signals
            if signal.source_type == EvidenceType.DEPLOYMENT
            and signal.payload.get("status")
        }

    @staticmethod
    def _temporal_similarity(
        current: Incident,
        historical: Incident,
    ) -> float:
        current_duration = _measured_duration(current)
        historical_duration = _measured_duration(historical)

        if current_duration is None or historical_duration is None:
            # Open incidents expose no temporal structure; a neutral 0.5 keeps
            # the factor from over-penalizing an incident whose end is unknown.
            return 0.5

        divisor = max(current_duration, historical_duration)
        if divisor <= 0.0:
            return 1.0
        difference = abs(current_duration - historical_duration)
        return max(0.0, 1.0 - (difference / divisor))

    @staticmethod
    def _scenario_match(current: Incident, historical: Incident) -> float:
        if current.scenario is None or historical.scenario is None:
            return 0.0
        return 1.0 if current.scenario == historical.scenario else 0.0

    @staticmethod
    def _explanation(similarity: IncidentSimilarity, historical: Incident) -> str:
        if similarity.overall_similarity == 0.0:
            return (
                f"No shared structure with historical incident "
                f"{historical.id}; overall similarity 0.0."
            )

        factors = {
            "service_overlap": similarity.service_overlap,
            "metric_similarity": similarity.metric_similarity,
            "event_sequence_similarity": (
                similarity.event_sequence_similarity
            ),
            "deployment_relationship": similarity.deployment_relationship,
            "temporal_similarity": similarity.temporal_similarity,
            "scenario_match": similarity.scenario_match,
        }
        top = max(factors, key=lambda name: (factors[name], name))
        contributing = [
            f"{name}={value:.3f}"
            for name, value in factors.items()
            if value > 0.0
        ]
        return (
            f"Historical incident {historical.id} is rated at "
            f"overall similarity {similarity.overall_similarity:.3f}; "
            f"top factor: {top}; contributing factors: "
            f"{', '.join(contributing) if contributing else 'none'}."
        )


def _measured_duration(incident: Incident) -> float | None:
    if incident.end_time is None:
        return None
    return (incident.end_time - incident.start_time).total_seconds()