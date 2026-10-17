"""Contract tests for InMemoryStorage (offline, deterministic, no Supabase).

These tests validate the Storage-abstract behavior that production code
relies on: CRUD round trips, optional filters, inclusive time bounds
(matching SupabaseStorage's gte/lte queries), deterministic ordering,
empty-result handling, and that bulk saves never mutate caller-owned lists.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from src.data.ingestion import TelemetryIngestionService
from src.data.memory_storage import InMemoryStorage
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

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def ts(seconds: int) -> datetime:
    return T0 + timedelta(seconds=seconds)


def make_metric(seconds: int, service: str = "api", name: str = "latency") -> Metric:
    return Metric(
        timestamp=ts(seconds),
        service=service,
        environment=Environment.DEVELOPMENT,
        metric_name=name,
        value=float(seconds),
        unit="ms",
    )


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


def test_service_round_trip():
    storage = InMemoryStorage()
    service = Service(
        name="api", environment=Environment.DEVELOPMENT, version="1.0.0"
    )
    assert storage.save_service(service) is service
    assert storage.get_service(str(service.id)) is service


def test_service_missing_returns_none():
    assert InMemoryStorage().get_service(str(uuid4())) is None


def test_service_save_upserts_by_id():
    storage = InMemoryStorage()
    service = Service(
        name="api", environment=Environment.DEVELOPMENT, version="1.0.0"
    )
    storage.save_service(service)
    updated = service.model_copy(update={"version": "2.0.0"})
    storage.save_service(updated)
    assert storage.get_service(str(service.id)).version == "2.0.0"


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def test_metric_save_and_bulk_do_not_mutate_caller_list():
    storage = InMemoryStorage()
    batch = [make_metric(0), make_metric(10)]
    snapshot = list(batch)
    returned = storage.save_metrics(batch)
    assert batch == snapshot
    assert returned == batch
    assert returned is not batch
    assert storage.save_metrics([]) == []
    assert len(storage.get_metrics()) == 2


def test_metric_filters_and_inclusive_bounds():
    storage = InMemoryStorage()
    storage.save_metrics(
        [
            make_metric(0, service="api", name="latency"),
            make_metric(60, service="api", name="latency"),
            make_metric(120, service="api", name="latency"),
            make_metric(60, service="web", name="latency"),
            make_metric(60, service="api", name="errors"),
        ]
    )
    assert len(storage.get_metrics(service="api")) == 4
    assert len(storage.get_metrics(service="api", metric_name="latency")) == 3
    windowed = storage.get_metrics(
        service="api",
        metric_name="latency",
        start_time=ts(0),
        end_time=ts(60),
    )
    # Both bounds are inclusive, matching SupabaseStorage gte/lte.
    assert [m.timestamp for m in windowed] == [ts(0), ts(60)]
    assert storage.get_metrics(service="missing") == []


def test_metric_ordering_chronological_with_stable_tiebreak():
    storage = InMemoryStorage()
    later = make_metric(30)
    earlier = make_metric(10)
    storage.save_metric(later)
    storage.save_metric(earlier)
    ordered = storage.get_metrics()
    assert [m.timestamp for m in ordered] == [ts(10), ts(30)]

    same_ts = [make_metric(50), make_metric(50)]
    storage.save_metrics(list(reversed(same_ts)))
    same = [m for m in storage.get_metrics() if m.timestamp == ts(50)]
    assert [str(m.id) for m in same] == sorted(str(m.id) for m in same)


# ---------------------------------------------------------------------------
# Logs / events / deployments
# ---------------------------------------------------------------------------


def test_log_filters():
    storage = InMemoryStorage()
    storage.save_logs(
        [
            Log(
                timestamp=ts(0),
                service="api",
                environment=Environment.DEVELOPMENT,
                level=LogLevel.ERROR,
                message="boom",
            ),
            Log(
                timestamp=ts(10),
                service="api",
                environment=Environment.DEVELOPMENT,
                level=LogLevel.INFO,
                message="ok",
            ),
        ]
    )
    assert len(storage.get_logs(service="api")) == 2
    assert len(storage.get_logs(service="api", level="ERROR")) == 1
    # Enum-valued filters are normalized the same way.
    assert len(storage.get_logs(service="api", level=LogLevel.ERROR)) == 1
    assert storage.get_logs(level="CRITICAL") == []


def test_event_filters():
    storage = InMemoryStorage()
    storage.save_events(
        [
            Event(
                timestamp=ts(0),
                service="api",
                environment=Environment.DEVELOPMENT,
                event_type=EventType.RESTART,
                description="restarted",
            ),
            Event(
                timestamp=ts(10),
                service="api",
                environment=Environment.DEVELOPMENT,
                event_type=EventType.SERVICE_FAILURE,
                description="failed",
            ),
        ]
    )
    assert len(storage.get_events(service="api")) == 2
    assert len(storage.get_events(event_type="restart")) == 1
    assert len(storage.get_events(event_type=EventType.RESTART)) == 1
    assert storage.get_events(service="missing") == []


def test_deployment_filters_and_time_window():
    storage = InMemoryStorage()
    storage.save_deployments(
        [
            Deployment(
                timestamp=ts(0),
                service="api",
                environment=Environment.DEVELOPMENT,
                version="1.0.0",
                status=DeploymentStatus.SUCCESS,
            ),
            Deployment(
                timestamp=ts(100),
                service="api",
                environment=Environment.DEVELOPMENT,
                version="1.1.0",
                status=DeploymentStatus.FAILED,
            ),
        ]
    )
    assert len(storage.get_deployments(service="api")) == 2
    assert len(storage.get_deployments(status="failed")) == 1
    assert len(storage.get_deployments(status=DeploymentStatus.FAILED)) == 1
    windowed = storage.get_deployments(start_time=ts(100), end_time=ts(100))
    assert len(windowed) == 1


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------


def make_incident(seconds: int, **overrides) -> Incident:
    params = {
        "title": "incident",
        "start_time": ts(seconds),
        "end_time": None,
        "severity": IncidentSeverity.HIGH,
        "status": IncidentStatus.OPEN,
        "scenario": "latency_spike",
        "affected_services": ["api"],
    }
    params.update(overrides)
    return Incident(**params)


def test_incident_round_trip_and_filters():
    storage = InMemoryStorage()
    storage.save_incident(make_incident(0))
    storage.save_incident(
        make_incident(
            300,
            severity=IncidentSeverity.CRITICAL,
            status=IncidentStatus.RESOLVED,
            scenario="service_failure",
        )
    )
    assert len(storage.get_incidents()) == 2
    assert len(storage.get_incidents(severity="critical")) == 1
    assert len(storage.get_incidents(severity=IncidentSeverity.CRITICAL)) == 1
    assert len(storage.get_incidents(status="resolved")) == 1
    assert len(storage.get_incidents(scenario="service_failure")) == 1
    assert storage.get_incidents(severity="low") == []
    windowed = storage.get_incidents(start_time=ts(0), end_time=ts(0))
    assert len(windowed) == 1


def test_incident_save_replaces_same_id():
    storage = InMemoryStorage()
    incident = make_incident(0)
    storage.save_incident(incident)
    storage.save_incident(incident.model_copy(update={"status": "resolved"}))
    stored = storage.get_incidents()
    assert len(stored) == 1
    assert stored[0].status == "resolved"


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


def make_evidence(seconds: int, source_id=None) -> Evidence:
    return Evidence(
        source_id=source_id or uuid4(),
        source_type=EvidenceType.INCIDENT,
        target_id=uuid4(),
        target_type=EvidenceType.METRIC,
        relationship_type=RelationshipType.CORRELATION,
        timestamp=ts(seconds),
        strength=0.8,
        explanation="correlated",
    )


def test_evidence_filters():
    storage = InMemoryStorage()
    source = uuid4()
    storage.save_evidence(make_evidence(0, source_id=source))
    storage.save_evidence(make_evidence(60, source_id=source))
    other = make_evidence(120)
    storage.save_evidence(other)

    assert len(storage.get_evidence()) == 3
    assert len(storage.get_evidence(source_id=str(source))) == 2
    assert len(storage.get_evidence(source_type="incident")) == 3
    assert len(storage.get_evidence(target_type="metric")) == 3
    assert len(storage.get_evidence(relationship_type="correlation")) == 3
    assert len(storage.get_evidence(target_id=str(other.target_id))) == 1
    windowed = storage.get_evidence(start_time=ts(0), end_time=ts(60))
    assert len(windowed) == 2
    assert storage.get_evidence(source_id=str(uuid4())) == []


def test_evidence_ordering_is_deterministic():
    storage = InMemoryStorage()
    items = [make_evidence(60), make_evidence(0), make_evidence(120)]
    for item in items:
        storage.save_evidence(item)
    ordered = storage.get_evidence()
    assert [e.timestamp for e in ordered] == [ts(0), ts(60), ts(120)]


# ---------------------------------------------------------------------------
# Integration with existing services
# ---------------------------------------------------------------------------


def test_ingestion_routes_through_memory_storage():
    storage = InMemoryStorage()
    ingestion = TelemetryIngestionService(storage)
    result = ingestion.ingest(
        [
            make_metric(0),
            Log(
                timestamp=ts(0),
                service="api",
                environment=Environment.DEVELOPMENT,
                level=LogLevel.INFO,
                message="ok",
            ),
            Event(
                timestamp=ts(0),
                service="api",
                environment=Environment.DEVELOPMENT,
                event_type=EventType.RESTART,
                description="restarted",
            ),
            Deployment(
                timestamp=ts(0),
                service="api",
                environment=Environment.DEVELOPMENT,
                version="1.0.0",
                status=DeploymentStatus.SUCCESS,
            ),
        ]
    )
    assert result.total == 4
    assert len(storage.get_metrics()) == 1
    assert len(storage.get_logs()) == 1
    assert len(storage.get_events()) == 1
    assert len(storage.get_deployments()) == 1

    batch_result = ingestion.ingest_batch([make_metric(10), make_metric(20)])
    assert batch_result.metrics == 2
    assert len(storage.get_metrics()) == 3


def test_incident_persist_round_trip_through_memory_storage():
    from src.incidents.service import IncidentIntelligenceService

    storage = InMemoryStorage()
    service = IncidentIntelligenceService(storage=storage)
    result = service.analyze(
        deviations=[],
        change_points=[],
        logs=[],
        events=[],
        deployments=[],
    )
    service.persist(result)
    assert storage.get_incidents() == []
    assert storage.get_evidence() == []

    incident = make_incident(0)
    evidence = make_evidence(0, source_id=incident.id)
    storage.save_incident(incident)
    storage.save_evidence(evidence)
    candidates = service.historical_candidates()
    assert len(candidates) == 1
    assert candidates[0].incident.id == incident.id
    assert candidates[0].evidence[0].id == evidence.id


def test_storage_instances_are_isolated():
    first = InMemoryStorage()
    second = InMemoryStorage()
    first.save_metric(make_metric(0))
    assert second.get_metrics() == []
