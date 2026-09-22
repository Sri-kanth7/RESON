"""Deterministic incident timeline construction for RESON incident
intelligence.

The incident timeline is the incident's evidence chain ordered
chronologically. Ordering is:

1. ``timestamp``
2. stable source id (the incident id)
3. target id (the referenced signal)

Ties on timestamp are resolved by the stable IDs so the ordering is fully
deterministic and never depends on list insertion order. No duplicate
timeline entries can occur for an incident because each supporting signal
produces exactly one chain entry.

Every timeline entry remains traceable to its source: ``target_id`` is the
signal's source id, ``target_type`` its ``EvidenceType``, and the raw
telemetry record ids are preserved on the backing signals.
"""

from __future__ import annotations

from typing import Iterable
from uuid import UUID

from src.data.schemas import Evidence
from src.incidents.evidence import link_incident_signals, order_timeline
from src.incidents.models import Signal


class TimelineBuilder:
    """Build deterministic, de-duplicated incident timelines."""

    def build(
        self,
        incident_id: UUID,
        signals: Iterable[Signal],
    ) -> list[Evidence]:
        """Return the chronologically ordered incident evidence chain."""
        return order_timeline(link_incident_signals(incident_id, signals))

    def from_chain(
        self,
        chain: Iterable[Evidence],
    ) -> list[Evidence]:
        """Order an already-built evidence chain deterministically."""
        return order_timeline(chain)