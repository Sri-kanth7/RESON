from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from src.data.schemas import (
    Deployment,
    DeploymentStatus,
    Environment,
    Event,
    EventType,
    Evidence,
    EvidenceType,
    Incident,
    IncidentSeverity,
    IncidentStatus,
    Log,
    LogLevel,
    Metric,
    RelationshipType,
    Service,
)


def test_service_creation():
    service = Service(
        name="payment-service",
        environment=Environment.PRODUCTION,
        version="v1.0.0",
    )

    assert service.name == "payment-service"
    assert service.environment == Environment.PRODUCTION
    assert service.version == "v1.0.0"


def test_metric_creation():
    metric = Metric(
        timestamp=datetime.now(),
        service="payment-service",
        environment=Environment.PRODUCTION,
        metric_name="latency",
        value=428.5,
        unit="ms",
    )

    assert metric.metric_name == "latency"
    assert metric.value == 428.5
    assert metric.unit == "ms"


def test_log_creation():
    log = Log(
        timestamp=datetime.now(),
        service="payment-service",
        environment=Environment.PRODUCTION,
        level=LogLevel.ERROR,
        message="Database connection timeout",
    )

    assert log.level == LogLevel.ERROR
    assert "Database" in log.message


def test_event_creation():
    event = Event(
        timestamp=datetime.now(),
        service="database",
        environment=Environment.PRODUCTION,
        event_type=EventType.DATABASE_PRESSURE,
        description="Database CPU exceeded normal operating range",
    )

    assert event.event_type == EventType.DATABASE_PRESSURE


def test_deployment_creation():
    deployment = Deployment(
        timestamp=datetime.now(),
        service="payment-service",
        environment=Environment.PRODUCTION,
        version="v2.4.1",
        status=DeploymentStatus.SUCCESS,
    )

    assert deployment.version == "v2.4.1"
    assert deployment.status == DeploymentStatus.SUCCESS


def test_incident_duration():
    start = datetime(2026, 9, 18, 14, 0, 0)
    end = start + timedelta(minutes=18)

    incident = Incident(
        title="Payment service degradation",
        start_time=start,
        end_time=end,
        severity=IncidentSeverity.HIGH,
        status=IncidentStatus.RESOLVED,
        scenario="database_overload",
        affected_services=[
            "payment-service",
            "database",
        ],
    )

    assert incident.duration_seconds == 18 * 60


def test_open_incident_has_no_duration():
    incident = Incident(
        title="Payment service degradation",
        start_time=datetime.now(),
        severity=IncidentSeverity.HIGH,
        status=IncidentStatus.OPEN,
    )

    assert incident.duration_seconds is None


def test_evidence_creation():
    source = Metric(
        timestamp=datetime.now(),
        service="database",
        environment=Environment.PRODUCTION,
        metric_name="latency",
        value=892,
        unit="ms",
    )

    target = Metric(
        timestamp=datetime.now(),
        service="payment-service",
        environment=Environment.PRODUCTION,
        metric_name="latency",
        value=428,
        unit="ms",
    )

    evidence = Evidence(
        source_id=source.id,
        source_type=EvidenceType.METRIC,
        target_id=target.id,
        target_type=EvidenceType.METRIC,
        relationship_type=RelationshipType.TEMPORAL,
        timestamp=datetime.now(),
        strength=0.82,
        explanation="Database latency increased before payment latency.",
    )

    assert evidence.strength == 0.82
    assert evidence.relationship_type == RelationshipType.TEMPORAL


def test_metric_rejects_missing_metric_name():
    with pytest.raises(ValidationError):
        Metric(
            timestamp=datetime.now(),
            service="payment-service",
            environment=Environment.PRODUCTION,
            metric_name="",
            value=100,
            unit="ms",
        )


def test_evidence_rejects_invalid_strength():
    with pytest.raises(ValidationError):
        Evidence(
            source_id=Service(
                name="database",
                environment=Environment.PRODUCTION,
                version="v1",
            ).id,
            source_type=EvidenceType.METRIC,
            target_id=Service(
                name="payment-service",
                environment=Environment.PRODUCTION,
                version="v1",
            ).id,
            target_type=EvidenceType.METRIC,
            relationship_type=RelationshipType.CORRELATION,
            timestamp=datetime.now(),
            strength=1.5,
            explanation="Invalid evidence strength.",
        )


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        Metric(
            timestamp=datetime.now(),
            service="payment-service",
            environment=Environment.PRODUCTION,
            metric_name="latency",
            value=100,
            unit="ms",
            unknown_field="should fail",
        )
