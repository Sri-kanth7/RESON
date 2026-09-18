"""Storage abstraction for RESON."""

from abc import ABC, abstractmethod
from datetime import datetime

from src.data.schemas import Deployment, Event, Evidence, Incident, Log, Metric, Service


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
    def get_metrics(
        self,
        service: str | None = None,
        metric_name: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Metric]:
        """Retrieve metrics using optional historical filters."""
        raise NotImplementedError

    @abstractmethod
    def save_evidence(self, evidence: Evidence) -> Evidence:
        """Persist an evidence relationship and return the stored evidence."""
        raise NotImplementedError

    @abstractmethod
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
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Incident]:
        """Retrieve incidents using optional historical filters."""
        raise NotImplementedError

    @abstractmethod
    def save_deployment(
        self,
        deployment: Deployment,
    ) -> Deployment:
        """Persist a deployment and return the stored deployment."""
        raise NotImplementedError

    @abstractmethod
    def get_deployments(
        self,
        service: str | None = None,
        status: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Deployment]:
        """Retrieve deployments using optional historical filters."""
        raise NotImplementedError

    @abstractmethod
    def save_event(self, event: Event) -> Event:
        """Persist an event and return the stored event."""
        raise NotImplementedError

    @abstractmethod
    def get_events(
        self,
        service: str | None = None,
        event_type: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Event]:
        """Retrieve events using optional historical filters."""
        raise NotImplementedError

    @abstractmethod
    def save_log(self, log: Log) -> Log:
        """Persist a log and return the stored log."""
        raise NotImplementedError

    @abstractmethod
    def get_logs(
        self,
        service: str | None = None,
        level: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Log]:
        """Retrieve logs using optional historical filters."""
        raise NotImplementedError
