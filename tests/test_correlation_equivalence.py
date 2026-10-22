"""Behaviour-pinning tests for the correlation engine.

The correlation engine evaluates every distinct signal pair, so the pair sweep
is the hottest path in the incident pipeline. The implementation prepares
per-signal values once instead of recomputing them for every pair; these
tests exist so that this optimisation can never change *what the engine
decides*.

The recorded digest below was produced by the engine before the optimisation
and is re-verified after it. Any change to a factor, a threshold, a decision,
an explanation or an identifier changes the digest and fails the test.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from uuid import UUID

from src.data.schemas import EvidenceType
from src.incidents.correlation import CorrelationEngine, correlate_signals
from src.incidents.models import (
    IncidentIntelligenceConfig,
    Signal,
    content_id,
)
from src.intelligence.models import BaselineKey

#: Recorded digest of the engine's decisions for the fixed signal set below.
#:
#: It covers every candidate (both endpoints, the relationship type, the
#: reference timestamp, the service/environment relationship, all six
#: factors, the strength, the acceptance decision and the explanation) and
#: every materialized evidence link (its identifier, endpoints, relationship
#: type, timestamp, strength, explanation and provenance).
GOLDEN_DIGEST = (
    "a2c6a8c97af528f4a7dab1a0d1aba37dc5318d464cb58f13c5d2f0765808042b"
)

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _signals() -> list[Signal]:
    """Return a fixed signal set covering every factor and decision path.

    The set deliberately spans more than the correlation window (so pairs are
    rejected by the time-bounded gate), mixes services and environments (so
    cross-service pairs exist), mixes signal types (so the event, deployment
    and metric-overlap factors are exercised) and mixes provenance (so the
    edge-provenance rule is exercised).
    """
    signals: list[Signal] = []
    index = 0
    for minutes in (0, 1, 5, 11, 12, 30, 61, 200, 700):
        for kind in ("metric", "log", "event", "deployment"):
            index += 1
            related: list[BaselineKey] = []
            if kind in ("metric", "deployment"):
                related = [
                    BaselineKey(
                        service="api",
                        environment="production",
                        metric_name="latency_ms",
                    )
                ]
            signals.append(
                Signal(
                    source_id=UUID(int=index),
                    source_type=EvidenceType(kind),
                    timestamp=BASE + timedelta(minutes=minutes),
                    service=["api", "db", "web"][index % 3],
                    environment=["production", "staging"][index % 2],
                    description=f"signal {index}",
                    strength=1.0,
                    trigger=kind in ("metric", "event"),
                    related_keys=related,
                    source_record_ids=[UUID(int=index)],
                    source="simulation" if index % 4 else None,
                    host_id="host-1" if index % 5 else None,
                )
            )
    return signals


def _digest(candidates, evidence) -> str:
    """Return the digest of every decision the engine made."""
    lines: list[str] = []
    for candidate in candidates:
        lines.append(
            "C|{0}|{1}|{2}|{3}|{4}|{5}|{6!r}|{7!r}|{8!r}|{9!r}|{10!r}|"
            "{11!r}|{12!r}|{13!r}|{14}".format(
                candidate.source.source_id,
                candidate.target.source_id,
                candidate.relationship_type.value,
                candidate.timestamp.isoformat(),
                candidate.service_relationship,
                candidate.environment_relationship,
                candidate.temporal_proximity,
                candidate.same_service,
                candidate.same_environment,
                candidate.metric_overlap,
                candidate.deployment_proximity,
                candidate.event_link,
                candidate.strength,
                candidate.accepted,
                candidate.explanation,
            )
        )
    for item in evidence:
        lines.append(
            "E|{0}|{1}|{2}|{3}|{4}|{5!r}|{6}|{7}|{8}".format(
                item.id,
                item.source_id,
                item.target_id,
                item.relationship_type,
                item.timestamp.isoformat(),
                item.strength,
                item.explanation,
                item.source,
                item.host_id,
            )
        )
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def test_every_decision_matches_the_recorded_digest():
    """The engine's decisions are unchanged by the pair-sweep optimisation."""
    candidates, evidence = correlate_signals(_signals())
    assert len(candidates) == 630
    assert len(evidence) == 137
    assert _digest(candidates, evidence) == GOLDEN_DIGEST


def test_decisions_match_the_recorded_digest_under_a_custom_configuration():
    """Different thresholds produce different decisions, and are pinned too."""
    config = IncidentIntelligenceConfig(
        correlation_window_seconds=120.0,
        deployment_window_seconds=60.0,
        minimum_correlation_strength=0.6,
    )
    candidates, evidence = correlate_signals(_signals(), config)
    assert _digest(candidates, evidence) == _CUSTOM_CONFIG_DIGEST


def test_the_signal_level_evaluation_matches_the_prepared_sweep():
    """Evaluating a pair directly gives the same candidate as the sweep."""
    engine = CorrelationEngine()
    signals = _signals()
    candidates, _ = engine.correlate(signals)
    by_pair = {
        (candidate.source.source_id, candidate.target.source_id): candidate
        for candidate in candidates
    }
    for source in signals[:6]:
        for target in signals[:6]:
            if source.source_id >= target.source_id:
                continue
            direct = engine._evaluate(source, target)
            swept = by_pair[(source.source_id, target.source_id)]
            assert direct == swept


def test_evidence_identifiers_are_the_content_derived_identifiers():
    """Every materialized link keeps the documented content-derived id."""
    candidates, evidence = correlate_signals(_signals())
    accepted = [item for item in candidates if item.accepted]
    expected = {
        content_id(
            "evidence",
            candidate.relationship_type,
            candidate.source.source_id,
            candidate.target.source_id,
            candidate.timestamp,
        )
        for candidate in accepted
    }
    assert len(accepted) == len(evidence)
    assert {item.id for item in evidence} == expected


#: Digest of the same signal set under a narrower window and a higher
#: strength threshold. Recorded from the engine before the optimisation.
_CUSTOM_CONFIG_DIGEST = (
    "69f5606cd45260fbe9e4fff6033e56efa14a9f7913059b80f6b92776b29b6e8a"
)
