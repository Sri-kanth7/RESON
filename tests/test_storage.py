from datetime import datetime, timezone
from uuid import uuid4

from src.data.schemas import (
    Deployment,
    DeploymentStatus,
    Environment,
    Incident,
    IncidentSeverity,
    IncidentStatus,
    Event,
    EventType,
    Evidence,
    EvidenceType,
    Log,
    LogLevel,
    Metric,
    Service,
)
from src.data.supabase_client import get_supabase_client
from src.data.supabase_storage import SupabaseStorage


def create_storage() -> SupabaseStorage:
    """Create a storage instance for integration tests."""
    return SupabaseStorage(get_supabase_client())


def test_service_persistence():
    storage = create_storage()

    service_name = f"test-service-{uuid4().hex[:8]}"

    service = Service(
        name=service_name,
        environment=Environment.DEVELOPMENT,
        version="1.0.0",
        description="RESON storage test service",
    )

    stored = storage.save_service(service)
    retrieved = storage.get_service(str(service.id))

    assert stored.id == service.id
    assert stored.name == service.name
    assert stored.environment == service.environment
    assert stored.version == service.version

    assert retrieved is not None
    assert retrieved.id == service.id
    assert retrieved.name == service.name
    assert retrieved.environment == service.environment


def test_metric_persistence():
    storage = create_storage()

    metric = Metric(
        timestamp=datetime.now(timezone.utc),
        service="test-service",
        environment=Environment.DEVELOPMENT,
        metric_name="latency",
        value=125.5,
        unit="ms",
    )

    stored = storage.save_metric(metric)

    assert stored.id == metric.id
    assert stored.service == metric.service
    assert stored.metric_name == metric.metric_name
    assert stored.value == metric.value
    assert stored.unit == metric.unit


def test_metric_historical_query():
    storage = create_storage()

    metric = Metric(
        timestamp=datetime.now(timezone.utc),
        service="test-service",
        environment=Environment.DEVELOPMENT,
        metric_name="latency",
        value=150.0,
        unit="ms",
    )

    storage.save_metric(metric)

    results = storage.get_metrics(
        service="test-service",
        metric_name="latency",
    )

    assert any(
        result.id == metric.id
        for result in results
    )


def test_log_persistence():
    storage = create_storage()

    log = Log(
        timestamp=datetime.now(timezone.utc),
        service="test-service",
        environment=Environment.DEVELOPMENT,
        level=LogLevel.ERROR,
        message="Database connection failed",
        metadata={
            "request_id": "test-request",
            "attempt": 1,
        },
    )

    stored = storage.save_log(log)

    assert stored.id == log.id
    assert stored.service == log.service
    assert stored.level == log.level
    assert stored.message == log.message
    assert stored.metadata == log.metadata


def test_log_historical_query():
    storage = create_storage()

    log = Log(
        timestamp=datetime.now(timezone.utc),
        service="test-service",
        environment=Environment.DEVELOPMENT,
        level=LogLevel.ERROR,
        message="Historical test error",
        metadata={},
    )

    storage.save_log(log)

    results = storage.get_logs(
        service="test-service",
        level="ERROR",
    )

    assert any(
        result.id == log.id
        for result in results
    )


def test_event_persistence():
    storage = create_storage()

    event = Event(
        timestamp=datetime.now(timezone.utc),
        service="test-service",
        environment=Environment.DEVELOPMENT,
        event_type=EventType.DATABASE_PRESSURE,
        description="Database pressure detected",
        metadata={
            "connection_pool": 95,
            "threshold": 80,
        },
    )

    stored = storage.save_event(event)

    assert stored.id == event.id
    assert stored.service == event.service
    assert stored.event_type == event.event_type
    assert stored.description == event.description
    assert stored.metadata == event.metadata


def test_event_historical_query():
    storage = create_storage()

    event = Event(
        timestamp=datetime.now(timezone.utc),
        service="test-service",
        environment=Environment.DEVELOPMENT,
        event_type=EventType.RESTART,
        description="Service restarted",
        metadata={},
    )

    storage.save_event(event)

    results = storage.get_events(
        service="test-service",
        event_type="restart",
    )

    assert any(
        result.id == event.id
        for result in results
    )


