from datetime import datetime, timezone
from uuid import uuid4

from src.data.schemas import Environment, Metric, Service
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