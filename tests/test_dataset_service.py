from datetime import datetime, timezone

from src.data.dataset import HistoricalDatasetBuilder
from src.data.dataset_service import HistoricalDatasetService
from src.data.ingestion import TelemetryIngestionService
from tests.test_ingestion import FakeStorage


def test_build_and_persist_historical_dataset():
    storage = FakeStorage()

    ingestion = TelemetryIngestionService(storage)

    builder = HistoricalDatasetBuilder()

    service = HistoricalDatasetService(
        builder=builder,
        ingestion=ingestion,
    )

    result = service.build_and_persist(
        start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    assert result.batches == 15
    assert result.records == 278

    assert result.ingestion.metrics == 225
    assert result.ingestion.logs == 45
    assert result.ingestion.events == 7
    assert result.ingestion.deployments == 1

    assert result.ingestion.total == 278

    assert len(storage.metrics) == 225
    assert len(storage.logs) == 45
    assert len(storage.events) == 7
    assert len(storage.deployments) == 1
