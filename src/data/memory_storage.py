"""Deterministic in-memory storage implementation for RESON.

``InMemoryStorage`` implements the existing :class:`src.data.storage.Storage`
abstraction without network access, filesystem persistence, Supabase
configuration, or additional dependencies. It exists so local development,
offline tests, and the intelligence pipelines can run against the real
``Storage`` contract instead of ad-hoc test doubles.

Semantics deliberately match :class:`src.data.supabase_storage.SupabaseStorage`
so the two implementations are interchangeable wherever the ``Storage``
abstraction is consumed:

- equality filters compare plain-string values (the schemas use
  ``use_enum_values=True``, so stored fields are strings; filter arguments may
  be enums or strings and are normalized the same way);
- time bounds are inclusive on both ends (``start_time`` is ``>=`` and
  ``end_time`` is ``<=``), matching the Supabase ``gte``/``lte`` queries;
- ``get_incidents`` filters both ``start_time`` and ``end_time`` against the
  incident's ``start_time`` field, matching the Supabase implementation;
- results are ordered by timestamp ascending. Ties on timestamp are broken by
  stringified record id so ordering is fully deterministic even when the
  database would leave same-timestamp order unspecified;
- ``save_service`` upserts by id; telemetry records are appended;
- incidents and evidence are keyed by id (repeat saves of the same id replace
  the previous entry rather than duplicating it);
- bulk saves accept empty lists (returning ``[]``) and never mutate the
  caller's list; returned lists are new objects.
"""

from __future__ import annotations

from datetime import datetime

from src.data.schemas import (
    Deployment,
    Event,
    Evidence,
    Incident,
    Log,
    Metric,
    Service,
)
from src.data.storage import Storage, source_matches


def _plain(value: object) -> str:
    """Return the plain-string form of an enum or string filter value."""
    return str(getattr(value, "value", value))


def _within(
    timestamp: datetime,
    start_time: datetime | None,
    end_time: datetime | None,
) -> bool:
    """Return whether a timestamp is inside the inclusive ``[start, end]``."""
    if start_time is not None and timestamp < start_time:
        return False
    if end_time is not None and timestamp > end_time:
        return False
    return True


