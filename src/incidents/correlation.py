"""Deterministic evidence correlation for RESON incident intelligence.

The correlation engine works in two steps:

1. ``SignalBuilder`` projects Phase 2 evidence (metric deviations, change
   points) and telemetry (logs, events, deployments) into the normalized
   :class:`~src.incidents.models.Signal` form.
2. ``CorrelationEngine`` considers every distinct signal pair once, computes a
   bounded strength from a small fixed set of documented factors, and
   materializes accepted pairs into the existing ``Evidence`` contract.

Factor policy (weights documented; total = 1.0)
-----------------------------------------------
- ``temporal_proximity`` (0.30): ``1 - |dt| / correlation_window_seconds``,
  clamped to ``[0, 1]``. Always computable.
- ``same_service`` (0.25): 1.0 when both signals share a service, else 0.0.
- ``same_environment`` (0.15): 1.0 when both signals share an environment.
- ``metric_overlap`` (0.15): 1.0 when both signals reference at least one
  common ``BaselineKey`` (a deviation and a change point for the same metric
  belong together). Computable only when both signals carry related metric
  keys.
- ``deployment_proximity`` (0.10): 1.0 when one signal is a deployment and the
  other falls within ``deployment_window_seconds`` of it. Computable only when
  at least one signal is a deployment.
- ``event_link`` (0.05): 1.0 when one signal is an event and both share a
  service within the correlation window. Computable only when at least one
  signal is an event.

Relationship strength = weighted mean of the computable factors (weights
renormalized over the computable subset). A candidate is accepted when its
strength is >= ``minimum_correlation_strength`` AND it carries at least one
*time-bounded* factor: ``temporal_proximity > 0``, ``deployment_proximity > 0``,
or ``event_link > 0``. This gate prevents purely static context (same service,
environment, or metric key) from associating evidence that is not actually
temporally close: evidence separated by more than the correlation window is
unrelated regardless of how much context it shares. Associations are always
time-anchored; context alone never suffices.

Relationship type decision (honest, no inferred causality):
- both signals are events -> ``EVENT_SEQUENCE`` (ordered co-occurrence)
- shared service, environment, metric key, or same-service deployment ->
  ``CORRELATION`` ("correlated with")
- otherwise -> ``TEMPORAL`` ("temporally associated with")

Wording throughout is deliberately cautious: the engine establishes
associations, never root cause.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from src.data.schemas import Deployment, DeploymentStatus, Event, EvidenceType, Log, RelationshipType
from src.intelligence.change_detection import ChangePoint
from src.intelligence.models import BaselineKey, MetricDeviation
from src.incidents.models import (
    CorrelationCandidate,
    IncidentIntelligenceConfig,
    Signal,
    content_id,
)

CRITICAL_LOG_LEVELS = ("ERROR", "CRITICAL")

# Event types that are severe enough to trigger an incident by themselves.
ESCALATION_EVENT_TYPES = (
    "service_failure",
    "database_pressure",
    "network_degradation",
)

# Deployment states that indicate a failed or rolled-back release.
FAILED_DEPLOYMENT_STATUSES = (
    DeploymentStatus.FAILED,
    DeploymentStatus.ROLLED_BACK,
)

# Deterministic factor weights, documented in the module docstring.
FACTOR_WEIGHTS: dict[str, float] = {
    "temporal_proximity": 0.30,
    "same_service": 0.25,
    "same_environment": 0.15,
    "metric_overlap": 0.15,
    "deployment_proximity": 0.10,
    "event_link": 0.05,
}


def _env_value(environment) -> str:
    """Return a plain-string environment for whatever enum/str is supplied."""
    value = getattr(environment, "value", environment)
    return str(value)


def _baseline_key_string(key: BaselineKey) -> str:
    return f"{key.service}/{key.environment}/{key.metric_name}"


class SignalBuilder:
    """Project Phase 2 evidence and telemetry into normalized signals.

    Only evidence that can support an incident is projected:

    - anomalous metric deviations (trigger), all change points (trigger),
    - ERROR/CRITICAL logs (trigger),
    - escalation events (trigger) and other events (context),
    - failed/rolled-back deployments (trigger) and successful/rolled-back
      deployments (recovery/context).

    Normal-status deviations are intentionally excluded from the signal set:
    they form the recovery evidence used by the lifecycle, not the incident
    corpus itself.
    """

    def build(
        self,
        *,
        deviations: Iterable[MetricDeviation] = (),
        change_points: Iterable[ChangePoint] = (),
        logs: Iterable[Log] = (),
        events: Iterable[Event] = (),
        deployments: Iterable[Deployment] = (),
    ) -> list[Signal]:
        signals: list[Signal] = []

        for deviation in deviations:
            key = deviation.key
            signals.append(
                Signal(
                    source_id=deviation.record_id,
                    source_type=EvidenceType.METRIC,
                    timestamp=deviation.timestamp,
                    service=key.service,
                    environment=key.environment,
                    description=self._deviation_description(deviation),
                    strength=1.0 if deviation.is_anomalous else 0.0,
                    trigger=deviation.is_anomalous,
                    related_keys=[key],
                    source_record_ids=[deviation.record_id],
                    payload={
                        "metric_name": key.metric_name,
                        "value": deviation.value,
                        "status": deviation.status.value,
                    },
                )
            )

        for point in change_points:
            # A change point has no identity of its own; derive one from its
            # content so the node is reproducible and unique.
            node_id = content_id("changepoint", point)
            service = point.affected_metric_keys[0].service if point.affected_metric_keys else "unknown"
            environment = (
                point.affected_metric_keys[0].environment
                if point.affected_metric_keys
                else "unknown"
            )
            signals.append(
                Signal(
                    source_id=node_id,
                    source_type=EvidenceType.METRIC,
                    timestamp=point.start_time,
                    service=service,
                    environment=environment,
                    description=(
                        f"Sustained change point over {point.consecutive_batches} "
                        f"batches from {point.start_time} to {point.end_time}"
                    ),
                    strength=1.0,
                    trigger=True,
                    sustained=True,
                    related_keys=list(point.affected_metric_keys),
                    source_record_ids=list(point.source_record_ids),
                    payload={
                        "consecutive_batches": point.consecutive_batches,
                        "end_time": point.end_time.isoformat(),
                    },
                )
            )

        for log in logs:
            level = str(log.level.value if hasattr(log.level, "value") else log.level)
            if level not in CRITICAL_LOG_LEVELS:
                continue
            signals.append(
                Signal(
                    source_id=log.id,
                    source_type=EvidenceType.LOG,
                    timestamp=log.timestamp,
                    service=log.service,
                    environment=_env_value(log.environment),
                    description=f"Log level={level}: {log.message}",
                    strength=0.8,
                    trigger=True,
                    source_record_ids=[log.id],
                    payload={
                        "level": level,
                        "message": log.message,
                        "scenario": log.metadata.get("scenario"),
                    },
                )
            )

        for event in events:
            event_type = str(
                event.event_type.value
                if hasattr(event.event_type, "value")
                else event.event_type
            )
            signals.append(
                Signal(
                    source_id=event.id,
                    source_type=EvidenceType.EVENT,
                    timestamp=event.timestamp,
                    service=event.service,
                    environment=_env_value(event.environment),
                    description=f"Event {event_type}: {event.description}",
                    strength=1.0 if event_type in ESCALATION_EVENT_TYPES else 0.5,
                    trigger=event_type in ESCALATION_EVENT_TYPES,
                    source_record_ids=[event.id],
                    payload={
                        "event_type": event_type,
                        "description": event.description,
                        "scenario": event.metadata.get("scenario"),
                    },
                )
            )

        for deployment in deployments:
            status = deployment.status
            failed = status in FAILED_DEPLOYMENT_STATUSES
            signals.append(
                Signal(
                    source_id=deployment.id,
                    source_type=EvidenceType.DEPLOYMENT,
                    timestamp=deployment.timestamp,
                    service=deployment.service,
                    environment=_env_value(deployment.environment),
                    description=(
                        f"Deployment {deployment.version} status={status.value}"
                        if hasattr(status, "value")
                        else f"Deployment {deployment.version} status={status}"
                    ),
                    strength=1.0 if failed else 0.5,
                    trigger=failed,
                    source_record_ids=[deployment.id],
                    payload={
                        "version": deployment.version,
                        "status": (
                            status.value if hasattr(status, "value") else status
                        ),
                        "scenario": deployment.metadata.get("scenario"),
                    },
                )
            )

        return self._dedupe(signals)

    def _deviation_description(self, deviation: MetricDeviation) -> str:
        key = deviation.key
        parts = [
            f"Metric deviation for {_baseline_key_string(key)}",
            f"value={deviation.value}",
            f"status={deviation.status.value}",
        ]
        if deviation.z_score is not None:
            parts.append(f"z={deviation.z_score:.3f}")
        if deviation.relative_deviation is not None:
            parts.append(f"rel={deviation.relative_deviation:.3f}")
        return " ".join(parts)

    @staticmethod
    def _dedupe(signals: Sequence[Signal]) -> list[Signal]:
        """Drop duplicate signals by source id, keeping a stable order."""
        unique: dict[object, Signal] = {}
        for signal in signals:
            unique.setdefault(signal.source_id, signal)
        return [unique[key] for key in sorted(unique, key=lambda item: str(item))]


class CorrelationEngine:
    """Deterministically relate signal pairs into ``Evidence``."""

    def __init__(
        self,
        config: IncidentIntelligenceConfig | None = None,
    ) -> None:
        self._config = config or IncidentIntelligenceConfig()

    def correlate(
        self,
        signals: Iterable[Signal],
    ) -> tuple[list[CorrelationCandidate], list]:
        """Evaluate every distinct signal pair once.

        Returns ``(candidates, accepted_evidence)``. ``candidates`` preserves
        the per-factor breakdown for explainability; ``accepted_evidence`` is
        the deduplicated, deterministically ordered ``Evidence`` list.
        """
        ordered = sorted(signals, key=lambda signal: (signal.timestamp, signal.source_id))

        candidates: list[CorrelationCandidate] = []
        evidence: list = []
        seen: set[object] = set()

        for index, source in enumerate(ordered):
            for target in ordered[index + 1:]:
                candidate = self._evaluate(source, target)
                if candidate.accepted:
                    item = self._materialize(candidate)
                    dedup_key = (item.source_id, item.target_id, item.relationship_type)
                    if dedup_key not in seen:
                        seen.add(dedup_key)
                        evidence.append(item)
                candidates.append(candidate)

        evidence.sort(key=lambda item: (str(item.source_id), str(item.target_id)))
        return candidates, evidence

    def _evaluate(
        self,
        source: Signal,
        target: Signal,
    ) -> CorrelationCandidate:
        gap = abs((target.timestamp - source.timestamp).total_seconds())
        temporal = max(
            0.0,
            1.0 - (gap / self._config.correlation_window_seconds),
        )
        same_service = 1.0 if source.service == target.service else 0.0
        same_environment = 1.0 if source.environment == target.environment else 0.0
        metric_overlap = self._metric_overlap(source, target)
        deployment_proximity = self._deployment_proximity(source, target, gap)
        event_link = self._event_link(source, target, gap)

        factors = {
            "temporal_proximity": temporal,
            "same_service": same_service,
            "same_environment": same_environment,
            "metric_overlap": metric_overlap,
            "deployment_proximity": deployment_proximity,
            "event_link": event_link,
        }
        computable = {
            name: value
            for name, value in factors.items()
            if self._computable(name, source, target)
        }

        numerator = sum(
            FACTOR_WEIGHTS[name] * value
            for name, value in computable.items()
        )
        denominator = sum(FACTOR_WEIGHTS[name] for name in computable)
        strength = numerator / denominator if denominator else 0.0

        relationship = self._relationship_type(source, target, factors)

        reference_ts = min(source.timestamp, target.timestamp)

        # Acceptance requires both a minimum strength AND a time-bounded
        # relationship (temporal proximity, deployment proximity, or event
        # link within their windows). Static context shared across a large time
        # gap (same service/environment/metric key) alone never associates
        # evidence that is not temporally close.
        bounded = (
            temporal > 0.0
            or deployment_proximity > 0.0
            or event_link > 0.0
        )
        accepted = (
            strength >= self._config.minimum_correlation_strength
            and bounded
        )

        explanation = self._explanation(
            source,
            target,
            relationship,
            strength,
            accepted,
            factors,
            gate_rejected=(
                strength >= self._config.minimum_correlation_strength
                and not bounded
            ),
        )

        return CorrelationCandidate(
            source=source,
            target=target,
            relationship_type=relationship,
            timestamp=reference_ts,
            service_relationship="same" if same_service else "cross",
            environment_relationship="same" if same_environment else "cross",
            temporal_proximity=temporal,
            same_service=same_service,
            same_environment=same_environment,
            metric_overlap=metric_overlap,
            deployment_proximity=deployment_proximity,
            event_link=event_link,
            strength=strength,
            explanation=explanation,
            accepted=accepted,
        )

    @staticmethod
    def _computable(name: str, source: Signal, target: Signal) -> bool:
        if name == "metric_overlap":
            return bool(source.related_keys) and bool(target.related_keys)
        if name == "deployment_proximity":
            return (
                source.source_type == EvidenceType.DEPLOYMENT
                or target.source_type == EvidenceType.DEPLOYMENT
            )
        if name == "event_link":
            return (
                source.source_type == EvidenceType.EVENT
                or target.source_type == EvidenceType.EVENT
            )
        return True

    @staticmethod
    def _metric_overlap(source: Signal, target: Signal) -> float:
        source_keys = set(
            _baseline_key_string(key) for key in source.related_keys
        )
        target_keys = set(
            _baseline_key_string(key) for key in target.related_keys
        )
        return 1.0 if source_keys & target_keys else 0.0

    def _deployment_proximity(
        self,
        source: Signal,
        target: Signal,
        gap: float,
    ) -> float:
        if not (
            source.source_type == EvidenceType.DEPLOYMENT
            or target.source_type == EvidenceType.DEPLOYMENT
        ):
            return 0.0
        if gap > self._config.deployment_window_seconds:
            return 0.0
        return 1.0 if source.service == target.service else 0.5

    def _event_link(self, source: Signal, target: Signal, gap: float) -> float:
        if not (
            source.source_type == EvidenceType.EVENT
            or target.source_type == EvidenceType.EVENT
        ):
            return 0.0
        if gap > self._config.correlation_window_seconds:
            return 0.0
        return 1.0 if source.service == target.service else 0.0

    @staticmethod
    def _relationship_type(
        source: Signal,
        target: Signal,
        factors: dict[str, float],
    ) -> RelationshipType:
        if (
            source.source_type == EvidenceType.EVENT
            and target.source_type == EvidenceType.EVENT
        ):
            return RelationshipType.EVENT_SEQUENCE

        shared_context = any(
            [
                factors["same_service"] == 1.0,
                factors["same_environment"] == 1.0,
                factors["metric_overlap"] == 1.0,
                factors["deployment_proximity"] > 0.0,
            ]
        )
        if shared_context:
            return RelationshipType.CORRELATION
        return RelationshipType.TEMPORAL

    @staticmethod
    def _explanation(
        source: Signal,
        target: Signal,
        relationship: RelationshipType,
        strength: float,
        accepted: bool,
        factors: dict[str, float],
        *,
        gate_rejected: bool = False,
    ) -> str:
        applied = [
            f"{name}={value:.2f}"
            for name, value in factors.items()
            if value > 0.0
        ]
        decision = "accepted" if accepted else "rejected"
        if gate_rejected:
            decision = "rejected (no time-bounded relationship, outside " \
                "correlation/deployment windows)"
        return (
            f"{source.source_type.value} {source.source_id} is {decision} as "
            f"{relationship.value} with {target.source_type.value} "
            f"{target.source_id} (strength={strength:.3f}, factors: "
            f"{', '.join(applied) if applied else 'none'})."
        )

    def _materialize(self, candidate: CorrelationCandidate):
        """Materialize an accepted candidate into the ``Evidence`` contract."""
        from src.data.schemas import Evidence

        explanation = (
            f"{candidate.explanation} "
            f"Service relationship: {candidate.service_relationship}; "
            f"environment relationship: {candidate.environment_relationship}."
        )
        return Evidence(
            id=content_id(
                "evidence",
                candidate.relationship_type,
                candidate.source.source_id,
                candidate.target.source_id,
                candidate.timestamp,
            ),
            source_id=candidate.source.source_id,
            source_type=candidate.source.source_type.value,
            target_id=candidate.target.source_id,
            target_type=candidate.target.source_type.value,
            relationship_type=candidate.relationship_type.value,
            timestamp=candidate.timestamp,
            strength=candidate.strength,
            explanation=explanation,
        )


def correlate_signals(
    signals: Iterable[Signal],
    config: IncidentIntelligenceConfig | None = None,
) -> tuple[list[CorrelationCandidate], list]:
    """Convenience wrapper for the correlation step."""
    return CorrelationEngine(config).correlate(signals)