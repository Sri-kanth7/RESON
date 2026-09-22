"""Tests for the deterministic incident lifecycle."""

import pytest

from src.data.schemas import (
    DeploymentStatus,
    EvidenceType,
    Incident,
    IncidentSeverity,
    IncidentStatus,
)
from src.incidents.lifecycle import IncidentLifecycle
from src.incidents.models import Signal
from src.intelligence.models import DeviationStatus
from tests.incident_test_helpers import (
    KEY,
    make_deployment,
    make_deviation,
    make_event,
)
from tests.incident_test_helpers import ts, fixed_uuid


def open_incident(seconds=20, seed="inc-1"):
    return Incident(
        id=fixed_uuid(seed),
        title="Incident",
        start_time=ts(10),
        end_time=ts(seconds),
        severity=IncidentSeverity.MEDIUM.value,
        status=IncidentStatus.OPEN.value,
        affected_services=["api-service"],
    )


def metric_signal(seconds, seed):
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


def test_newly_detected_incident_stays_open_without_evidence():
    lifecycle = IncidentLifecycle()
    final = lifecycle.finalize(
        open_incident(),
        signals=[],
    )
    assert final.status == IncidentStatus.OPEN.value
    assert final.end_time is None


def test_explicit_investigation_marks_investigating():
    lifecycle = IncidentLifecycle()
    final = lifecycle.finalize(
        open_incident(),
        signals=[],
        investigate=True,
    )
    assert final.status == IncidentStatus.INVESTIGATING.value
    assert final.end_time is None


def test_successful_deployment_after_incident_resolves_it():
    lifecycle = IncidentLifecycle()
    final = lifecycle.finalize(
        open_incident(seconds=20),
        signals=[],
        deployments=[make_deployment(40, service="api-service", seed="dep-r")],
    )
    assert final.status == IncidentStatus.RESOLVED.value
    assert final.end_time == ts(20)


def test_rolled_back_deployment_at_incident_end_resolves_it():
    lifecycle = IncidentLifecycle()
    final = lifecycle.finalize(
        open_incident(seconds=20),
        signals=[],
        deployments=[
            make_deployment(20, service="api-service", status=DeploymentStatus.ROLLED_BACK, seed="dep-rollback")
        ],
    )
    assert final.status == IncidentStatus.RESOLVED.value


def test_successful_deployment_inside_incident_does_not_resolve():
    # A SUCCESS deployment that coincides with the last evidence is context,
    # not recovery: strictly-after is required.
    lifecycle = IncidentLifecycle()
    final = lifecycle.finalize(
        open_incident(seconds=20),
        signals=[],
        deployments=[
            make_deployment(20, service="api-service", seed="dep-inside")
        ],
    )
    assert final.status == IncidentStatus.OPEN.value


def test_normal_deviation_after_last_anomalous_resolves_it():
    lifecycle = IncidentLifecycle()
    deviations = [
        make_deviation(10, status=DeviationStatus.ANOMALOUS, seed="an-1"),
        make_deviation(20, status=DeviationStatus.ANOMALOUS, seed="an-2"),
        make_deviation(40, status=DeviationStatus.NORMAL, seed="an-3"),
    ]
    final = lifecycle.finalize(
        open_incident(seconds=20),
        signals=[metric_signal(20, "an-2")],
        deviations=deviations,
    )
    assert final.status == IncidentStatus.RESOLVED.value
    assert final.end_time == ts(20)


def test_no_recovery_evidence_preserves_open_state():
    lifecycle = IncidentLifecycle()
    final = lifecycle.finalize(
        open_incident(seconds=20),
        signals=[],
        deviations=[
            make_deviation(10, status=DeviationStatus.ANOMALOUS, seed="no-r1"),
            make_deviation(20, status=DeviationStatus.ANOMALOUS, seed="no-r2"),
        ],
        deployments=[make_deployment(15, seed="no-r3")],
    )
    assert final.status == IncidentStatus.OPEN.value


def test_allowed_forward_transition_chain():
    lifecycle = IncidentLifecycle()
    incident = open_incident()
    investigating = lifecycle.investigate(incident)
    assert investigating.status == IncidentStatus.INVESTIGATING.value
    resolved = lifecycle.resolve(investigating)
    assert resolved.status == IncidentStatus.RESOLVED.value
    direct = lifecycle.resolve(open_incident())
    assert direct.status == IncidentStatus.RESOLVED.value


def test_invalid_transition_raises_and_preserves_state():
    lifecycle = IncidentLifecycle()
    incident = lifecycle.investigate(open_incident())
    with pytest.raises(ValueError):
        lifecycle.transition(incident, IncidentStatus.OPEN)
    # last state preserved
    assert incident.status == IncidentStatus.INVESTIGATING.value


def test_identical_transition_is_idempotent():
    lifecycle = IncidentLifecycle()
    incident = open_incident()
    assert lifecycle.resolve(incident).status == IncidentStatus.RESOLVED.value