class InMemoryStorage(Storage):
    """Store RESON data in process-local memory.

    All state lives on the instance; nothing is shared between instances and
    nothing leaves the process. The implementation is synchronous and makes no
    defensive copies of the stored models themselves (matching the existing
    test doubles and keeping identity stable); it only avoids aliasing or
    mutating caller-owned *lists*.
    """

    def __init__(self) -> None:
        self._services: dict[str, Service] = {}
        self._metrics: list[Metric] = []
        self._logs: list[Log] = []
        self._events: list[Event] = []
        self._deployments: list[Deployment] = []
        self._incidents: dict[str, Incident] = {}
        self._evidence: dict[str, Evidence] = {}

    # ------------------------------------------------------------------
    # Services
    # ------------------------------------------------------------------

    def save_service(self, service: Service) -> Service:
        """Insert or update a service and return the domain model."""
        self._services[str(service.id)] = service
        return service

    def get_service(self, service_id: str) -> Service | None:
        """Retrieve a service by ID."""
        return self._services.get(service_id)

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def save_metric(self, metric: Metric) -> Metric:
        """Insert a metric and return the domain model."""
        self._metrics.append(metric)
        return metric

    def save_metrics(self, metrics: list[Metric]) -> list[Metric]:
        """Persist multiple metrics efficiently."""
        stored = list(metrics)
        self._metrics.extend(stored)
        return stored

    def get_metrics(
        self,
        service: str | None = None,
        metric_name: str | None = None,
        source: str | None = None,
        host_id: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Metric]:
        """Retrieve metrics using optional historical filters."""
        results = [
            metric
            for metric in self._metrics
            if (service is None or metric.service == _plain(service))
            and (metric_name is None or metric.metric_name == metric_name)
            and source_matches(metric.source, source)
            and (host_id is None or metric.host_id == host_id)
            and _within(metric.timestamp, start_time, end_time)
        ]
        results.sort(key=lambda item: (item.timestamp, str(item.id)))
        return results

    # ------------------------------------------------------------------
    # Evidence
    # ------------------------------------------------------------------

    def save_evidence(self, evidence: Evidence) -> Evidence:
        """Insert an evidence relationship and return the domain model."""
        self._evidence[str(evidence.id)] = evidence
        return evidence

    def save_evidence_batch(self, evidence: list[Evidence]) -> list[Evidence]:
        """Persist multiple evidence relationships in one call."""
        if not evidence:
            return []
        stored = list(evidence)
        for item in stored:
            self._evidence[str(item.id)] = item
        return stored

    def get_evidence(
        self,
        source_id: str | None = None,
        source_type: str | None = None,
        target_id: str | None = None,
        target_type: str | None = None,
        relationship_type: str | None = None,
        source: str | None = None,
        host_id: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Evidence]:
        """Retrieve evidence relationships using optional filters."""
        results = [
            item
            for item in self._evidence.values()
            if (source_id is None or str(item.source_id) == source_id)
            and (source_type is None or _plain(item.source_type) == _plain(source_type))
            and (target_id is None or str(item.target_id) == target_id)
            and (target_type is None or _plain(item.target_type) == _plain(target_type))
            and (
                relationship_type is None
                or _plain(item.relationship_type) == _plain(relationship_type)
            )
            and source_matches(item.source, source)
            and (host_id is None or item.host_id == host_id)
            and _within(item.timestamp, start_time, end_time)
        ]
        results.sort(key=lambda item: (item.timestamp, str(item.id)))
        return results

    # ------------------------------------------------------------------
    # Incidents
    # ------------------------------------------------------------------

    def save_incident(self, incident: Incident) -> Incident:
        """Insert an incident and return the domain model."""
        self._incidents[str(incident.id)] = incident
        return incident

    def get_incidents(
        self,
        severity: str | None = None,
        status: str | None = None,
        scenario: str | None = None,
        source: str | None = None,
        host_id: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Incident]:
        """Retrieve incidents using optional historical and provenance filters."""
        results = [
            incident
            for incident in self._incidents.values()
            if (severity is None or _plain(incident.severity) == _plain(severity))
            and (status is None or _plain(incident.status) == _plain(status))
            and (scenario is None or incident.scenario == _plain(scenario))
            and source_matches(incident.source, source)
            and (host_id is None or incident.host_id == host_id)
            and _within(incident.start_time, start_time, end_time)
        ]
        results.sort(key=lambda item: (item.start_time, str(item.id)))
        return results

    # ------------------------------------------------------------------
    # Deployments
    # ------------------------------------------------------------------

    def save_deployment(self, deployment: Deployment) -> Deployment:
        """Insert a deployment and return the domain model."""
        self._deployments.append(deployment)
        return deployment

    def save_deployments(self, deployments: list[Deployment]) -> list[Deployment]:
        """Persist multiple deployments efficiently."""
        stored = list(deployments)
        self._deployments.extend(stored)
        return stored

    def get_deployments(
        self,
        service: str | None = None,
        status: str | None = None,
        source: str | None = None,
        host_id: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Deployment]:
        """Retrieve deployments using optional historical filters."""
        results = [
            deployment
            for deployment in self._deployments
            if (service is None or deployment.service == _plain(service))
            and (status is None or _plain(deployment.status) == _plain(status))
            and source_matches(deployment.source, source)
            and (host_id is None or deployment.host_id == host_id)
            and _within(deployment.timestamp, start_time, end_time)
        ]
        results.sort(key=lambda item: (item.timestamp, str(item.id)))
        return results

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def save_event(self, event: Event) -> Event:
        """Insert an event and return the domain model."""
        self._events.append(event)
        return event

    def save_events(self, events: list[Event]) -> list[Event]:
        """Persist multiple events efficiently."""
        stored = list(events)
        self._events.extend(stored)
        return stored

    def get_events(
        self,
        service: str | None = None,
        event_type: str | None = None,
        source: str | None = None,
        host_id: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Event]:
        """Retrieve events using optional historical filters."""
        results = [
            event
            for event in self._events
            if (service is None or event.service == _plain(service))
            and (event_type is None or _plain(event.event_type) == _plain(event_type))
            and source_matches(event.source, source)
            and (host_id is None or event.host_id == host_id)
            and _within(event.timestamp, start_time, end_time)
        ]
        results.sort(key=lambda item: (item.timestamp, str(item.id)))
        return results

    # ------------------------------------------------------------------
    # Logs
    # ------------------------------------------------------------------

    def save_log(self, log: Log) -> Log:
        """Insert a log and return the domain model."""
        self._logs.append(log)
        return log

    def save_logs(self, logs: list[Log]) -> list[Log]:
        """Persist multiple logs efficiently."""
        stored = list(logs)
        self._logs.extend(stored)
        return stored

    def get_logs(
        self,
        service: str | None = None,
        level: str | None = None,
        source: str | None = None,
        host_id: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Log]:
        """Retrieve logs using optional historical filters."""
        results = [
            log
            for log in self._logs
            if (service is None or log.service == _plain(service))
            and (level is None or _plain(log.level) == _plain(level))
            and source_matches(log.source, source)
            and (host_id is None or log.host_id == host_id)
            and _within(log.timestamp, start_time, end_time)
        ]
        results.sort(key=lambda item: (item.timestamp, str(item.id)))
        return results

    def purge_before(self, cutoff: datetime) -> dict[str, int]:
        """Delete telemetry records older than ``cutoff``."""
        removed = {
            "metrics": 0,
            "logs": 0,
            "events": 0,
            "deployments": 0,
            "evidence": 0,
        }

        def keep(items, count_key):
            kept = []
            for item in items:
                if item.timestamp < cutoff:
                    removed[count_key] += 1
                else:
                    kept.append(item)
            return kept

        self._metrics = keep(self._metrics, "metrics")
        self._logs = keep(self._logs, "logs")
        self._events = keep(self._events, "events")
        self._deployments = keep(self._deployments, "deployments")

        kept_evidence = []
        for item in self._evidence.values():
            if item.timestamp < cutoff:
                removed["evidence"] += 1
            else:
                kept_evidence.append(item)
        self._evidence = {str(item.id): item for item in kept_evidence}

        return removed
