"""Supabase-backed storage implementation for RESON."""

from datetime import datetime

from supabase import Client

from src.data.schemas import Deployment, Event, Incident, Log, Metric, Service
from src.data.storage import Storage


class SupabaseStorage(Storage):
    """Persist RESON data in Supabase PostgreSQL."""

    def __init__(self, client: Client) -> None:
        self._client = client

    def save_service(self, service: Service) -> Service:
        """Insert or update a service and return the domain model."""

        payload = {
            "id": str(service.id),
            "name": service.name,
            "environment": service.environment,
            "version": service.version,
            "description": service.description,
        }

        response = (
            self._client
            .table("services")
            .upsert(payload)
            .execute()
        )

        if not response.data:
            raise RuntimeError("Failed to save service.")

        row = response.data[0]

        return Service.model_validate(
            {
                "id": row["id"],
                "name": row["name"],
                "environment": row["environment"],
                "version": row["version"],
                "description": row["description"],
            }
        )

    def get_service(self, service_id: str) -> Service | None:
        """Retrieve a service by ID."""

        response = (
            self._client
            .table("services")
            .select(
                "id, name, environment, version, description"
            )
            .eq("id", service_id)
            .limit(1)
            .execute()
        )

        if not response.data:
            return None

        return Service.model_validate(response.data[0])

    def save_metric(self, metric: Metric) -> Metric:
        """Insert a metric and return the domain model."""

        payload = {
            "id": str(metric.id),
            "timestamp": metric.timestamp.isoformat(),
            "service": metric.service,
            "environment": metric.environment,
            "metric_name": metric.metric_name,
            "value": metric.value,
            "unit": metric.unit,
        }

        response = (
            self._client
            .table("metrics")
            .insert(payload)
            .execute()
        )

        if not response.data:
            raise RuntimeError("Failed to save metric.")

        row = response.data[0]

        return Metric.model_validate(
            {
                "id": row["id"],
                "timestamp": row["timestamp"],
                "service": row["service"],
                "environment": row["environment"],
                "metric_name": row["metric_name"],
                "value": row["value"],
                "unit": row["unit"],
            }
        )

    def get_metrics(
        self,
        service: str | None = None,
        metric_name: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Metric]:
        """Retrieve metrics using optional historical filters."""

        query = (
            self._client
            .table("metrics")
            .select(
                "id, timestamp, service, environment, "
                "metric_name, value, unit"
            )
        )

        if service is not None:
            query = query.eq("service", service)

        if metric_name is not None:
            query = query.eq("metric_name", metric_name)

        if start_time is not None:
            query = query.gte(
                "timestamp",
                start_time.isoformat(),
            )

        if end_time is not None:
            query = query.lte(
                "timestamp",
                end_time.isoformat(),
            )

        response = (
            query
            .order("timestamp", desc=False)
            .execute()
        )

        return [
            Metric.model_validate(row)
            for row in response.data
        ]

    def save_incident(self, incident: Incident) -> Incident:
        """Insert an incident and return the domain model."""

        payload = {
            "id": str(incident.id),
            "title": incident.title,
            "start_time": incident.start_time.isoformat(),
            "end_time": (
                incident.end_time.isoformat()
                if incident.end_time is not None
                else None
            ),
            "severity": incident.severity,
            "status": incident.status,
            "scenario": incident.scenario,
            "affected_services": incident.affected_services,
        }

        response = (
            self._client
            .table("incidents")
            .insert(payload)
            .execute()
        )

        if not response.data:
            raise RuntimeError("Failed to save incident.")

        row = response.data[0]

        return Incident.model_validate(
            {
                "id": row["id"],
                "title": row["title"],
                "start_time": row["start_time"],
                "end_time": row["end_time"],
                "severity": row["severity"],
                "status": row["status"],
                "scenario": row["scenario"],
                "affected_services": row["affected_services"],
            }
        )

    def get_incidents(
        self,
        severity: str | None = None,
        status: str | None = None,
        scenario: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Incident]:
        """Retrieve incidents using optional historical filters."""

        query = (
            self._client
            .table("incidents")
            .select(
                "id, title, start_time, end_time, "
                "severity, status, scenario, affected_services"
            )
        )

        if severity is not None:
            query = query.eq("severity", severity)

        if status is not None:
            query = query.eq("status", status)

        if scenario is not None:
            query = query.eq("scenario", scenario)

        if start_time is not None:
            query = query.gte(
                "start_time",
                start_time.isoformat(),
            )

        if end_time is not None:
            query = query.lte(
                "start_time",
                end_time.isoformat(),
            )

        response = (
            query
            .order("start_time", desc=False)
            .execute()
        )

        return [
            Incident.model_validate(row)
            for row in response.data
        ]

    def save_deployment(
        self,
        deployment: Deployment,
    ) -> Deployment:
        """Insert a deployment and return the domain model."""

        payload = {
            "id": str(deployment.id),
            "timestamp": deployment.timestamp.isoformat(),
            "service": deployment.service,
            "environment": deployment.environment,
            "version": deployment.version,
            "status": deployment.status,
            "metadata": deployment.metadata,
        }

        response = (
            self._client
            .table("deployments")
            .insert(payload)
            .execute()
        )

        if not response.data:
            raise RuntimeError("Failed to save deployment.")

        row = response.data[0]

        return Deployment.model_validate(
            {
                "id": row["id"],
                "timestamp": row["timestamp"],
                "service": row["service"],
                "environment": row["environment"],
                "version": row["version"],
                "status": row["status"],
                "metadata": row["metadata"],
            }
        )

    def get_deployments(
        self,
        service: str | None = None,
        status: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Deployment]:
        """Retrieve deployments using optional historical filters."""

        query = (
            self._client
            .table("deployments")
            .select(
                "id, timestamp, service, environment, "
                "version, status, metadata"
            )
        )

        if service is not None:
            query = query.eq("service", service)

        if status is not None:
            query = query.eq("status", status)

        if start_time is not None:
            query = query.gte(
                "timestamp",
                start_time.isoformat(),
            )

        if end_time is not None:
            query = query.lte(
                "timestamp",
                end_time.isoformat(),
            )

        response = (
            query
            .order("timestamp", desc=False)
            .execute()
        )

        return [
            Deployment.model_validate(row)
            for row in response.data
        ]

    def save_event(self, event: Event) -> Event:
        """Insert an event and return the domain model."""

        payload = {
            "id": str(event.id),
            "timestamp": event.timestamp.isoformat(),
            "service": event.service,
            "environment": event.environment,
            "event_type": event.event_type,
            "description": event.description,
            "metadata": event.metadata,
        }

        response = (
            self._client
            .table("events")
            .insert(payload)
            .execute()
        )

        if not response.data:
            raise RuntimeError("Failed to save event.")

        row = response.data[0]

        return Event.model_validate(
            {
                "id": row["id"],
                "timestamp": row["timestamp"],
                "service": row["service"],
                "environment": row["environment"],
                "event_type": row["event_type"],
                "description": row["description"],
                "metadata": row["metadata"],
            }
        )

    def get_events(
        self,
        service: str | None = None,
        event_type: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Event]:
        """Retrieve events using optional historical filters."""

        query = (
            self._client
            .table("events")
            .select(
                "id, timestamp, service, environment, "
                "event_type, description, metadata"
            )
        )

        if service is not None:
            query = query.eq("service", service)

        if event_type is not None:
            query = query.eq("event_type", event_type)

        if start_time is not None:
            query = query.gte(
                "timestamp",
                start_time.isoformat(),
            )

        if end_time is not None:
            query = query.lte(
                "timestamp",
                end_time.isoformat(),
            )

        response = (
            query
            .order("timestamp", desc=False)
            .execute()
        )

        return [
            Event.model_validate(row)
            for row in response.data
        ]

    def save_log(self, log: Log) -> Log:
        """Insert a log and return the domain model."""

        payload = {
            "id": str(log.id),
            "timestamp": log.timestamp.isoformat(),
            "service": log.service,
            "environment": log.environment,
            "level": log.level,
            "message": log.message,
            "metadata": log.metadata,
        }

        response = (
            self._client
            .table("logs")
            .insert(payload)
            .execute()
        )

        if not response.data:
            raise RuntimeError("Failed to save log.")

        row = response.data[0]

        return Log.model_validate(
            {
                "id": row["id"],
                "timestamp": row["timestamp"],
                "service": row["service"],
                "environment": row["environment"],
                "level": row["level"],
                "message": row["message"],
                "metadata": row["metadata"],
            }
        )

    def get_logs(
        self,
        service: str | None = None,
        level: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Log]:
        """Retrieve logs using optional historical filters."""

        query = (
            self._client
            .table("logs")
            .select(
                "id, timestamp, service, environment, "
                "level, message, metadata"
            )
        )

        if service is not None:
            query = query.eq("service", service)

        if level is not None:
            query = query.eq("level", level)

        if start_time is not None:
            query = query.gte(
                "timestamp",
                start_time.isoformat(),
            )

        if end_time is not None:
            query = query.lte(
                "timestamp",
                end_time.isoformat(),
            )

        response = (
            query
            .order("timestamp", desc=False)
            .execute()
        )

        return [
            Log.model_validate(row)
            for row in response.data
        ]
