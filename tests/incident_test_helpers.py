"""Shared offline helpers for Phase 3 incident intelligence tests.

Everything here runs without ``.env``, Supabase, network, Streamlit, or an
LLM. All records use explicit, deterministic UUIDs so that algorithmic results
(and repeated ``model_dump()`` calls) are reproducible.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid5

from src.data.schemas import (
    Deployment,
    DeploymentStatus,
    Environment,
    Event,
    EventType,
    Evidence,
    Incident,
    Log,
    LogLevel,
    Metric,
    Service,
)
from src.data.storage import Storage
from src.intelligence.models import BaselineKey, DeviationStatus, MetricDeviation

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
KEY = BaselineKey(
    service="api-service",
    environment="development",
    metric_name="latency_ms",
)
PAYMENT_KEY = BaselineKey(
    service="payment-service",
    environment="development",
    metric_name="latency_ms",
)


def ts(seconds: int) -> datetime:
    """Return a deterministic timestamp offset from the Phase 3 reference."""
    return T0 + timedelta(seconds=seconds)


TEST_NAMESPACE = UUID("0a0b0c0d-1111-4222-8333-444455556666")


def fixed_uuid(seed: str) -> UUID:
    """Return a deterministic UUID for a stable test identity."""
    return uuid5(TEST_NAMESPACE, f"incident-test:{seed}")


def make_deviation(
    seconds: int,
    *,
    status: DeviationStatus = DeviationStatus.ANOMALOUS,
    key: BaselineKey = KEY,
    value: float = 300.0,
    z_score: float | None = 4.0,
    seed: str = "1",
) -> MetricDeviation:
    return MetricDeviation(
        record_id=fixed_uuid(seed),
        timestamp=ts(seconds),
        key=key,
        value=value,
        baseline_mean=100.0,
        absolute_deviation=value - 100.0,
        z_score=z_score,
        status=status,
    )


def make_log(
    seconds: int,
    *,
    level: LogLevel = LogLevel.ERROR,
    service: str = "api-service",
    environment: Environment = Environment.DEVELOPMENT,
    message: str = "elevated failures",
    seed: str = "log1",
) -> Log:
    return Log(
        id=fixed_uuid(seed),
        timestamp=ts(seconds),
        service=service,
        environment=environment,
        level=level,
        message=message,
    )


def make_event(
    seconds: int,
    *,
    event_type: EventType = EventType.SERVICE_FAILURE,
    service: str = "api-service",
    environment: Environment = Environment.DEVELOPMENT,
    description: str = "failure observed",
    seed: str = "ev1",
) -> Event:
    return Event(
        id=fixed_uuid(seed),
        timestamp=ts(seconds),
        service=service,
        environment=environment,
        event_type=event_type,
        description=description,
    )


def make_deployment(
    seconds: int,
    *,
    status: DeploymentStatus = DeploymentStatus.SUCCESS,
    service: str = "payment-service",
    version: str = "2.0.0",
    seed: str = "dep1",
) -> Deployment:
    return Deployment(
        id=fixed_uuid(seed),
        timestamp=ts(seconds),
        service=service,
        environment=Environment.DEVELOPMENT,
        version=version,
        status=status,
    )


def make_metric(
    seconds: int,
    *,
    value: float = 300.0,
    service: str = "api-service",
    metric_name: str = "latency_ms",
    seed: str = "m1",
) -> Metric:
    return Metric(
        id=fixed_uuid(seed),
        timestamp=ts(seconds),
        service=service,
        environment=Environment.DEVELOPMENT,
        metric_name=metric_name,
        value=value,
        unit="ms",
    )


class FakeStorage(Storage):
    """In-memory Storage implementation for offline Phase 3 tests."""

    def __init__(self) -> None:
        self.services: dict[str, Service] = {}
        self.metrics: list[Metric] = []
        self.evidence: dict[UUID, Evidence] = {}
        self.incidents: dict[UUID, Incident] = {}

    def save_service(self, service: Service) -> Service:
        self.services[str(service.id)] = service
        return service

    def get_service(self, service_id: str) -> Service | None:
        return self.services.get(service_id)

    def save_metric(self, metric: Metric) -> Metric:
        self.metrics.append(metric)
        return metric

    def save_metrics(self, metrics: list[Metric]) -> list[Metric]:
        self.metrics.extend(metrics)
        return metrics

    def get_metrics(self, **filters: Any) -> list[Metric]:
        return self.metrics

    def save_evidence(self, evidence: Evidence) -> Evidence:
        self.evidence[evidence.id] = evidence
        return evidence

    def get_evidence(
        self,
        source_id: str | None = None,
        source_type: str | None = None,
        target_id: str | None = None,
        target_type: str | None = None,
        relationship_type: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Evidence]:
        results: list[Evidence] = []
        for item in self.evidence.values():
            if source_id is not None and str(item.source_id) != source_id:
                continue
            if source_type is not None and item.source_type != source_type:
                continue
            if target_id is not None and str(item.target_id) != target_id:
                continue
            if target_type is not None and item.target_type != target_type:
                continue
            if relationship_type is not None and item.relationship_type != relationship_type:
                continue
            if start_time is not None and item.timestamp < start_time:
                continue
            if end_time is not None and item.timestamp >= end_time:
                continue
            results.append(item)
        return results

    def save_incident(self, incident: Incident) -> Incident:
        self.incidents[incident.id] = incident
        return incident

    def get_incidents(
        self,
        severity: str | None = None,
        status: str | None = None,
        scenario: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Incident]:
        results: list[Incident] = []
        for item in self.incidents.values():
            if severity is not None and item.severity != severity:
                continue
            if status is not None and item.status != status:
                continue
            if scenario is not None and item.scenario != scenario:
                continue
            if start_time is not None and item.start_time < start_time:
                continue
            if end_time is not None and item.start_time >= end_time:
                continue
            results.append(item)
        return results

    def save_deployment(self, deployment: Deployment) -> Deployment:
        return deployment

    def save_deployments(self, deployments: list[Deployment]) -> list[Deployment]:
        return deployments

    def get_deployments(self, **filters: Any) -> list[Deployment]:
        return []

    def save_event(self, event: Event) -> Event:
        return event

    def save_events(self, events: list[Event]) -> list[Event]:
        return events

    def get_events(self, **filters: Any) -> list[Event]:
        return []

    def save_log(self, log: Log) -> Log:
        return log

    def save_logs(self, logs: list[Log]) -> list[Log]:
        return logs

    def get_logs(self, **filters: Any) -> list[Log]:
        return []


def all_evidence(storage: FakeStorage) -> list[Evidence]:
    """Return stored evidence deterministically ordered."""
    return [
        storage.evidence[item]
        for item in sorted(storage.evidence, key=str)
    ]