"""Tests for the evidence chain and traceability module."""

from src.data.schemas import EvidenceType
from src.incidents.evidence import (
    link_incident_signals,
    order_timeline,
    referenced_record_ids,
    validate_evidence,
)
from src.incidents.models import Signal
from tests.incident_test_helpers import KEY, make_deviation
from tests.incident_test_helpers import ts, fixed_uuid


def signal_for(seconds, seed):
    deviation = make_deviation(seconds, seed=seed)
    return Signal(
        source_id=deviation.record_id,
        source_type=EvidenceType.METRIC,
        timestamp=deviation.timestamp,
        service=KEY.service,
        environment=KEY.environment,
        description="metric evidence",
        strength=1.0,
        trigger=True,
        related_keys=[KEY],
        source_record_ids=[deviation.record_id],
    )


def test_incident_evidence_chain_reaches_raw_record():
    incident = fixed_uuid("inc")
    deviation = make_deviation(10, seed="chain-1")
    signal = signal_for(10, "chain-1")
    chain = link_incident_signals(incident, [signal])
    item = chain[0]

    assert item.source_id == incident
    assert item.target_id == deviation.record_id
    # Traceability: Incident -> Evidence -> phase 2 signal -> raw record id.
    assert item.target_type == EvidenceType.METRIC.value
    assert signal.source_record_ids == [deviation.record_id]


def test_change_point_chain_preserves_multiple_source_records():
    from src.intelligence.models import BaselineKey, ChangePoint

    key = BaselineKey(
        service="api-service",
        environment="development",
        metric_name="latency_ms",
    )
    points = ChangePoint(
        start_time=ts(10),
        end_time=ts(30),
        consecutive_batches=2,
        affected_metric_keys=[key],
        source_record_ids=[fixed_uuid("r1"), fixed_uuid("r2")],
        explanation="sustained anomaly",
    )
    signal = Signal(
        source_id=fixed_uuid("cp-node"),
        source_type=EvidenceType.METRIC,
        timestamp=ts(10),
        service="api-service",
        environment="development",
        description="change point",
        strength=1.0,
        trigger=True,
        sustained=True,
        source_record_ids=[fixed_uuid("r1"), fixed_uuid("r2")],
    )
    chain = link_incident_signals(fixed_uuid("inc"), [signal])
    assert len(chain) == 1
    assert referenced_record_ids([signal]) == sorted(
        [fixed_uuid("r1"), fixed_uuid("r2")],
        key=str,
    )


def test_every_generated_evidence_is_valid():
    incident = fixed_uuid("inc-valid")
    signals = [signal_for(10, "v-1"), signal_for(20, "v-2")]
    chain = link_incident_signals(incident, signals)
    assert all(validate_evidence(item) for item in chain)
    assert all(item.source_id and item.target_id for item in chain)


def test_timeline_ordering_never_invented():
    incident = fixed_uuid("inc-order")
    chain = link_incident_signals(incident, [signal_for(30, "o-3"), signal_for(10, "o-1")])
    ordered = order_timeline(chain)
    assert [item.timestamp for item in ordered] == [
        ts(10),
        ts(30),
    ]