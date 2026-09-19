"""Telemetry ingestion service for RESON."""

from dataclasses import dataclass
from typing import Iterable

from src.data.schemas import (
    Deployment,
    Event,
    Log,
    Metric,
)
from src.data.storage import Storage


TelemetryRecord = Metric | Log | Event | Deployment


@dataclass(frozen=True)
class IngestionResult:
    """Summary of one ingestion operation."""

    metrics: int = 0
    logs: int = 0
    events: int = 0
    deployments: int = 0

    @property
    def total(self) -> int:
        """Return the total number of ingested records."""

        return (
            self.metrics
            + self.logs
            + self.events
            + self.deployments
        )


class TelemetryIngestionService:
    """Persist validated telemetry through the Storage interface."""

    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def ingest(
        self,
        records: Iterable[TelemetryRecord],
    ) -> IngestionResult:
        """Persist a collection of telemetry records."""

        metrics = 0
        logs = 0
        events = 0
        deployments = 0

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
