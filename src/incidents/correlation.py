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

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Sequence

from src.data.schemas import Deployment, DeploymentStatus, Event, EvidenceType, Log, RelationshipType
from src.incidents.evidence import dominant_provenance
from src.intelligence.change_detection import ChangePoint
from src.intelligence.models import BaselineKey, MetricDeviation
from src.incidents.models import (
    CorrelationCandidate,
    IncidentIntelligenceConfig,
    Signal,
    content_id,
    content_id_from_canonical,
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


#: Cache of the two-decimal rendering of factor values.
#:
#: A pair sweep renders the same handful of factor values over and over (a
#: bounded set of services, environments and time gaps), so each distinct
#: value is formatted once instead of once per pair.
_FACTOR_FORMAT_CACHE: dict[float, str] = {}

#: Upper bound for the factor-format cache. Beyond this the cache is reset,
#: so a pathological input cannot grow it without bound.
_FACTOR_FORMAT_CACHE_LIMIT = 100_000


def _format_factor(value: float) -> str:
    """Return the two-decimal rendering of one factor value."""
    formatted = _FACTOR_FORMAT_CACHE.get(value)
    if formatted is None:
        formatted = f"{value:.2f}"
        if len(_FACTOR_FORMAT_CACHE) >= _FACTOR_FORMAT_CACHE_LIMIT:
            _FACTOR_FORMAT_CACHE.clear()
        _FACTOR_FORMAT_CACHE[value] = formatted
    return formatted


@dataclass(frozen=True)
class _PreparedSignal:
    """The values of one signal that do not depend on the signal it is paired with.

    The correlation engine evaluates every distinct signal pair. Anything that
    depends on a single signal only — its service, its environment, its metric
    keys, its identifier, its timestamp — is computed once here and reused for
    every pair the signal takes part in. This changes no factor, no threshold
    and no identifier; it only removes repeated work.
    """

    signal: Signal
    """The signal this preparation describes."""
    uuid_str: str
    """Canonical string form of the signal id (used in ids and explanations)."""
    type_value: str
    """Plain value of the signal's evidence type."""
    service: str
    environment: str
    is_deployment: bool
    is_event: bool
    key_strings: frozenset[str]
    """Canonical metric-key strings referenced by the signal."""
    timestamp: datetime
    timestamp_iso: str
    """ISO-8601 rendering of the timestamp, used by the identifier."""


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
                    source=deviation.source,
                    host_id=deviation.host_id,
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
                    source=point.source,
                    host_id=point.host_id,
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
                    source=getattr(log, "source", None),
                    host_id=getattr(log, "host_id", None),
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
                    source=getattr(event, "source", None),
                    host_id=getattr(event, "host_id", None),
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
                    source=getattr(deployment, "source", None),
                    host_id=getattr(deployment, "host_id", None),
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

        The engine evaluates every pair, so the work is quadratic in the number
        of signals by construction — the *output* is quadratic too. What the
        implementation controls is the cost of one pair: every value that
        depends on a single signal is prepared once (see
        :class:`_PreparedSignal`) and reused, instead of being recomputed for
        every pair it appears in. The pairs evaluated, the factors computed,
        the decisions taken, and the identifiers produced are unchanged.
        """
        ordered = sorted(signals, key=lambda signal: (signal.timestamp, signal.source_id))
        prepared = [self._prepare(signal) for signal in ordered]

        candidates: list[CorrelationCandidate] = []
        #: (sort key, evidence) pairs. The sort key is built from the cached
        #: identifier strings, so the ordering is the documented one
        #: (``str(source_id)``, ``str(target_id)``) without re-rendering every
        #: identifier a second time.
        evidence_entries: list[tuple[str, str, object]] = []
        seen: set[object] = set()

        for index, source in enumerate(prepared):
            for target in prepared[index + 1:]:
                candidate = self._evaluate_prepared(source, target)
                if candidate.accepted:
                    item = self._materialize_prepared(candidate, source, target)
                    dedup_key = (item.source_id, item.target_id, item.relationship_type)
                    if dedup_key not in seen:
                        seen.add(dedup_key)
                        evidence_entries.append(
                            (source.uuid_str, target.uuid_str, item)
                        )
                candidates.append(candidate)

        evidence_entries.sort(key=lambda entry: (entry[0], entry[1]))
        evidence = [entry[2] for entry in evidence_entries]
        return candidates, evidence

    @staticmethod
    def _prepare(signal: Signal) -> _PreparedSignal:
        """Return the prepared view of one signal."""
        return _PreparedSignal(
            signal=signal,
            uuid_str=str(signal.source_id),
            type_value=signal.source_type.value,
            service=signal.service,
            environment=signal.environment,
            is_deployment=signal.source_type == EvidenceType.DEPLOYMENT,
            is_event=signal.source_type == EvidenceType.EVENT,
            key_strings=frozenset(
                _baseline_key_string(key) for key in signal.related_keys
            ),
            timestamp=signal.timestamp,
            timestamp_iso=signal.timestamp.isoformat(),
        )

    def _evaluate(
        self,
        source: Signal,
        target: Signal,
    ) -> CorrelationCandidate:
        """Evaluate one pair of signals (convenience wrapper)."""
        return self._evaluate_prepared(
            self._prepare(source), self._prepare(target)
        )

    def _evaluate_prepared(
        self,
        source: _PreparedSignal,
        target: _PreparedSignal,
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

        #: ``sum`` is used deliberately: CPython applies compensated
        #: (Neumaier) summation to floats, so a hand-rolled accumulation
        #: would produce a *different* last bit and change every strength
        #: value. The identifier and factor values must stay bit-identical.
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
            source=source.signal,
            target=target.signal,
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
    def _computable(
        name: str,
        source: _PreparedSignal,
        target: _PreparedSignal,
    ) -> bool:
        if name == "metric_overlap":
            return bool(source.key_strings) and bool(target.key_strings)
        if name == "deployment_proximity":
            return source.is_deployment or target.is_deployment
        if name == "event_link":
            return source.is_event or target.is_event
        return True

    @staticmethod
    def _metric_overlap(
        source: _PreparedSignal,
        target: _PreparedSignal,
    ) -> float:
        return 1.0 if source.key_strings & target.key_strings else 0.0

    def _deployment_proximity(
        self,
        source: _PreparedSignal,
        target: _PreparedSignal,
        gap: float,
    ) -> float:
        if not (source.is_deployment or target.is_deployment):
            return 0.0
        if gap > self._config.deployment_window_seconds:
            return 0.0
        return 1.0 if source.service == target.service else 0.5

    def _event_link(
        self,
        source: _PreparedSignal,
        target: _PreparedSignal,
        gap: float,
    ) -> float:
        if not (source.is_event or target.is_event):
            return 0.0
        if gap > self._config.correlation_window_seconds:
            return 0.0
        return 1.0 if source.service == target.service else 0.0

    @staticmethod
    def _relationship_type(
        source: _PreparedSignal,
        target: _PreparedSignal,
        factors: dict[str, float],
    ) -> RelationshipType:
        if (source.is_event and target.is_event):
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
        source: _PreparedSignal,
        target: _PreparedSignal,
        relationship: RelationshipType,
        strength: float,
        accepted: bool,
        factors: dict[str, float],
        *,
        gate_rejected: bool = False,
    ) -> str:
        applied = [
            f"{name}={_format_factor(value)}"
            for name, value in factors.items()
            if value > 0.0
        ]
        decision = "accepted" if accepted else "rejected"
        if gate_rejected:
            decision = "rejected (no time-bounded relationship, outside " \
                "correlation/deployment windows)"
        return (
            f"{source.type_value} {source.uuid_str} is {decision} as "
            f"{relationship.value} with {target.type_value} "
            f"{target.uuid_str} (strength={strength:.3f}, factors: "
            f"{', '.join(applied) if applied else 'none'})."
        )

    def _materialize(self, candidate: CorrelationCandidate):
        """Materialize an accepted candidate into the ``Evidence`` contract."""
        return self._materialize_prepared(
            candidate,
            self._prepare(candidate.source),
            self._prepare(candidate.target),
        )

    def _materialize_prepared(
        self,
        candidate: CorrelationCandidate,
        source: _PreparedSignal,
        target: _PreparedSignal,
    ):
        """Materialize an accepted candidate into the ``Evidence`` contract."""
        from src.data.schemas import Evidence

        explanation = (
            f"{candidate.explanation} "
            f"Service relationship: {candidate.service_relationship}; "
            f"environment relationship: {candidate.environment_relationship}."
        )
        #: The edge inherits the provenance of the signals it links. It is
        #: left unattributed when the two signals disagree on their source.
        edge_source, edge_host = dominant_provenance(
            [candidate.source, candidate.target]
        )
        return Evidence(
            id=self._evidence_id(candidate, source, target),
            source_id=candidate.source.source_id,
            source_type=candidate.source.source_type.value,
            target_id=candidate.target.source_id,
            target_type=candidate.target.source_type.value,
            relationship_type=candidate.relationship_type.value,
            timestamp=candidate.timestamp,
            strength=candidate.strength,
            explanation=explanation,
            source=edge_source,
            host_id=edge_host,
        )

    @staticmethod
    def _evidence_id(
        candidate: CorrelationCandidate,
        source: _PreparedSignal,
        target: _PreparedSignal,
    ):
        """Return the content-derived id of a materialized evidence link.

        This is the identifier :func:`content_id` produces for
        ``("evidence", relationship_type, source_id, target_id, timestamp)``,
        computed from the cached canonical strings so that making the pair
        sweep faster cannot change an identifier.
        """
        relationship = candidate.relationship_type
        reference_iso = (
            source.timestamp_iso
            if source.timestamp <= target.timestamp
            else target.timestamp_iso
        )
        payload = (
            "'evidence'|"
            f"{type(relationship).__name__}:{relationship.value}|"
            f"{source.uuid_str}|{target.uuid_str}|"
            f"{reference_iso}"
        )
        return content_id_from_canonical(payload)


def correlate_signals(
    signals: Iterable[Signal],
    config: IncidentIntelligenceConfig | None = None,
) -> tuple[list[CorrelationCandidate], list]:
    """Convenience wrapper for the correlation step."""
    return CorrelationEngine(config).correlate(signals)