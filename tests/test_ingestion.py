from datetime import datetime, timezone

from src.data.ingestion import TelemetryIngestionService
from src.data.schemas import (
    Deployment,
    DeploymentStatus,
    Environment,
    Event,
    EventType,
    Log,
    LogLevel,
    Metric,
)
from src.data.storage import Storage


class FakeStorage(Storage):
    """In-memory storage used to test ingestion routing."""

    def __init__(self) -> None:
        self.metrics = []
        self.logs = []
        self.events = []
        self.deployments = []

    def save_service(self, service):
        raise NotImplementedError

    def get_service(self, service_id):
        raise NotImplementedError

    def save_metric(self, metric):
        self.metrics.append(metric)
        return metric

    def get_metrics(
        self,
        service=None,
        metric_name=None,
        start_time=None,
        end_time=None,
    ):
        raise NotImplementedError

    def save_log(self, log):
        self.logs.append(log)
        return log

    def get_logs(
        self,
        service=None,
        level=None,
        start_time=None,
        end_time=None,
    ):
        raise NotImplementedError

    def save_event(self, event):
        self.events.append(event)
        return event

    def get_events(
        self,
        service=None,
        event_type=None,
        start_time=None,
        end_time=None,
    ):
        raise NotImplementedError

    def save_deployment(self, deployment):
        self.deployments.append(deployment)
        return deployment

    def get_deployments(
        self,
        service=None,
        status=None,
        start_time=None,
        end_time=None,
    ):
        raise NotImplementedError

    def save_incident(self, incident):
        raise NotImplementedError

    def get_incidents(
        self,
        severity=None,
        status=None,
        scenario=None,
        start_time=None,
        end_time=None,
    ):
        raise NotImplementedError

    def save_evidence(self, evidence):
        raise NotImplementedError

    def get_evidence(
        self,
        source_id=None,
        source_type=None,
        target_id=None,
        target_type=None,
        relationship_type=None,
        start_time=None,
        end_time=None,
    ):
        raise NotImplementedError


def test_ingestion_routes_telemetry_to_storage():
    storage = FakeStorage()
    ingestion = TelemetryIngestionService(storage)

    timestamp = datetime(
        2026,
        1,
        1,
        tzinfo=timezone.utc,
    )

    records = [
        Metric(
            timestamp=timestamp,
            service="api-service",
            environment=Environment.DEVELOPMENT,
            metric_name="latency_ms",
            value=120.0,
            unit="ms",
        ),
        Log(
            timestamp=timestamp,
            service="api-service",
            environment=Environment.DEVELOPMENT,
            level=LogLevel.INFO,
            message="Request completed",
        ),
        Event(
            timestamp=timestamp,
            service="api-service",
            environment=Environment.DEVELOPMENT,
            event_type=EventType.RESTART,
            description="Service restarted",
        ),
        Deployment(
            timestamp=timestamp,
            service="api-service",
            environment=Environment.DEVELOPMENT,
            version="1.0.0",
            status=DeploymentStatus.SUCCESS,
        ),
    ]

    result = ingestion.ingest(records)

    assert result.metrics == 1
    assert result.logs == 1
    assert result.events == 1
    assert result.deployments == 1
    assert result.total == 4

    assert len(storage.metrics) == 1
    assert len(storage.logs) == 1
    assert len(storage.events) == 1
    assert len(storage.deployments) == 1


def test_empty_ingestion_returns_zero_counts():
    storage = FakeStorage()
    ingestion = TelemetryIngestionService(storage)

    result = ingestion.ingest([])

    assert result.metrics == 0
    assert result.logs == 0
    assert result.events == 0
    assert result.deployments == 0
    assert result.total == 0


def test_simulator_batch_can_flow_through_ingestion():
    from datetime import datetime, timezone

    from src.data.simulator import SystemSimulator

    storage = FakeStorage()
    ingestion = TelemetryIngestionService(storage)
    simulator = SystemSimulator(seed=42)

    batch = simulator.generate_batch(
        timestamp=datetime(
            2026,
            1,
            1,
            tzinfo=timezone.utc,
        ),
        scenario="deployment",
    )

    result = ingestion.ingest(batch.records())

    assert result.metrics == 15
    assert result.logs == 3
    assert result.events == 1
    assert result.deployments == 1
    assert result.total == 20


def test_ingest_batch_matches_single_record_ingestion():
    from datetime import datetime, timezone

    from src.data.schemas import Environment, LogLevel, Metric

    storage = FakeStorage()
    ingestion = TelemetryIngestionService(storage)

    records = [
        Metric(
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            service="api-service",
            environment=Environment.DEVELOPMENT,
            metric_name="latency",
            value=125.0,
            unit="ms",
        ),
        Log(
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            service="api-service",
            environment=Environment.DEVELOPMENT,
            level=LogLevel.INFO,
            message="Request completed",
        ),
    ]

    result = ingestion.ingest_batch(records)

    assert result.metrics == 1
    assert result.logs == 1
    assert result.events == 0
    assert result.deployments == 0
    assert result.total == 2

    assert len(storage.metrics) == 1
    assert len(storage.logs) == 1


def test_simulator_batch_uses_batch_ingestion():
    from datetime import datetime, timezone

    from src.data.simulator import SystemSimulator

    storage = FakeStorage()
    ingestion = TelemetryIngestionService(storage)
    simulator = SystemSimulator(seed=42)

    batch = simulator.generate_batch(
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        scenario="deployment",
    )

    result = ingestion.ingest_batch(batch.records())

    assert result.metrics == 15
    assert result.logs == 3
    assert result.events == 1
    assert result.deployments == 1
    assert result.total == 20
