"""Persistent SQLite storage backend for RESON.

Implements the :class:`src.data.storage.Storage` abstraction on top of a
SQLite database file stored on the local machine. Unlike
:class:`src.data.memory_storage.InMemoryStorage`, records survive application
restarts. Unlike :class:`src.data.supabase_storage.SupabaseStorage`, no
network access or cloud credentials are required.

The database location is configurable through the ``RESON_SQLITE_PATH``
environment variable or the ``sqlite_db_path`` configuration field. Sensible
operating-system-specific defaults are used otherwise:

* Windows: ``%APPDATA%\\RESON\\reson.db``
* macOS: ``~/Library/Application Support/RESON/reson.db``
* Linux/other Unix: ``$XDG_DATA_HOME/reson/reson.db``
  (falling back to ``~/.local/share/reson/reson.db``)

All timestamps are stored as ISO-8601 strings in UTC. Time-range queries use
inclusive bounds on both ends (``timestamp >= start`` and ``timestamp <= end``),
matching the existing in-memory and Supabase implementations so the
intelligence layer sees identical semantics regardless of backend.

Schema changes are tracked in a ``schema_migrations`` table and applied in a
single transaction per migration, so initialization is safe to repeat and
migrations are non-destructive.

The database uses WAL journal mode (``-wal``/``-shm`` sidecar files) with a
busy timeout, so the real-time collector can write from a background thread
while the UI reads without failing on transient lock contention.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from src.data.schemas import (
    Deployment,
    DeploymentStatus,
    Environment,
    Event,
    EventType,
    Evidence,
    EvidenceType,
    Incident,
    IncidentSeverity,
    IncidentStatus,
    Log,
    LogLevel,
    Metric,
    Service,
)
from src.data.storage import Storage

#: Current schema version. Increment for each non-destructive migration.
SCHEMA_VERSION = 1

#: Default file name for the local RESON database.
DEFAULT_DB_FILE_NAME = "reson.db"

#: Folder name used under platform data directories.
APP_NAME = "RESON"

#: Environment variable that overrides the default database path.
SQLITE_PATH_ENV = "RESON_SQLITE_PATH"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def default_sqlite_path() -> Path:
    """Return the default persistent database path for this operating system.

    ``RESON_SQLITE_PATH`` takes precedence when set.
    """
    override = os.getenv(SQLITE_PATH_ENV)
    if override:
        return Path(override).expanduser()

    if sys.platform.startswith("win"):
        base = Path(os.getenv("APPDATA", str(Path.home())))
        return base / APP_NAME / DEFAULT_DB_FILE_NAME

    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / APP_NAME
            / DEFAULT_DB_FILE_NAME
        )

    # Linux and other Unix-like systems.
    base = Path(
        os.getenv("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
    )
    return base / "reson" / DEFAULT_DB_FILE_NAME


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------

def _iso(value: datetime) -> str:
    """Return an ISO-8601 string for an aware datetime, normalized to UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp stored in the database back to UTC."""
    if value is None:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
PRAGMA user_version = {version};

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS services (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    environment TEXT NOT NULL,
    version TEXT NOT NULL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS metrics (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    service TEXT NOT NULL,
    environment TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    source TEXT,
    host_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_metrics_service ON metrics(service);
CREATE INDEX IF NOT EXISTS idx_metrics_timestamp ON metrics(timestamp);
CREATE INDEX IF NOT EXISTS idx_metrics_service_timestamp ON metrics(service, timestamp);
CREATE INDEX IF NOT EXISTS idx_metrics_source_host ON metrics(source, host_id);

CREATE TABLE IF NOT EXISTS logs (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    service TEXT NOT NULL,
    environment TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL,
    metadata TEXT
);
CREATE INDEX IF NOT EXISTS idx_logs_service ON logs(service);
CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON logs(timestamp);
CREATE INDEX IF NOT EXISTS idx_logs_service_timestamp ON logs(service, timestamp);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    service TEXT NOT NULL,
    environment TEXT NOT NULL,
    event_type TEXT NOT NULL,
    description TEXT NOT NULL,
    metadata TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_service ON events(service);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_service_timestamp ON events(service, timestamp);

CREATE TABLE IF NOT EXISTS deployments (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    service TEXT NOT NULL,
    environment TEXT NOT NULL,
    version TEXT NOT NULL,
    status TEXT NOT NULL,
    metadata TEXT
);
CREATE INDEX IF NOT EXISTS idx_deployments_service ON deployments(service);
CREATE INDEX IF NOT EXISTS idx_deployments_timestamp ON deployments(timestamp);
CREATE INDEX IF NOT EXISTS idx_deployments_service_timestamp ON deployments(service, timestamp);

CREATE TABLE IF NOT EXISTS incidents (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT,
    severity TEXT NOT NULL,
    status TEXT NOT NULL,
    scenario TEXT,
    affected_services TEXT
);
CREATE INDEX IF NOT EXISTS idx_incidents_start_time ON incidents(start_time);
CREATE INDEX IF NOT EXISTS idx_incidents_severity_status ON incidents(severity, status);
CREATE INDEX IF NOT EXISTS idx_incidents_scenario ON incidents(scenario);

CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    target_type TEXT NOT NULL,
    relationship_type TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    strength REAL NOT NULL,
    explanation TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evidence_source ON evidence(source_id, source_type);
CREATE INDEX IF NOT EXISTS idx_evidence_target ON evidence(target_id, target_type);
CREATE INDEX IF NOT EXISTS idx_evidence_timestamp ON evidence(timestamp);
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    """Open a connection to the database file, creating the parent directory.

    ``busy_timeout`` lets concurrent readers and the single writer (for example
    the real-time collector thread publishing while the UI reads) wait instead
    of immediately failing with ``database is locked``. WAL mode allows readers
    to proceed while a write transaction is active and persists in the database
    file header, so it is a safe no-op once set.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


