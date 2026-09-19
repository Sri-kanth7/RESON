"""Telemetry ingestion service for RESON."""

from dataclasses import dataclass
from typing import Iterable

from src.data.schemas import Deployment, Event, Log, Metric
from src.data.storage import Storage

TelemetryRecord = Metric | Log | Event | Deployment


@dataclass(frozen=True)
class IngestionResult:
    """Summary of records successfully ingested."""

    metrics: int = 0
    logs: int = 0
    events: int = 0
    deployments: int = 0

    @property
    def total(self) -> int:
        """Return the total number of ingested records."""
        return self.metrics + self.logs + self.events + self.deployments


class TelemetryIngestionService:
    """Validate and route telemetry records to persistent storage."""

    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def ingest(self, records: Iterable[TelemetryRecord]) -> IngestionResult:
        """Ingest telemetry records one at a time."""
        metrics = logs = events = deployments = 0

        for record in records:
            if isinstance(record, Metric):
                self._storage.save_metric(record)
                metrics += 1

            elif isinstance(record, Log):
                self._storage.save_log(record)
                logs += 1

            elif isinstance(record, Event):
                self._storage.save_event(record)
                events += 1

            elif isinstance(record, Deployment):
                self._storage.save_deployment(record)
                deployments += 1

            else:
                raise TypeError(
                    "Unsupported telemetry record type: "
                    f"{type(record).__name__}"
                )

        return IngestionResult(
            metrics=metrics,
            logs=logs,
            events=events,
            deployments=deployments,
        )

    def ingest_batch(
        self,
        records: Iterable[TelemetryRecord],
    ) -> IngestionResult:
        """Ingest a batch of telemetry records.

        The first implementation deliberately reuses the validated
        single-record ingestion path. This gives RESON a stable batch
        API without prematurely coupling ingestion to a particular
        database bulk-insert implementation.
        """
        return self.ingest(records)
