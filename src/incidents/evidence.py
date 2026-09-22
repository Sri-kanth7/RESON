"""Evidence chain construction and traceability for RESON incident
intelligence.

The traceability backbone is:

    Incident
      -> Evidence (source=incident, target=signal)
      -> Signal source id / source record ids
      -> raw record id

Every generated ``Evidence`` object carries real source and target IDs. The
chain links an incident to each of its supporting signals; each signal in turn
preserves its raw ``source_record_ids`` (the telemetry records that produced
the signal), so the full Incident -> Evidence -> record path is always
resolvable.

The timeline for an incident is exactly this chain, ordered chronologically
by :func:`order_timeline` with a stable ID tie-break.
"""

from __future__ import annotations

from typing import Iterable, Sequence
from uuid import UUID

from src.data.schemas import Evidence, EvidenceType
from src.incidents.models import Signal, content_id


def link_incident_signals(
    incident_id: UUID,
    signals: Iterable[Signal],
) -> list[Evidence]:
    """Build the Incident -> Signal evidence chain for an incident.

    One Evidence entry per supporting signal; duplicates are impossible
    because detection already deduped signals by source id. Each entry
    references a real source (the incident id) and a real target (the signal
    source id). Relationship type is ``CORRELATION`` with cautious wording
    ("supporting evidence correlated with the incident"), never causality.
    """
    ordered = sorted(
        signals,
        key=lambda signal: (signal.timestamp, signal.source_id),
    )

    chain: list[Evidence] = []

    for signal in ordered:
        chain.append(
            Evidence(
                id=content_id(
                    "chain",
                    incident_id,
                    signal.source_id,
                ),
                source_id=incident_id,
                source_type=EvidenceType.INCIDENT.value,
                target_id=signal.source_id,
                target_type=signal.source_type.value,
                relationship_type="correlation",
                timestamp=signal.timestamp,
                strength=signal.strength,
                explanation=(
                    f"Supporting evidence for incident {incident_id}: "
                    f"{signal.description} "
                    f"(source record ids: "
                    f"{', '.join(str(item) for item in signal.source_record_ids)})."
                ),
            )
        )

    return chain


def order_timeline(evidence: Iterable[Evidence]) -> list[Evidence]:
    """Order evidence chronologically, then by stable source id.

    ``Evidence.timestamp`` then ``source_id`` then ``target_id`` gives a
    fully deterministic ordering with a stable tie-break for same-timestamp
    entries and never relies on insertion order.
    """
    return sorted(
        evidence,
        key=lambda item: (
            item.timestamp,
            str(item.source_id),
            str(item.target_id),
        ),
    )


def timeline_signals_by_target(
    timeline: Sequence[Evidence],
) -> dict[UUID, Evidence]:
    """Map chain evidence by its referenced signal id.

    Useful for traceability lookups: given a timeline, look up the evidence
    that points at a particular source record. Entries are de-duplicated by
    target id deterministically (sorted input).
    """
    mapping: dict[UUID, Evidence] = {}
    for item in order_timeline(timeline):
        mapping.setdefault(item.target_id, item)
    return mapping


def referenced_record_ids(signals: Sequence[Signal]) -> list[UUID]:
    """Flatten and de-duplicate the raw record ids behind a set of signals."""
    collected: set[UUID] = set()
    for signal in signals:
        collected.update(signal.source_record_ids)
    return sorted(collected, key=str)


def validate_evidence(item: Evidence) -> bool:
    """Return whether an Evidence object carries real source/target references."""
    return bool(item.source_id) and bool(item.target_id) and bool(item.strength >= 0.0)