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
        """Ingest a batch using type-specific bulk persistence operations."""
        metrics: list[Metric] = []
        logs: list[Log] = []
        events: list[Event] = []
        deployments: list[Deployment] = []

        for record in records:
            if isinstance(record, Metric):
                metrics.append(record)
            elif isinstance(record, Log):
                logs.append(record)
            elif isinstance(record, Event):
                events.append(record)
            elif isinstance(record, Deployment):
                deployments.append(record)
            else:
                raise TypeError(
                    f"Unsupported telemetry record type: {type(record).__name__}"
                )

        if metrics:
            self._storage.save_metrics(metrics)

        if logs:
            self._storage.save_logs(logs)

        if events:
            self._storage.save_events(events)

        if deployments:
            self._storage.save_deployments(deployments)

        return IngestionResult(
            metrics=len(metrics),
            logs=len(logs),
            events=len(events),
            deployments=len(deployments),
        )
