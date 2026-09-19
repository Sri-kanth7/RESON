"""Historical dataset persistence service for RESON."""

from dataclasses import dataclass
from datetime import datetime

from src.data.dataset import HistoricalDatasetBuilder
from src.data.ingestion import IngestionResult, TelemetryIngestionService


@dataclass(frozen=True)
class DatasetPersistenceResult:
    """Result of building and persisting a historical dataset."""

    batches: int
    records: int
    ingestion: IngestionResult


class HistoricalDatasetService:
    """Build and persist deterministic historical telemetry."""

    def __init__(
        self,
        builder: HistoricalDatasetBuilder,
        ingestion: TelemetryIngestionService,
    ) -> None:
        self._builder = builder
        self._ingestion = ingestion

    def build_and_persist(
        self,
        start_time: datetime,
        scenarios: tuple[str, ...] | list[str] | None = None,
    ) -> DatasetPersistenceResult:
        """Build a dataset and persist all of its telemetry."""
        dataset = self._builder.build(
            start_time=start_time,
            scenarios=scenarios,
        )

        ingestion_result = self._ingestion.ingest_batch(
            dataset.records
        )

        return DatasetPersistenceResult(
            batches=dataset.batch_count,
            records=dataset.record_count,
            ingestion=ingestion_result,
        )
