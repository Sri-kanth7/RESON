"""Phase 3 domain models for RESON incident intelligence.

Every model here is a *genuinely new* Phase 3 concept. Shared contracts
(``Incident``, ``Evidence``, ``RelationshipType``, ``EvidenceType``,
``IncidentSeverity``, ``IncidentStatus``) are NOT duplicated; they are
imported from :mod:`src.data.schemas` and reused unchanged.

Model inventory
---------------
- ``IncidentIntelligenceConfig`` — central, immutable thresholds used by the
  correlation engine and the incident detector. No magic numbers may live in
  implementation modules.
- ``Signal`` — the normalized, pre-relationship representation of one piece of
  evidence (a deviation, a change point, a log, an event, or a deployment)
  carrying metric context and full source traceability. This is a projection
  over the Phase 2 / telemetry contracts so the correlation engine can treat
  all evidence uniformly; it is not a duplicate of ``Evidence`` (which is a
  *relationship* between two entities).
- ``CorrelationCandidate`` — a deterministic relationship candidate between
  two signals, preserving both source and target evidence, the computed
  factors, the reference timestamp, the service/environment relationship, the
  strength, and an explanation. Accepted candidates are materialized into the
  existing ``Evidence`` contract.
- ``IncidentSimilarity`` — deterministic similarity factors between an
  incident and a historical incident.
- ``HistoricalIncidentMatch`` — a ranked match pairing the current incident
  with a historical incident while preserving the historical incident object,
  its timeline, and the similarity factors.
- ``IncidentHistory`` — a historical incident bundled with its timeline
  (evidence) and machine-readable signals; a candidate container for matching.
- ``IncidentResearch`` — the System Machine unit for one incident: the
  incident, its supporting signals, its timeline (evidence chain), and any
  historical matches.
- ``IncidentIntelligenceResult`` — the complete output of one analysis run.

Determinism
-----------
Sub-objects that carry no stable identity of their own are given
**content-derived UUIDs** via :func:`content_id` (a uuid5 over the canonical
content). This makes incident, evidence, and change-point ids reproducible
across runs for identical inputs instead of being random per execution.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid5

from pydantic import BaseModel, Field

from src.data.schemas import Evidence, EvidenceType, Incident, RelationshipType
from src.intelligence.models import BaselineKey, IntelligenceModel

# Fixed namespace used for all content-derived Phase 3 UUIDs. It is arbitrary
# but constant; uuid5(NAMESPACE, canonical-content) yields the same UUID for
# the same content on every run.
RESON_PHASE3_NAMESPACE = UUID("4b6d52a1-71e7-4b9c-9d2f-3f0a9d1e8c42")


def _canonical(value: Any) -> str:
    """Return a deterministic, order-stable string for arbitrary values."""
    if isinstance(value, BaseModel):
        pairs = [
            f"{key}={_canonical(getattr(value, key))}"
            for key in type(value).model_fields
        ]
        return "{" + ",".join(pairs) + "}"

    if isinstance(value, datetime):
        # isoformat is stable for equal datetimes including tz-aware ones.
        return value.isoformat()

    if isinstance(value, UUID):
        return str(value)

    if isinstance(value, Enum):
        return f"{type(value).__name__}:{value.value}"

    if isinstance(value, dict):
        return "{" + ",".join(
            sorted(f"{_canonical(k)}={_canonical(v)}" for k, v in value.items())
        ) + "}"

    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_canonical(item) for item in value]
        if isinstance(value, (set, frozenset)):
            items = sorted(items)
        return "[" + ",".join(items) + "]"

    if value is None or isinstance(value, (str, int, float, bool)):
        return repr(value)

    return repr(value)


def content_id(*parts: Any) -> UUID:
    """Return a deterministic UUID derived from canonical content parts.

    Identical content always produces the identical UUID; changed content
    produces a different UUID. Order matters for sequences (but callers are
    responsible for sorting when order is irrelevant to identity).
    """
    payload = "|".join(_canonical(part) for part in parts)
    return uuid5(RESON_PHASE3_NAMESPACE, payload)


class IncidentIntelligenceConfig(IntelligenceModel):
    """Immutable thresholds for Phase 3 incident intelligence.

    Threshold rationale (grounded in the RESON telemetry granularity):

    - ``correlation_window_seconds``: the simulator and the offline analysis
      pipeline emit a telemetry batch every 10 seconds. A 600 second window
      associates evidence that lands within a ten-minute disruption span while
      leaving clearly separated events (minutes apart) unconnected.
    - ``deployment_window_seconds``: deploy impact (metrics, errors, events)
      typically co-occurs within five minutes of the deployment itself.
    - ``minimum_correlation_strength``: pairwise strengths below this value are
      not materialized into ``Evidence`` and do not join signals into one
      incident (no time proximity and no shared context => no relationship).
    - ``incident_gap_seconds``: contiguous components whose bounding gap is at
      most two minutes (about 12 simulator batches) are merged into one
      incident so that sparse-but-continuous disruptions are not fragmented;
      evidence separated by more than this stays separate.
    - ``severity_shift_threshold``: a service behavior shift of 0.5 or more
      (half of the bounded [0, 1] scale) counts as high magnitude for the
      deterministic severity policy.
    """

    correlation_window_seconds: float = Field(default=600.0, gt=0.0)
    deployment_window_seconds: float = Field(default=300.0, gt=0.0)
    minimum_correlation_strength: float = Field(default=0.3, ge=0.0, le=1.0)
    incident_gap_seconds: float = Field(default=120.0, gt=0.0)
    severity_shift_threshold: float = Field(default=0.5, gt=0.0, le=1.0)


class Signal(IntelligenceModel):
    """One normalized piece of evidence before any relationship is drawn.

    ``source_id`` is the identity of the underlying evidence node:

    - metric deviation -> ``MetricDeviation.record_id`` (the Metric id)
    - change point -> a content-derived UUID of the change point (it has no id
      of its own) whose raw records live in ``source_record_ids``
    - log / event / deployment -> the schema record's ``id``

    ``source_type`` uses the existing ``EvidenceType`` vocabulary so the
    incident chain can link the incident to the signal without a parallel type
    system. ``trigger`` marks evidence that can by itself constitute an
    incident (anomalous metric evidence, critical logs/events, failed
    deployments). Non-trigger signals such as successful deployments enrich a
    cluster but never start one.
    """

    source_id: UUID
    source_type: EvidenceType
    timestamp: datetime
    service: str
    environment: str
    description: str = Field(min_length=1)
    strength: float = Field(ge=0.0, le=1.0)
    trigger: bool = False
    sustained: bool = False
    related_keys: list[BaselineKey] = Field(default_factory=list)
    source_record_ids: list[UUID] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)


class CorrelationCandidate(IntelligenceModel):
    """A deterministic relationship candidate between two signals."""

    source: Signal
    target: Signal
    relationship_type: RelationshipType
    timestamp: datetime
    service_relationship: str
    environment_relationship: str
    temporal_proximity: float = Field(ge=0.0, le=1.0)
    same_service: float = Field(ge=0.0, le=1.0)
    same_environment: float = Field(ge=0.0, le=1.0)
    metric_overlap: float = Field(ge=0.0, le=1.0)
    deployment_proximity: float = Field(ge=0.0, le=1.0)
    event_link: float = Field(ge=0.0, le=1.0)
    strength: float = Field(ge=0.0, le=1.0)
    explanation: str = Field(min_length=1)
    accepted: bool = False


class IncidentSimilarity(IntelligenceModel):
    """Deterministic similarity factors between two incidents.

    Every factor is in ``[0, 1]`` with a documented calculation. ``overall`` is
    the weighted combination defined by the similarity engine. Similarity is a
    comparison of available incident/evidence structure; it is NOT a
    probability and never a confidence percentage.
    """

    service_overlap: float = Field(ge=0.0, le=1.0)
    metric_similarity: float = Field(ge=0.0, le=1.0)
    event_sequence_similarity: float = Field(ge=0.0, le=1.0)
    deployment_relationship: float = Field(ge=0.0, le=1.0)
    temporal_similarity: float = Field(ge=0.0, le=1.0)
    scenario_match: float = Field(ge=0.0, le=1.0)
    overall_similarity: float = Field(ge=0.0, le=1.0)


class HistoricalIncidentMatch(IntelligenceModel):
    """A ranked match of the current incident against a historical incident.

    The historical incident object and its timeline are preserved intact so the
    System Machine can always present them, and the historical IDs are never
    rewritten.
    """

    incident_id: UUID
    historical_incident_id: UUID
    historical_incident: Incident
    historical_timeline: list[Evidence] = Field(default_factory=list)
    similarity: IncidentSimilarity
    supporting_evidence: list[Evidence] = Field(default_factory=list)
    explanation: str = Field(min_length=1)


class IncidentHistory(IntelligenceModel):
    """A historical incident bundled with its timeline and signals."""

    incident: Incident
    evidence: list[Evidence] = Field(default_factory=list)
    signals: list[Signal] = Field(default_factory=list)


class IncidentResearch(IntelligenceModel):
    """The System Machine unit for a single incident."""

    incident: Incident
    signals: list[Signal] = Field(default_factory=list)
    timeline: list[Evidence] = Field(default_factory=list)
    severity_explanation: str = ""
    historical_matches: list[HistoricalIncidentMatch] = Field(default_factory=list)


class IncidentIntelligenceResult(IntelligenceModel):
    """Complete output of one Phase 3 analysis run.

    ``correlation_evidence`` holds the accepted signal-to-signal relationships
    that were materialized into the ``Evidence`` contract.
    ``correlation_candidates`` holds *every* pair the engine evaluated, in the
    engine's deterministic order, including the pairs it rejected and the
    per-factor values behind each decision. The two lists are aligned by
    construction: a candidate is a superset of the accepted evidence, so a
    consumer can explain both "why are these two signals related" and "why are
    these two signals not related" from the same structured factors, without
    re-running the engine and without parsing explanation text.

    Exposing candidates changes no calculation, no threshold, no grouping and
    no identifier; it only preserves output the engine already produced.
    """

    window_start: datetime | None = None
    window_end: datetime | None = None
    overall_shift: float = 0.0
    changed_services: list[str] = Field(default_factory=list)
    researches: list[IncidentResearch] = Field(default_factory=list)
    correlation_evidence: list[Evidence] = Field(default_factory=list)
    correlation_candidates: list[CorrelationCandidate] = Field(default_factory=list)