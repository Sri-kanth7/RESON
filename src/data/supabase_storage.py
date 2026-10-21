"""Supabase-backed storage implementation for RESON.

Provenance
----------
Every telemetry record carries a ``source`` (where the observation came from)
and a ``host_id`` (which machine produced it). This backend stores and
returns those fields and accepts them as query filters, so the Supabase
backend enforces the same source isolation as the local SQLite and in-memory
backends.

A Supabase project is created outside RESON, so its tables may predate the
provenance columns. Rather than guessing, the backend **probes** the remote
schema once per table and caches the result:

* when the columns exist, they are written, read and filtered on;
* when they do not, the record is still stored, its provenance is reported as
  unavailable (never silently treated as live telemetry), and the reason is
  exposed through :meth:`SupabaseStorage.provenance_capability` so the
  dashboard can state it plainly.

A source filter against a table without provenance columns is an error, not a
no-op: ignoring it would silently mix live and synthetic data.
"""

from datetime import datetime, timezone

from supabase import Client

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
from src.data.storage import Storage

#: Tables that carry telemetry provenance columns.
PROVENANCE_TABLES = ("metrics", "logs", "events", "deployments", "incidents", "evidence")


class SupabaseStorage(Storage):
    """Persist RESON data in Supabase PostgreSQL."""

    def __init__(self, client: Client) -> None:
        self._client = client
        #: Cache of ``(table, column) -> bool`` schema-probe results.
        self._column_support: dict[tuple[str, str], bool] = {}
        #: Human-readable notes about degraded capabilities.
        self._capability_notes: list[str] = []

    # ------------------------------------------------------------------
    # Schema probing
    # ------------------------------------------------------------------

    def _column_exists(self, table: str, column: str) -> bool:
        """Return whether the remote table has ``column``.

        The probe is executed at most once per table/column and cached. An
        error that is *not* about a missing column (network, permissions) is
        re-raised: it is not evidence that the column is absent.
        """
        key = (table, column)
        if key in self._column_support:
            return self._column_support[key]

        try:
            (
                self._client
                .table(table)
                .select(column)
                .limit(1)
                .execute()
            )
        except Exception as exc:
            message = str(getattr(exc, "message", "") or exc).lower()
            code = str(getattr(exc, "code", "") or "").lower()
            missing = (
                "pgrst204" in code
                or "could not find the" in message
                or ("column" in message and "not exist" in message)
                or "unknown column" in message
            )
            if not missing:
                raise
            supported = False
            note = (
                f"Table '{table}' has no '{column}' column; telemetry "
                "provenance cannot be stored for this table."
            )
            if note not in self._capability_notes:
                self._capability_notes.append(note)
        else:
            supported = True

        self._column_support[key] = supported
        return supported

    def _provenance_columns(self, table: str) -> tuple[str, ...]:
        """Return the provenance columns the remote table actually has."""
        return tuple(
            column
            for column in ("source", "host_id")
            if self._column_exists(table, column)
        )

    def provenance_capability(self) -> dict:
        """Report whether this backend can store telemetry provenance.

        Returns ``{"supported": bool, "tables": {...}, "notes": [...]}``.
        ``supported`` is ``True`` only when every provenance table exposes
        both ``source`` and ``host_id``.
        """
        tables: dict[str, dict] = {}
        fully_supported = True
        for table in PROVENANCE_TABLES:
            columns = self._provenance_columns(table)
            entry = {
                "columns": list(columns),
                "supported": len(columns) == 2,
            }
            tables[table] = entry
            if not entry["supported"]:
                fully_supported = False
        return {
            "supported": fully_supported,
            "tables": tables,
            "notes": list(self._capability_notes),
        }

    def _require_provenance_filter(
        self,
        table: str,
        column: str,
        value: object,
    ) -> None:
        """Raise when a filter cannot be applied because the column is absent.

        Silently ignoring the filter would return records from every source,
        which is exactly the cross-source mixing RESON must never do.
        """
        if value is None:
            return
        if not self._column_exists(table, column):
            raise RuntimeError(
                f"Cannot filter '{table}' by '{column}': the remote table has "
                "no such column. Add the column (ALTER TABLE "
                f"{table} ADD COLUMN {column} TEXT) to enable provenance "
                "filtering."
            )

    def _apply_provenance_filter(
        self,
        query,
        table: str,
        source: str | None,
        host_id: str | None,
    ):
        """Apply source/host filters to a PostgREST query."""
        if source is not None:
            self._require_provenance_filter(table, "source", source)
            query = query.eq("source", classify_source(source))
        if host_id is not None:
            self._require_provenance_filter(table, "host_id", host_id)
            query = query.eq("host_id", host_id)
        return query

    @staticmethod
    def _provenance_payload(record, columns: tuple[str, ...]) -> dict:
        """Return the provenance fields for a write payload."""
        return {column: getattr(record, column, None) for column in columns}

    def _provenance_row(self, row: dict, table: str) -> dict:
        """Return the provenance fields for a read row."""
        return {
            column: row.get(column)
            for column in self._provenance_columns(table)
        }

    # ------------------------------------------------------------------
    # Services
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def save_metric(self, metric: Metric) -> Metric:
        """Insert a metric and return the domain model."""

        columns = self._provenance_columns("metrics")
        payload = {
            "id": str(metric.id),
            "timestamp": metric.timestamp.isoformat(),
            "service": metric.service,
            "environment": metric.environment,
            "metric_name": metric.metric_name,
            "value": metric.value,
            "unit": metric.unit,
            **self._provenance_payload(metric, columns),
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
                **self._provenance_row(row, "metrics"),
            }
        )

    def save_metrics(self, metrics: list[Metric]) -> list[Metric]:
        """Persist multiple metrics in a single database request."""
        if not metrics:
            return []

        columns = self._provenance_columns("metrics")
        payloads = [
            {
                "id": str(metric.id),
                "timestamp": metric.timestamp.isoformat(),
                "service": metric.service,
                "environment": metric.environment,
                "metric_name": metric.metric_name,
                "value": metric.value,
                "unit": metric.unit,
                **self._provenance_payload(metric, columns),
            }
            for metric in metrics
        ]

        response = (
            self._client
            .table("metrics")
            .insert(payloads)
            .execute()
        )

        if len(response.data) != len(metrics):
            raise RuntimeError("Failed to save all metrics.")

        return [
            Metric.model_validate(
                {
                    "id": row["id"],
                    "timestamp": row["timestamp"],
                    "service": row["service"],
                    "environment": row["environment"],
                    "metric_name": row["metric_name"],
                    "value": row["value"],
                    "unit": row["unit"],
                    **self._provenance_row(row, "metrics"),
                }
            )
            for row in response.data
        ]

    def get_metrics(
        self,
        service: str | None = None,
        metric_name: str | None = None,
        source: str | None = None,
        host_id: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Metric]:
        """Retrieve metrics using optional historical and provenance filters."""

        provenance = self._provenance_columns("metrics")
        selected = (
            "id, timestamp, service, environment, "
            "metric_name, value, unit"
        )
        if provenance:
            selected += ", " + ", ".join(provenance)

        query = (
            self._client
            .table("metrics")
            .select(selected)
        )

        if service is not None:
            query = query.eq("service", service)

        if metric_name is not None:
            query = query.eq("metric_name", metric_name)

        query = self._apply_provenance_filter(query, "metrics", source, host_id)

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
            Metric.model_validate(
                {
                    "id": row["id"],
                    "timestamp": row["timestamp"],
                    "service": row["service"],
                    "environment": row["environment"],
                    "metric_name": row["metric_name"],
                    "value": row["value"],
                    "unit": row["unit"],
                    **self._provenance_row(row, "metrics"),
                }
            )
            for row in response.data
        ]

    # ------------------------------------------------------------------
    # Evidence
    # ------------------------------------------------------------------

    def save_evidence(self, evidence: Evidence) -> Evidence:
        """Insert an evidence relationship and return the domain model."""

        columns = self._provenance_columns("evidence")
        payload = {
            "id": str(evidence.id),
            "source_id": str(evidence.source_id),
            "source_type": evidence.source_type,
            "target_id": str(evidence.target_id),
            "target_type": evidence.target_type,
            "relationship_type": evidence.relationship_type,
            "timestamp": evidence.timestamp.isoformat(),
            "strength": evidence.strength,
            "explanation": evidence.explanation,
            **self._provenance_payload(evidence, columns),
        }

        response = (
            self._client
            .table("evidence")
            .insert(payload)
            .execute()
        )

        if not response.data:
            raise RuntimeError("Failed to save evidence.")

        row = response.data[0]

        return Evidence.model_validate(
            {
                "id": row["id"],
                "source_id": row["source_id"],
                "source_type": row["source_type"],
                "target_id": row["target_id"],
                "target_type": row["target_type"],
                "relationship_type": row["relationship_type"],
                "timestamp": row["timestamp"],
                "strength": row["strength"],
                "explanation": row["explanation"],
                **self._provenance_row(row, "evidence"),
            }
        )

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

        provenance = self._provenance_columns("evidence")
        selected = (
            "id, source_id, source_type, target_id, "
            "target_type, relationship_type, timestamp, "
            "strength, explanation"
        )
        if provenance:
            selected += ", " + ", ".join(provenance)

        query = (
            self._client
            .table("evidence")
            .select(selected)
        )

        if source_id is not None:
            query = query.eq("source_id", source_id)

        if source_type is not None:
            query = query.eq("source_type", source_type)

        if target_id is not None:
            query = query.eq("target_id", target_id)

        if target_type is not None:
            query = query.eq("target_type", target_type)

        if relationship_type is not None:
            query = query.eq(
                "relationship_type",
                relationship_type,
            )

        query = self._apply_provenance_filter(query, "evidence", source, host_id)

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
            Evidence.model_validate(
                {
                    "id": row["id"],
                    "source_id": row["source_id"],
                    "source_type": row["source_type"],
                    "target_id": row["target_id"],
                    "target_type": row["target_type"],
                    "relationship_type": row["relationship_type"],
                    "timestamp": row["timestamp"],
                    "strength": row["strength"],
                    "explanation": row["explanation"],
                    **self._provenance_row(row, "evidence"),
                }
            )
            for row in response.data
        ]

    # ------------------------------------------------------------------
    # Incidents
    # ------------------------------------------------------------------

    def save_incident(self, incident: Incident) -> Incident:
        """Insert an incident and return the domain model."""

        columns = self._provenance_columns("incidents")
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
            **self._provenance_payload(incident, columns),
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
                **self._provenance_row(row, "incidents"),
            }
        )

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

        provenance = self._provenance_columns("incidents")
        selected = (
            "id, title, start_time, end_time, "
            "severity, status, scenario, affected_services"
        )
        if provenance:
            selected += ", " + ", ".join(provenance)

        query = (
            self._client
            .table("incidents")
            .select(selected)
        )

        if severity is not None:
            query = query.eq("severity", severity)

        if status is not None:
            query = query.eq("status", status)

        if scenario is not None:
            query = query.eq("scenario", scenario)

        query = self._apply_provenance_filter(query, "incidents", source, host_id)

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
            Incident.model_validate(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "start_time": row["start_time"],
                    "end_time": row["end_time"],
                    "severity": row["severity"],
                    "status": row["status"],
                    "scenario": row["scenario"],
                    "affected_services": row["affected_services"],
                    **self._provenance_row(row, "incidents"),
                }
            )
            for row in response.data
        ]

    # ------------------------------------------------------------------
    # Deployments
    # ------------------------------------------------------------------

    def save_deployment(
        self,
        deployment: Deployment,
    ) -> Deployment:
        """Insert a deployment and return the domain model."""

        columns = self._provenance_columns("deployments")
        payload = {
            "id": str(deployment.id),
            "timestamp": deployment.timestamp.isoformat(),
            "service": deployment.service,
            "environment": deployment.environment,
            "version": deployment.version,
            "status": deployment.status,
            "metadata": deployment.metadata,
            **self._provenance_payload(deployment, columns),
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
                **self._provenance_row(row, "deployments"),
            }
        )

    def save_deployments(
        self,
        deployments: list[Deployment],
    ) -> list[Deployment]:
        """Persist multiple deployments in a single database request."""
        if not deployments:
            return []

        columns = self._provenance_columns("deployments")
        payloads = [
            {
                "id": str(deployment.id),
                "timestamp": deployment.timestamp.isoformat(),
                "service": deployment.service,
                "environment": deployment.environment,
                "version": deployment.version,
                "status": deployment.status,
                "metadata": deployment.metadata,
                **self._provenance_payload(deployment, columns),
            }
            for deployment in deployments
        ]

        response = (
            self._client
            .table("deployments")
            .insert(payloads)
            .execute()
        )

        if len(response.data) != len(deployments):
            raise RuntimeError("Failed to save all deployments.")

        return [
            Deployment.model_validate(
                {
                    "id": row["id"],
                    "timestamp": row["timestamp"],
                    "service": row["service"],
                    "environment": row["environment"],
                    "version": row["version"],
                    "status": row["status"],
                    "metadata": row["metadata"],
                    **self._provenance_row(row, "deployments"),
                }
            )
            for row in response.data
        ]

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

        provenance = self._provenance_columns("deployments")
        selected = (
            "id, timestamp, service, environment, "
            "version, status, metadata"
        )
        if provenance:
            selected += ", " + ", ".join(provenance)

        query = (
            self._client
            .table("deployments")
            .select(selected)
        )

        if service is not None:
            query = query.eq("service", service)

        if status is not None:
            query = query.eq("status", status)

        query = self._apply_provenance_filter(
            query, "deployments", source, host_id
        )

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
            Deployment.model_validate(
                {
                    "id": row["id"],
                    "timestamp": row["timestamp"],
                    "service": row["service"],
                    "environment": row["environment"],
                    "version": row["version"],
                    "status": row["status"],
                    "metadata": row["metadata"],
                    **self._provenance_row(row, "deployments"),
                }
            )
            for row in response.data
        ]

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def save_event(self, event: Event) -> Event:
        """Insert an event and return the domain model."""

        columns = self._provenance_columns("events")
        payload = {
            "id": str(event.id),
            "timestamp": event.timestamp.isoformat(),
            "service": event.service,
            "environment": event.environment,
            "event_type": event.event_type,
            "description": event.description,
            "metadata": event.metadata,
            **self._provenance_payload(event, columns),
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
                **self._provenance_row(row, "events"),
            }
        )

    def save_events(self, events: list[Event]) -> list[Event]:
        """Persist multiple events in a single database request."""
        if not events:
            return []

        columns = self._provenance_columns("events")
        payloads = [
            {
                "id": str(event.id),
                "timestamp": event.timestamp.isoformat(),
                "service": event.service,
                "environment": event.environment,
                "event_type": event.event_type,
                "description": event.description,
                "metadata": event.metadata,
                **self._provenance_payload(event, columns),
            }
            for event in events
        ]

        response = (
            self._client
            .table("events")
            .insert(payloads)
            .execute()
        )

        if len(response.data) != len(events):
            raise RuntimeError("Failed to save all events.")

        return [
            Event.model_validate(
                {
                    "id": row["id"],
                    "timestamp": row["timestamp"],
                    "service": row["service"],
                    "environment": row["environment"],
                    "event_type": row["event_type"],
                    "description": row["description"],
                    "metadata": row["metadata"],
                    **self._provenance_row(row, "events"),
                }
            )
            for row in response.data
        ]

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

        provenance = self._provenance_columns("events")
        selected = (
            "id, timestamp, service, environment, "
            "event_type, description, metadata"
        )
        if provenance:
            selected += ", " + ", ".join(provenance)

        query = (
            self._client
            .table("events")
            .select(selected)
        )

        if service is not None:
            query = query.eq("service", service)

        if event_type is not None:
            query = query.eq("event_type", event_type)

        query = self._apply_provenance_filter(query, "events", source, host_id)

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
            Event.model_validate(
                {
                    "id": row["id"],
                    "timestamp": row["timestamp"],
                    "service": row["service"],
                    "environment": row["environment"],
                    "event_type": row["event_type"],
                    "description": row["description"],
                    "metadata": row["metadata"],
                    **self._provenance_row(row, "events"),
                }
            )
            for row in response.data
        ]

    # ------------------------------------------------------------------
    # Logs
    # ------------------------------------------------------------------

    def save_log(self, log: Log) -> Log:
        """Insert a log and return the domain model."""

        columns = self._provenance_columns("logs")
        payload = {
            "id": str(log.id),
            "timestamp": log.timestamp.isoformat(),
            "service": log.service,
            "environment": log.environment,
            "level": log.level,
            "message": log.message,
            "metadata": log.metadata,
            **self._provenance_payload(log, columns),
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
                **self._provenance_row(row, "logs"),
            }
        )

    def save_logs(self, logs: list[Log]) -> list[Log]:
        """Persist multiple logs in a single database request."""
        if not logs:
            return []

        columns = self._provenance_columns("logs")
        payloads = [
            {
                "id": str(log.id),
                "timestamp": log.timestamp.isoformat(),
                "service": log.service,
                "environment": log.environment,
                "level": log.level,
                "message": log.message,
                "metadata": log.metadata,
                **self._provenance_payload(log, columns),
            }
            for log in logs
        ]

        response = (
            self._client
            .table("logs")
            .insert(payloads)
            .execute()
        )

        if len(response.data) != len(logs):
            raise RuntimeError("Failed to save all logs.")

        return [
            Log.model_validate(
                {
                    "id": row["id"],
                    "timestamp": row["timestamp"],
                    "service": row["service"],
                    "environment": row["environment"],
                    "level": row["level"],
                    "message": row["message"],
                    "metadata": row["metadata"],
                    **self._provenance_row(row, "logs"),
                }
            )
            for row in response.data
        ]

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

        provenance = self._provenance_columns("logs")
        selected = (
            "id, timestamp, service, environment, "
            "level, message, metadata"
        )
        if provenance:
            selected += ", " + ", ".join(provenance)

        query = (
            self._client
            .table("logs")
            .select(selected)
        )

        if service is not None:
            query = query.eq("service", service)

        if level is not None:
            query = query.eq("level", level)

        query = self._apply_provenance_filter(query, "logs", source, host_id)

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
            Log.model_validate(
                {
                    "id": row["id"],
                    "timestamp": row["timestamp"],
                    "service": row["service"],
                    "environment": row["environment"],
                    "level": row["level"],
                    "message": row["message"],
                    "metadata": row["metadata"],
                    **self._provenance_row(row, "logs"),
                }
            )
            for row in response.data
        ]

    # ------------------------------------------------------------------
    # Aggregation helpers
    # ------------------------------------------------------------------

    def count_records(
        self,
        source: str | None = None,
        host_id: str | None = None,
    ) -> dict[str, int]:
        """Return record counts by type using database-side aggregation.

        PostgREST computes the count, so the cost does not grow with the
        retained history the way a full scan would.
        """
        tables = {
            "metrics": "metrics",
            "logs": "logs",
            "events": "events",
            "deployments": "deployments",
        }
        counts: dict[str, int] = {}
        for key, table in tables.items():
            query = self._client.table(table).select("id", count="exact")
            query = self._apply_provenance_filter(
                query, table, source, host_id
            )
            response = query.execute()
            total = getattr(response, "count", None)
            if total is None:
                total = len(response.data or [])
            counts[key] = int(total)
        return counts

    def list_sources(self) -> list[str]:
        """Return the distinct telemetry sources present in the remote tables.

        Only tables that expose a provenance column can contribute sources.
        """
        sources: set[str] = set()
        for table in ("metrics", "logs", "events", "deployments"):
            if not self._column_exists(table, "source"):
                continue
            response = (
                self._client
                .table(table)
                .select("source")
                .execute()
            )
            for row in response.data or []:
                value = row.get("source")
                sources.add(value if value else "")
        return sorted(sources)

    def list_hosts(self, source: str | None = None) -> list[str]:
        """Return the distinct host identifiers for a source."""
        hosts: set[str] = set()
        for table in ("metrics", "logs", "events", "deployments"):
            if not self._column_exists(table, "host_id"):
                continue
            query = self._client.table(table).select("host_id")
            if source is not None:
                query = self._apply_provenance_filter(
                    query, table, source, None
                )
            response = query.execute()
            for row in response.data or []:
                value = row.get("host_id")
                if value:
                    hosts.add(value)
        return sorted(hosts)

    def latest_observation(
        self,
        source: str | None = None,
        host_id: str | None = None,
    ) -> datetime | None:
        """Return the timestamp of the most recent stored observation."""
        query = self._client.table("metrics").select("timestamp")
        query = self._apply_provenance_filter(query, "metrics", source, host_id)
        response = query.order("timestamp", desc=True).limit(1).execute()
        rows = response.data or []
        if not rows:
            return None
        raw = rows[0].get("timestamp")
        if raw is None:
            return None
        parsed = datetime.fromisoformat(str(raw))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
