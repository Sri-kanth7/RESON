"""Incident detection for RESON incident intelligence.

The detector transforms correlated signals into incidents:

- transitively related evidence (connected through accepted correlations)
  becomes one incident,
- clearly separated evidence becomes separate incidents,
- an isolated anomaly with no correlations stays a standalone incident,
- multiple affected services can belong to one incident because correlation is
  not restricted to a single service.

Incident boundaries
-------------------
- start = earliest supporting signal timestamp
- end = latest supporting signal timestamp
- affected_services = sorted unique services of the supporting signals
- environment is preserved on the signals/evidence (the ``Incident`` contract
  has no environment field)

Clustering
----------
Connected components are formed over the accepted correlation edges. Adjacent
components whose bounding gap is at most ``incident_gap_seconds`` are merged so
that sparse-but-continuous disruptions are not fragmented; evidence separated
by more than that stays in separate incidents. Only components that contain at
least one *trigger* signal produce an incident (normal telemetry alone never
starts one).

Severity policy (deterministic, documented in ``SeverityPolicy``)
-----------------------------------------------------------------
Scaled from ``LOW`` upward by verifiable evidence, never by judgment.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Iterable, Mapping, Sequence
from uuid import UUID

from src.data.schemas import Evidence, EvidenceType, Incident, IncidentSeverity, IncidentStatus
from src.incidents.correlation import ESCALATION_EVENT_TYPES, FAILED_DEPLOYMENT_STATUSES
from src.incidents.evidence import link_incident_signals
from src.incidents.models import (
    IncidentIntelligenceConfig,
    Signal,
    content_id,
)


class SeverityPolicy:
    """Deterministic, fully explainable incident severity escalation.

    Every escalation below must be supported by actual evidence:

    - base ``LOW`` for any detected incident;
    - +1 when the incident involves a behavior shift magnitude >=
      ``severity_shift_threshold`` (the documented half-scale bound);
    - +1 when two or more services are affected;
    - +1 when sustained (change point) anomaly evidence is present;
    - +1 when critical evidence is present: an ERROR/CRITICAL log, an
      escalation event type, a failed/rolled-back deployment, or a deployment
      event accompanied by same-service anomalous metric evidence
      (deployment-regression evidence).

    The score is clamped to ``CRITICAL``. The explanation records every rule
    that fired. Severity is not an LLM judgment and never claims causality.
    """

    BASE = 1

    def classify(
        self,
        signals: Sequence[Signal],
        *,
        shift_magnitude: float = 0.0,
        multi_service_threshold: int = 2,
        severity_shift_threshold: float = 0.5,
    ) -> tuple[IncidentSeverity, str]:
        affected_services = sorted({signal.service for signal in signals})
        reasons: list[str] = []

        score = self.BASE
        reasons.append("base severity LOW for detected incident")

        if shift_magnitude >= severity_shift_threshold:
            score += 1
            reasons.append(
                f"behavior shift magnitude {shift_magnitude:.3f} >= "
                f"{severity_shift_threshold}"
            )

        if len(affected_services) >= multi_service_threshold:
            score += 1
            reasons.append(
                f"{len(affected_services)} affected services: "
                f"{', '.join(affected_services)}"
            )

        if any(signal.sustained for signal in signals):
            score += 1
            reasons.append("sustained change-point anomaly evidence")

        if self._has_critical_evidence(signals):
            score += 1
            reasons.append("critical evidence present")

        severity = self._severity_for(score)
        explanation = (
            f"Severity {severity.value} (score {score}) because: "
            + "; ".join(reasons)
            + "."
        )
        return severity, explanation

    @staticmethod
    def _has_critical_evidence(signals: Sequence[Signal]) -> bool:
        critical_log = any(
            signal.source_type == EvidenceType.LOG
            and signal.payload.get("level") in ("ERROR", "CRITICAL")
            for signal in signals
        )
        escalation_event = any(
            signal.source_type == EvidenceType.EVENT
            and signal.payload.get("event_type") in ESCALATION_EVENT_TYPES
            for signal in signals
        )
        failed_deployment = any(
            signal.source_type == EvidenceType.DEPLOYMENT
            and signal.payload.get("status") in (
                status.value for status in FAILED_DEPLOYMENT_STATUSES
            )
            for signal in signals
        )

        deployment_event_services = {
            signal.service
            for signal in signals
            if signal.source_type == EvidenceType.EVENT
            and signal.payload.get("event_type") == "deployment"
        }
        anomalous_metric_services = {
            signal.service
            for signal in signals
            if signal.source_type == EvidenceType.METRIC
            and signal.trigger
        }
        regression = bool(
            deployment_event_services & anomalous_metric_services
        )

        return bool(
            critical_log or escalation_event or failed_deployment or regression
        )

    @staticmethod
    def _severity_for(score: int) -> IncidentSeverity:
        clamped = min(max(score, 1), 4)
        return list(IncidentSeverity)[clamped - 1]


class IncidentDetector:
    """Build incidents from signals and their accepted correlations."""

    def __init__(
        self,
        config: IncidentIntelligenceConfig | None = None,
    ) -> None:
        self._config = config or IncidentIntelligenceConfig()
        self._severity_policy = SeverityPolicy()

    def detect(
        self,
        signals: Iterable[Signal],
        evidence: Iterable[Evidence],
        *,
        overall_shift: float = 0.0,
        service_shifts: Mapping[str, float] | None = None,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> tuple[list[Incident], dict[UUID, list[Evidence]], dict[UUID, str]]:
        """Return ``(incidents, incident_chain_evidence, severity_notes)``.

        ``incident_chain_evidence`` maps each incident id to its
        Incident -> Signal evidence chain (the traceability backbone).
        ``severity_notes`` maps each incident id to its explainable severity
        decision.
        """
        service_shift_map = dict(service_shifts or {})

        ordered = list(signals)
        if window_start is not None:
            ordered = [
                signal
                for signal in ordered
                if signal.timestamp >= window_start
            ]
        if window_end is not None:
            ordered = [
                signal
                for signal in ordered
                if signal.timestamp < window_end
            ]

        clusters = self._cluster(ordered, evidence)
        incidents: list[Incident] = []
        chain: dict[UUID, list[Evidence]] = {}
        severity_notes: dict[UUID, str] = {}

        for cluster in clusters:
            shift_magnitude = max(
                (
                    service_shift_map.get(signal.service, 0.0)
                    for signal in cluster
                ),
                default=overall_shift,
            )
            severity, severity_explanation = self._severity_policy.classify(
                cluster,
                shift_magnitude=shift_magnitude,
                severity_shift_threshold=(
                    self._config.severity_shift_threshold
                ),
            )

            start = cluster[0].timestamp
            end = cluster[-1].timestamp
            affected_services = sorted({signal.service for signal in cluster})
            scenario = self._uniform_scenario(cluster)

            incident = Incident(
                id=content_id(
                    "incident",
                    start,
                    end,
                    affected_services,
                    sorted(signal.source_id for signal in cluster),
                ),
                title=self._title(affected_services, start),
                start_time=start,
                end_time=end,
                severity=severity,
                status=IncidentStatus.OPEN.value,
                scenario=scenario,
                affected_services=affected_services,
            )
            chain[incident.id] = link_incident_signals(incident.id, cluster)
            severity_notes[incident.id] = severity_explanation
            incidents.append(incident)

        return incidents, chain, severity_notes

    def _cluster(
        self,
        signals: Sequence[Signal],
        evidence: Iterable[Evidence],
    ) -> list[list[Signal]]:
        """Connect signals through accepted correlations and merge runs."""
        by_id = {signal.source_id: signal for signal in signals}

        parent: dict[int, int] = {}
        size: dict[int, int] = {}

        def find(item: int) -> int:
            while parent[item] != item:
                parent[item] = parent[parent[item]]
                item = parent[item]
            return item

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra == rb:
                return
            if size[ra] < size[rb]:
                ra, rb = rb, ra
            parent[rb] = ra
            size[ra] += size[rb]

        for signal in signals:
            parent[signal.source_id.int] = signal.source_id.int
            size[signal.source_id.int] = 1

        for item in evidence:
            source = item.source_id.int
            target = item.target_id.int
            if source not in parent or target not in parent:
                continue
            if item.strength < self._config.minimum_correlation_strength:
                continue
            union(source, target)

        members: dict[int, list[Signal]] = defaultdict(list)
        for signal in signals:
            root = find(signal.source_id.int)
            members[root].append(signal)

        clusters = [
            sorted(members[root], key=lambda signal: (signal.timestamp, signal.source_id))
            for root in members
        ]
        clusters.sort(key=lambda cluster: (cluster[0].timestamp, cluster[0].source_id))

        merged = self._merge_adjacent(clusters)
        return [
            cluster
            for cluster in merged
            if any(signal.trigger for signal in cluster)
        ]

    def _merge_adjacent(
        self,
        clusters: Sequence[list[Signal]],
    ) -> list[list[Signal]]:
        """Merge neighboring clusters whose bounding gap is within tolerance."""
        merged: list[list[Signal]] = []
        for cluster in clusters:
            if not merged:
                merged.append(cluster)
                continue
            previous = merged[-1]
            gap = (
                cluster[0].timestamp - previous[-1].timestamp
            ).total_seconds()
            if gap <= self._config.incident_gap_seconds:
                combined = previous + cluster
                combined.sort(key=lambda signal: (signal.timestamp, signal.source_id))
                merged[-1] = combined
            else:
                merged.append(cluster)
        return merged

    @staticmethod
    def _uniform_scenario(signals: Sequence[Signal]) -> str | None:
        scenarios = {
            signal.payload.get("scenario")
            for signal in signals
            if signal.payload.get("scenario") is not None
        }
        if len(scenarios) == 1:
            return scenarios.pop()
        return None

    @staticmethod
    def _title(services: Sequence[str], start: datetime) -> str:
        joined = ",".join(services) if services else "unknown"
        return f"Incident affecting {joined} at {start.isoformat()}"