class SQLiteStorage(Storage):
    """Persist RESON data in a local SQLite database file.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file. Defaults to
        :func:`default_sqlite_path` (an OS-appropriate location under the
        user's data directory), which can be overridden with
        ``RESON_SQLITE_PATH``.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db_path = Path(db_path) if db_path is not None else default_sqlite_path()
        self._schema_ensured = False

    @property
    def db_path(self) -> Path:
        """Return the configured database file path."""
        return self._db_path

    # ------------------------------------------------------------------
    # Connection / schema
    # ------------------------------------------------------------------

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        """Create tables and record the schema migration (idempotent)."""
        if self._schema_ensured:
            return
        conn.executescript(_SCHEMA.format(version=SCHEMA_VERSION))
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations "
            "(version, name, applied_at) VALUES (?, ?, ?)",
            (
                SCHEMA_VERSION,
                "initial_schema",
                _iso(datetime.now(timezone.utc)),
            ),
        )
        conn.commit()
        self._schema_ensured = True

    def _op(self, func):
        """Run ``func(conn)`` inside a connection with schema ensured.

        The connection is committed on success, rolled back on failure, and
        always closed.
        """
        conn = _connect(self._db_path)
        try:
            self._ensure_schema(conn)
            result = func(conn)
            conn.commit()
            return result
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def health(self) -> dict:
        """Return a status report for this database.

        A successful connection and schema check means the database is
        operational; any exception is surfaced as an error.
        """
        try:
            self._op(lambda conn: conn.execute("SELECT 1").fetchone())
            counts = self._counts()
            return {
                "operational": True,
                "error": None,
                "backend": type(self).__name__,
                "database_path": str(self._db_path),
                "counts": counts,
            }
        except Exception as exc:  # pragma: no cover - defensive
            return {
                "operational": False,
                "error": str(exc),
                "backend": type(self).__name__,
                "database_path": str(self._db_path),
                "counts": {},
            }

    def _counts(self) -> dict:
        conn = _connect(self._db_path)
        try:
            self._ensure_schema(conn)
            return {
                "metrics": conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0],
                "logs": conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0],
                "events": conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
                "deployments": conn.execute(
                    "SELECT COUNT(*) FROM deployments"
                ).fetchone()[0],
                "incidents": conn.execute(
                    "SELECT COUNT(*) FROM incidents"
                ).fetchone()[0],
                "evidence": conn.execute(
                    "SELECT COUNT(*) FROM evidence"
                ).fetchone()[0],
                "services": conn.execute(
                    "SELECT COUNT(*) FROM services"
                ).fetchone()[0],
            }
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Services
    # ------------------------------------------------------------------

    def save_service(self, service: Service) -> Service:
        def func(conn: sqlite3.Connection) -> Service:
            conn.execute(
                """
                INSERT INTO services (id, name, environment, version, description)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    environment = excluded.environment,
                    version = excluded.version,
                    description = excluded.description
                """,
                (
                    str(service.id),
                    service.name,
                    str(service.environment),
                    service.version,
                    service.description,
                ),
            )
            return service

        return self._op(func)

    def get_service(self, service_id: str) -> Service | None:
        def func(conn: sqlite3.Connection) -> Service | None:
            row = conn.execute(
                "SELECT id, name, environment, version, description "
                "FROM services WHERE id = ?",
                (service_id,),
            ).fetchone()
            if row is None:
                return None
            return Service(
                id=row["id"],
                name=row["name"],
                environment=row["environment"],
                version=row["version"],
                description=row["description"],
            )

        return self._op(func)

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def save_metric(self, metric: Metric) -> Metric:
        def func(conn: sqlite3.Connection) -> Metric:
            conn.execute(
                """
                INSERT INTO metrics
                    (id, timestamp, service, environment, metric_name, value,
                     unit, source, host_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(metric.id),
                    _iso(metric.timestamp),
                    metric.service,
                    str(metric.environment),
                    metric.metric_name,
                    metric.value,
                    metric.unit,
                    metric.source,
                    metric.host_id,
                ),
            )
            return metric

        return self._op(func)

    def save_metrics(self, metrics: list[Metric]) -> list[Metric]:
        def func(conn: sqlite3.Connection) -> list[Metric]:
            if not metrics:
                return []
            conn.executemany(
                """
                INSERT INTO metrics
                    (id, timestamp, service, environment, metric_name, value,
                     unit, source, host_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(m.id),
                        _iso(m.timestamp),
                        m.service,
                        str(m.environment),
                        m.metric_name,
                        m.value,
                        m.unit,
                        m.source,
                        m.host_id,
                    )
                    for m in metrics
                ],
            )
            return list(metrics)

        return self._op(func)

    def get_metrics(
        self,
        service: str | None = None,
        metric_name: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Metric]:
        def func(conn: sqlite3.Connection) -> list[Metric]:
            query = (
                "SELECT id, timestamp, service, environment, metric_name, value, "
                "unit, source, host_id FROM metrics WHERE 1=1"
            )
            params: list = []
            if service is not None:
                query += " AND service = ?"
                params.append(service)
            if metric_name is not None:
                query += " AND metric_name = ?"
                params.append(metric_name)
            if start_time is not None:
                query += " AND timestamp >= ?"
                params.append(_iso(start_time))
            if end_time is not None:
                query += " AND timestamp <= ?"
                params.append(_iso(end_time))
            query += " ORDER BY timestamp ASC, id ASC"
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_metric(row) for row in rows]

        return self._op(func)

    @staticmethod
    def _row_to_metric(row: sqlite3.Row) -> Metric:
        return Metric(
            id=row["id"],
            timestamp=_parse(row["timestamp"]),
            service=row["service"],
            environment=row["environment"],
            metric_name=row["metric_name"],
            value=row["value"],
            unit=row["unit"],
            source=row["source"],
            host_id=row["host_id"],
        )

    # ------------------------------------------------------------------
    # Logs
    # ------------------------------------------------------------------

    def save_log(self, log: Log) -> Log:
        def func(conn: sqlite3.Connection) -> Log:
            conn.execute(
                """
                INSERT INTO logs
                    (id, timestamp, service, environment, level, message, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(log.id),
                    _iso(log.timestamp),
                    log.service,
                    str(log.environment),
                    str(log.level),
                    log.message,
                    json.dumps(log.metadata),
                ),
            )
            return log

        return self._op(func)

    def save_logs(self, logs: list[Log]) -> list[Log]:
        def func(conn: sqlite3.Connection) -> list[Log]:
            if not logs:
                return []
            conn.executemany(
                """
                INSERT INTO logs
                    (id, timestamp, service, environment, level, message, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(l.id),
                        _iso(l.timestamp),
                        l.service,
                        str(l.environment),
                        str(l.level),
                        l.message,
                        json.dumps(l.metadata),
                    )
                    for l in logs
                ],
            )
            return list(logs)

        return self._op(func)

    def get_logs(
        self,
        service: str | None = None,
        level: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Log]:
        def func(conn: sqlite3.Connection) -> list[Log]:
            query = (
                "SELECT id, timestamp, service, environment, level, message, "
                "metadata FROM logs WHERE 1=1"
            )
            params: list = []
            if service is not None:
                query += " AND service = ?"
                params.append(service)
            if level is not None:
                query += " AND level = ?"
                params.append(level)
            if start_time is not None:
                query += " AND timestamp >= ?"
                params.append(_iso(start_time))
            if end_time is not None:
                query += " AND timestamp <= ?"
                params.append(_iso(end_time))
            query += " ORDER BY timestamp ASC, id ASC"
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_log(row) for row in rows]

        return self._op(func)

    @staticmethod
    def _row_to_log(row: sqlite3.Row) -> Log:
        return Log(
            id=row["id"],
            timestamp=_parse(row["timestamp"]),
            service=row["service"],
            environment=row["environment"],
            level=row["level"],
            message=row["message"],
            metadata=json.loads(row["metadata"] or "{}"),
        )

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def save_event(self, event: Event) -> Event:
        def func(conn: sqlite3.Connection) -> Event:
            conn.execute(
                """
                INSERT INTO events
                    (id, timestamp, service, environment, event_type,
                     description, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(event.id),
                    _iso(event.timestamp),
                    event.service,
                    str(event.environment),
                    str(event.event_type),
                    event.description,
                    json.dumps(event.metadata),
                ),
            )
            return event

        return self._op(func)

    def save_events(self, events: list[Event]) -> list[Event]:
        def func(conn: sqlite3.Connection) -> list[Event]:
            if not events:
                return []
            conn.executemany(
                """
                INSERT INTO events
                    (id, timestamp, service, environment, event_type,
                     description, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(e.id),
                        _iso(e.timestamp),
                        e.service,
                        str(e.environment),
                        str(e.event_type),
                        e.description,
                        json.dumps(e.metadata),
                    )
                    for e in events
                ],
            )
            return list(events)

        return self._op(func)

    def get_events(
        self,
        service: str | None = None,
        event_type: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Event]:
        def func(conn: sqlite3.Connection) -> list[Event]:
            query = (
                "SELECT id, timestamp, service, environment, event_type, "
                "description, metadata FROM events WHERE 1=1"
            )
            params: list = []
            if service is not None:
                query += " AND service = ?"
                params.append(service)
            if event_type is not None:
                query += " AND event_type = ?"
                params.append(event_type)
            if start_time is not None:
                query += " AND timestamp >= ?"
                params.append(_iso(start_time))
            if end_time is not None:
                query += " AND timestamp <= ?"
                params.append(_iso(end_time))
            query += " ORDER BY timestamp ASC, id ASC"
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_event(row) for row in rows]

        return self._op(func)

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> Event:
        return Event(
            id=row["id"],
            timestamp=_parse(row["timestamp"]),
            service=row["service"],
            environment=row["environment"],
            event_type=row["event_type"],
            description=row["description"],
            metadata=json.loads(row["metadata"] or "{}"),
        )

    # ------------------------------------------------------------------
    # Deployments
    # ------------------------------------------------------------------

    def save_deployment(self, deployment: Deployment) -> Deployment:
        def func(conn: sqlite3.Connection) -> Deployment:
            conn.execute(
                """
                INSERT INTO deployments
                    (id, timestamp, service, environment, version, status,
                     metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(deployment.id),
                    _iso(deployment.timestamp),
                    deployment.service,
                    str(deployment.environment),
                    deployment.version,
                    str(deployment.status),
                    json.dumps(deployment.metadata),
                ),
            )
            return deployment

        return self._op(func)

    def save_deployments(self, deployments: list[Deployment]) -> list[Deployment]:
        def func(conn: sqlite3.Connection) -> list[Deployment]:
            if not deployments:
                return []
            conn.executemany(
                """
                INSERT INTO deployments
                    (id, timestamp, service, environment, version, status,
                     metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(d.id),
                        _iso(d.timestamp),
                        d.service,
                        str(d.environment),
                        d.version,
                        str(d.status),
                        json.dumps(d.metadata),
                    )
                    for d in deployments
                ],
            )
            return list(deployments)

        return self._op(func)

    def get_deployments(
        self,
        service: str | None = None,
        status: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Deployment]:
        def func(conn: sqlite3.Connection) -> list[Deployment]:
            query = (
                "SELECT id, timestamp, service, environment, version, status, "
                "metadata FROM deployments WHERE 1=1"
            )
            params: list = []
            if service is not None:
                query += " AND service = ?"
                params.append(service)
            if status is not None:
                query += " AND status = ?"
                params.append(status)
            if start_time is not None:
                query += " AND timestamp >= ?"
                params.append(_iso(start_time))
            if end_time is not None:
                query += " AND timestamp <= ?"
                params.append(_iso(end_time))
            query += " ORDER BY timestamp ASC, id ASC"
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_deployment(row) for row in rows]

        return self._op(func)

    @staticmethod
    def _row_to_deployment(row: sqlite3.Row) -> Deployment:
        return Deployment(
            id=row["id"],
            timestamp=_parse(row["timestamp"]),
            service=row["service"],
            environment=row["environment"],
            version=row["version"],
            status=row["status"],
            metadata=json.loads(row["metadata"] or "{}"),
        )

    # ------------------------------------------------------------------
    # Incidents
    # ------------------------------------------------------------------

    def save_incident(self, incident: Incident) -> Incident:
        def func(conn: sqlite3.Connection) -> Incident:
            conn.execute(
                """
                INSERT INTO incidents
                    (id, title, start_time, end_time, severity, status,
                     scenario, affected_services)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(incident.id),
                    incident.title,
                    _iso(incident.start_time),
                    _iso(incident.end_time) if incident.end_time else None,
                    str(incident.severity),
                    str(incident.status),
                    incident.scenario,
                    json.dumps(incident.affected_services),
                ),
            )
            return incident

        return self._op(func)

    def get_incidents(
        self,
        severity: str | None = None,
        status: str | None = None,
        scenario: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Incident]:
        def func(conn: sqlite3.Connection) -> list[Incident]:
            query = (
                "SELECT id, title, start_time, end_time, severity, status, "
                "scenario, affected_services FROM incidents WHERE 1=1"
            )
            params: list = []
            if severity is not None:
                query += " AND severity = ?"
                params.append(severity)
            if status is not None:
                query += " AND status = ?"
                params.append(status)
            if scenario is not None:
                query += " AND scenario = ?"
                params.append(scenario)
            if start_time is not None:
                query += " AND start_time >= ?"
                params.append(_iso(start_time))
            if end_time is not None:
                query += " AND start_time <= ?"
                params.append(_iso(end_time))
            query += " ORDER BY start_time ASC, id ASC"
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_incident(row) for row in rows]

        return self._op(func)

    @staticmethod
    def _row_to_incident(row: sqlite3.Row) -> Incident:
        return Incident(
            id=row["id"],
            title=row["title"],
            start_time=_parse(row["start_time"]),
            end_time=_parse(row["end_time"]),
            severity=row["severity"],
            status=row["status"],
            scenario=row["scenario"],
            affected_services=json.loads(row["affected_services"] or "[]"),
        )

    # ------------------------------------------------------------------
    # Evidence
    # ------------------------------------------------------------------

    def save_evidence(self, evidence: Evidence) -> Evidence:
        def func(conn: sqlite3.Connection) -> Evidence:
            conn.execute(
                """
                INSERT INTO evidence
                    (id, source_id, source_type, target_id, target_type,
                     relationship_type, timestamp, strength, explanation)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(evidence.id),
                    str(evidence.source_id),
                    str(evidence.source_type),
                    str(evidence.target_id),
                    str(evidence.target_type),
                    str(evidence.relationship_type),
                    _iso(evidence.timestamp),
                    evidence.strength,
                    evidence.explanation,
                ),
            )
            return evidence

        return self._op(func)

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
        def func(conn: sqlite3.Connection) -> list[Evidence]:
            query = (
                "SELECT id, source_id, source_type, target_id, target_type, "
                "relationship_type, timestamp, strength, explanation "
                "FROM evidence WHERE 1=1"
            )
            params: list = []
            if source_id is not None:
                query += " AND source_id = ?"
                params.append(source_id)
            if source_type is not None:
                query += " AND source_type = ?"
                params.append(source_type)
            if target_id is not None:
                query += " AND target_id = ?"
                params.append(target_id)
            if target_type is not None:
                query += " AND target_type = ?"
                params.append(target_type)
            if relationship_type is not None:
                query += " AND relationship_type = ?"
                params.append(relationship_type)
            if start_time is not None:
                query += " AND timestamp >= ?"
                params.append(_iso(start_time))
            if end_time is not None:
                query += " AND timestamp <= ?"
                params.append(_iso(end_time))
            query += " ORDER BY timestamp ASC, id ASC"
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_evidence(row) for row in rows]

        return self._op(func)

    @staticmethod
    def _row_to_evidence(row: sqlite3.Row) -> Evidence:
        return Evidence(
            id=row["id"],
            source_id=row["source_id"],
            source_type=row["source_type"],
            target_id=row["target_id"],
            target_type=row["target_type"],
            relationship_type=row["relationship_type"],
            timestamp=_parse(row["timestamp"]),
            strength=row["strength"],
            explanation=row["explanation"],
        )
