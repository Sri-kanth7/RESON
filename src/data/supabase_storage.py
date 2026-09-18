"""Supabase-backed storage implementation for RESON."""

from datetime import datetime

from supabase import Client

from src.data.schemas import Metric, Service
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

        # The database also returns fields such as created_at.
        # Only map fields that belong to the Service domain model.
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

        # Explicit mapping keeps database-only fields from leaking
        # into the Pydantic domain model.
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