def test_deployment_persistence():
    storage = create_storage()

    deployment = Deployment(
        timestamp=datetime.now(timezone.utc),
        service="test-service",
        environment=Environment.DEVELOPMENT,
        version="2.0.0",
        status=DeploymentStatus.SUCCESS,
        metadata={
            "commit": "abc123",
            "author": "test-user",
        },
    )

    stored = storage.save_deployment(deployment)

    assert stored.id == deployment.id
    assert stored.service == deployment.service
    assert stored.version == deployment.version
    assert stored.status == deployment.status
    assert stored.metadata == deployment.metadata


def test_deployment_historical_query():
    storage = create_storage()

    deployment = Deployment(
        timestamp=datetime.now(timezone.utc),
        service="test-service",
        environment=Environment.DEVELOPMENT,
        version="3.0.0",
        status=DeploymentStatus.FAILED,
        metadata={},
    )

    storage.save_deployment(deployment)

    results = storage.get_deployments(
        service="test-service",
        status="failed",
    )

    assert any(
        result.id == deployment.id
        for result in results
    )


def test_incident_persistence():
    storage = create_storage()

    incident = Incident(
        title="Database latency spike",
        start_time=datetime.now(timezone.utc),
        end_time=None,
        severity=IncidentSeverity.HIGH,
        status=IncidentStatus.OPEN,
        scenario="database_latency",
        affected_services=[
            "api-service",
            "database-service",
        ],
    )

    stored = storage.save_incident(incident)

    assert stored.id == incident.id
    assert stored.title == incident.title
    assert stored.severity == incident.severity
    assert stored.status == incident.status
    assert stored.scenario == incident.scenario
    assert stored.affected_services == incident.affected_services


def test_incident_historical_query():
    storage = create_storage()

    incident = Incident(
        title="Historical service failure",
        start_time=datetime.now(timezone.utc),
        end_time=None,
        severity=IncidentSeverity.CRITICAL,
        status=IncidentStatus.RESOLVED,
        scenario="service_failure",
        affected_services=["test-service"],
    )

    storage.save_incident(incident)

    results = storage.get_incidents(
        severity="critical",
        status="resolved",
        scenario="service_failure",
    )

    assert any(
        result.id == incident.id
        for result in results
    )


def test_evidence_persistence():
    storage = create_storage()

    source_id = uuid4()
    target_id = uuid4()

    evidence = Evidence(
        source_id=source_id,
        source_type=EvidenceType.DEPLOYMENT,
        target_id=target_id,
        target_type=EvidenceType.INCIDENT,
        relationship_type="temporal",
        timestamp=datetime.now(timezone.utc),
        strength=0.92,
        explanation="Deployment occurred shortly before the incident.",
    )

    stored = storage.save_evidence(evidence)

    assert stored.id == evidence.id
    assert stored.source_id == source_id
    assert stored.source_type == evidence.source_type
    assert stored.target_id == target_id
    assert stored.target_type == evidence.target_type
    assert stored.relationship_type == evidence.relationship_type
    assert stored.strength == evidence.strength
    assert stored.explanation == evidence.explanation


def test_evidence_historical_query():
    storage = create_storage()

    source_id = uuid4()
    target_id = uuid4()

    evidence = Evidence(
        source_id=source_id,
        source_type=EvidenceType.EVENT,
        target_id=target_id,
        target_type=EvidenceType.INCIDENT,
        relationship_type="correlation",
        timestamp=datetime.now(timezone.utc),
        strength=0.85,
        explanation="Event supports the incident timeline.",
    )

    storage.save_evidence(evidence)

    results = storage.get_evidence(
        source_id=str(source_id),
        target_id=str(target_id),
        relationship_type="correlation",
    )

    assert any(
        result.id == evidence.id
        for result in results
    )
