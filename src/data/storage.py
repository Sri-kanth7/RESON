"""Storage abstraction for RESON."""

from abc import ABC, abstractmethod
from datetime import datetime

from src.data.schemas import (
    Deployment,
    Event,
    Evidence,
    Incident,
    Log,
    Metric,
    Service,
    classify_source,
)


def source_matches(
    record_source: str | None,
    filter_source: str | None,
) -> bool:
    """Return whether a record's source matches a source filter.

    The comparison uses the *classified* source, so the filter
    ``"unknown"`` matches legacy records whose stored source is ``NULL``,
    empty, or the literal ``"unknown"``. A ``None`` filter means "any
    source" and always matches.
    """
    if filter_source is None:
        return True
    return classify_source(record_source) == classify_source(filter_source)


class Storage(ABC):
    """Abstract interface for RESON persistence."""

    @abstractmethod
    def save_service(self, service: Service) -> Service:
        """Persist a service and return the stored service."""
        raise NotImplementedError

    @abstractmethod
    def get_service(self, service_id: str) -> Service | None:
        """Retrieve a service by ID."""
        raise NotImplementedError

    @abstractmethod
    def save_metric(self, metric: Metric) -> Metric:
        """Persist a metric and return the stored metric."""
        raise NotImplementedError

    @abstractmethod
    def save_metrics(self, metrics: list[Metric]) -> list[Metric]:
        """Persist multiple metrics efficiently."""
        raise NotImplementedError

    @abstractmethod
    def get_metrics(
        self,
        service: str | None = None,
        metric_name: str | None = None,
        source: str | None = None,
        host_id: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Metric]:
        """Retrieve metrics using optional historical and provenance filters.

        ``source`` and ``host_id`` are part of the contract: a backend that
        cannot filter by them must say so rather than silently returning
        records from every source.
        """
        raise NotImplementedError

    @abstractmethod
    def save_evidence(self, evidence: Evidence) -> Evidence:
        """Persist an evidence relationship and return the stored evidence."""
        raise NotImplementedError

    def save_evidence_batch(self, evidence: list[Evidence]) -> list[Evidence]:
        """Persist many evidence relationships in one call.

        The default implementation writes each record through
        :meth:`save_evidence`, so every backend behaves correctly without
        change. Backends that can write many rows in a single request or
        transaction override it.

        The batch exists because a single analysis run can produce tens of
        thousands of evidence links (the correlation engine materializes one
        link per accepted signal pair). Writing them one at a time turns a
        local demonstration into tens of seconds of per-row transactions; one
        batched write turns it into a single transaction.

        Semantics match :meth:`save_evidence`: a record with an id that is
        already stored replaces the stored record rather than failing, and an
        empty list is a no-op that returns an empty list.
        """
        if not evidence:
            return []
        return [self.save_evidence(item) for item in evidence]

    @abstractmethod
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
        raise NotImplementedError

    @abstractmethod
    def save_incident(self, incident: Incident) -> Incident:
        """Persist an incident and return the stored incident."""
        raise NotImplementedError

    @abstractmethod
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
        raise NotImplementedError

    @abstractmethod
    def save_deployment(
        self,
        deployment: Deployment,
    ) -> Deployment:
        """Persist a deployment and return the stored deployment."""
        raise NotImplementedError

    @abstractmethod
    def save_deployments(
        self,
        deployments: list[Deployment],
    ) -> list[Deployment]:
        """Persist multiple deployments efficiently."""
        raise NotImplementedError

    @abstractmethod
    def get_deployments(
        self,
        service: str | None = None,
        status: str | None = None,
        source: str | None = None,
        host_id: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Deployment]:
        """Retrieve deployments using optional historical and provenance filters."""
        raise NotImplementedError

    @abstractmethod
    def save_event(self, event: Event) -> Event:
        """Persist an event and return the stored event."""
        raise NotImplementedError

    @abstractmethod
    def save_events(self, events: list[Event]) -> list[Event]:
        """Persist multiple events efficiently."""
        raise NotImplementedError

    @abstractmethod
    def get_events(
        self,
        service: str | None = None,
        event_type: str | None = None,
        source: str | None = None,
        host_id: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Event]:
        """Retrieve events using optional historical and provenance filters."""
        raise NotImplementedError

    @abstractmethod
    def save_log(self, log: Log) -> Log:
        """Persist a log and return the stored log."""
        raise NotImplementedError

    @abstractmethod
    def save_logs(self, logs: list[Log]) -> list[Log]:
        """Persist multiple logs efficiently."""
        raise NotImplementedError

    @abstractmethod
    def get_logs(
        self,
        service: str | None = None,
        level: str | None = None,
        source: str | None = None,
        host_id: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Log]:
        """Retrieve logs using optional historical and provenance filters."""
        raise NotImplementedError

    def provenance_capability(self) -> dict:
        """Report whether this backend can store telemetry provenance.

        Returns ``{"supported": bool, "tables": {...}, "notes": [...]}``.
        Backends that always store provenance report ``supported=True``. A
        backend that cannot must report it, so the dashboard can state the
        limitation instead of implying that every record is attributable.
        """
        return {
            "supported": True,
            "tables": {},
            "notes": [],
        }

    def purge_before(self, cutoff: datetime) -> dict[str, int]:
        """Delete telemetry records older than ``cutoff``.

        Retention support: backends that can delete rows override this method
        and return the number of records removed per collection. The default
        implementation removes nothing and reports zero counts, so a backend
        without deletion support never silently loses data or claims to have
        purged anything.
        """
        return {
            "metrics": 0,
            "logs": 0,
            "events": 0,
            "deployments": 0,
            "evidence": 0,
        }

    def get_latest_metrics(
        self,
        source: str | None = None,
        host_id: str | None = None,
    ) -> list[Metric]:
        """Return the most recent metric record for each metric name.

        The generic implementation scans the matching records. Backends that
        can answer this without a full scan override it, because the
        dashboard calls it on every page load and the scan cost grows with
        the retained history.
        """
        latest: dict[str, Metric] = {}
        for metric in self.get_metrics(source=source, host_id=host_id):
            current = latest.get(metric.metric_name)
            if current is None or metric.timestamp >= current.timestamp:
                latest[metric.metric_name] = metric
        return list(latest.values())

    def count_records(
        self,
        source: str | None = None,
        host_id: str | None = None,
    ) -> dict[str, int]:
        """Return record counts by type, optionally filtered by source/host.

        The generic implementation counts the matching records. Backends that
        can count in the database override it.
        """
        return {
            "metrics": len(self.get_metrics(source=source, host_id=host_id)),
            "logs": len(self.get_logs(source=source, host_id=host_id)),
            "events": len(self.get_events(source=source, host_id=host_id)),
            "deployments": len(
                self.get_deployments(source=source, host_id=host_id)
            ),
        }

    def list_sources(self) -> list[str]:
        """Return the sorted, distinct telemetry sources present in storage.

        The generic implementation scans the records. Backends that can answer
        with a distinct query override it.
        """
        sources: set[str] = set()
        for record in self.get_metrics():
            sources.add(record.source or "")
        for record in self.get_logs():
            sources.add(record.source or "")
        for record in self.get_events():
            sources.add(record.source or "")
        for record in self.get_deployments():
            sources.add(record.source or "")
        return sorted(sources)

    def list_hosts(self, source: str | None = None) -> list[str]:
        """Return the sorted, distinct host identifiers for a source."""
        hosts = {
            record.host_id
            for record in self.get_metrics(source=source)
            if record.host_id
        }
        return sorted(hosts)

    def latest_observation(
        self,
        source: str | None = None,
        host_id: str | None = None,
    ) -> datetime | None:
        """Return the timestamp of the most recent stored observation."""
        latest: datetime | None = None
        for metric in self.get_metrics(source=source, host_id=host_id):
            if latest is None or metric.timestamp > latest:
                latest = metric.timestamp
        return latest
