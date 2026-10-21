"""Historical dataset persistence service for RESON."""

from dataclasses import dataclass, field
from datetime import datetime

from src.data.dataset import HistoricalDataset, HistoricalDatasetBuilder
from src.data.ingestion import IngestionResult, TelemetryIngestionService
from src.data.schemas import SOURCE_SIMULATION


@dataclass(frozen=True)
class DatasetPersistenceResult:
    """Result of building and persisting a historical dataset."""

    batches: int
    records: int
    ingestion: IngestionResult
    telemetry: list = field(default_factory=list)
    """The exact records that were persisted.

    They are returned so a caller can analyze *the same* records it stored.
    Analyzing a second, untagged copy would produce incidents and evidence
    without provenance, which would then be indistinguishable from records
    of unknown origin.
    """


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
        source: str | None = SOURCE_SIMULATION,
        dataset: HistoricalDataset | None = None,
    ) -> DatasetPersistenceResult:
        """Build a dataset and persist all of its telemetry.

        When ``source`` is provided (defaulting to ``"simulation"``), every
        persisted record is tagged with that source so demonstration data is
        explicitly distinguishable from live host telemetry at storage, query,
        and dashboard time. It is never silently relabeled as live.

        ``dataset`` may be supplied to persist a dataset the caller already
        built. The very same record objects are tagged and returned, so the
        caller analyzes exactly what was stored.
        """
        if dataset is None:
            dataset = self._builder.build(
                start_time=start_time,
                scenarios=scenarios,
            )

        records = list(dataset.records)
        if source is not None:
            for record in records:
                record.source = source

        ingestion_result = self._ingestion.ingest_batch(records)

        return DatasetPersistenceResult(
            batches=dataset.batch_count,
            records=dataset.record_count,
            ingestion=ingestion_result,
            telemetry=records,
        )
