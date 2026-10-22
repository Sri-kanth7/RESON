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

Concurrency design:

* WAL is enabled once per process per database file. SQLite does not invoke
  the busy handler for journal-mode changes, so running
  ``PRAGMA journal_mode = WAL`` on every connection open fails immediately
  with ``database is locked`` when several threads open connections during
  initial database setup. WAL persists in the database file header, so a
  one-time (serialized, bounded-retry) setup is sufficient.
* Within a :class:`SQLiteStorage` instance, write transactions are serialized
  with a thread-safe lock so concurrent ``save_*`` calls queue in-process
  instead of contending for SQLite's single-writer slot. Reads are not
  serialized; WAL allows them to proceed while a write is in flight.
* Transient ``database is locked``/``database is busy`` errors (for example
  from a second process writing the same file) are retried a bounded number
  of times with exponential backoff. Each retry uses a fresh connection and
  re-runs the full transaction, which is safe because a failed transaction is
  rolled back first. Every other error is surfaced to the caller.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from src.data.schemas import (
    Deployment,
    Event,
    Evidence,
    Incident,
    Log,
    Metric,
    SOURCE_UNKNOWN,
    SOURCE_VALUES,
    Service,
    classify_source,
)
from src.data.storage import Storage

#: Current schema version. Increment for each non-destructive migration.
SCHEMA_VERSION = 4

#: Default file name for the local RESON database.
DEFAULT_DB_FILE_NAME = "reson.db"

#: Versioned, non-destructive schema migrations.
#:
#: Each migration is applied at most once (tracked in ``schema_migrations``).
#: Column additions are guarded by :func:`_column_exists` so migrations are
#: repeatable and safe to re-run. Tables are never dropped or recreated.
_MIGRATIONS = (
    {
        "version": 2,
        "name": "add_source_host_id_to_logs_events_deployments",
        "add_columns": {
            "logs": ["source", "host_id"],
            "events": ["source", "host_id"],
            "deployments": ["source", "host_id"],
        },
        "index_tables": ("logs", "events", "deployments"),
    },
    {
        "version": 3,
        "name": "add_source_timestamp_indexes",
        "index_tables": ("logs", "events", "deployments"),
    },
    {
        "version": 4,
        "name": "add_source_host_id_to_incidents_evidence",
        "add_columns": {
            "incidents": ["source", "host_id"],
            "evidence": ["source", "host_id"],
        },
        "indexes": (
            ("idx_incidents_source_host", "incidents", ("source", "host_id")),
            ("idx_incidents_source_start", "incidents", ("source", "start_time")),
            ("idx_evidence_source_host", "evidence", ("source", "host_id")),
            ("idx_evidence_source_timestamp", "evidence", ("source", "timestamp")),
        ),
    },
)

#: Tables known to carry source/host_id telemetry provenance columns.
_PROVENANCE_TABLES = (
    "logs",
    "events",
    "deployments",
    "metrics",
    "incidents",
    "evidence",
)

#: Folder name used under platform data directories.
APP_NAME = "RESON"

#: Environment variable that overrides the default database path.
SQLITE_PATH_ENV = "RESON_SQLITE_PATH"

#: Maximum attempts for an operation that failed with a retryable lock error.
_MAX_LOCK_RETRIES = 5

#: Initial backoff between lock-error retries; doubles each attempt.
_LOCK_RETRY_INITIAL_BACKOFF_SECONDS = 0.05

#: Upper bound for a single backoff sleep between lock-error retries.
_LOCK_RETRY_MAX_BACKOFF_SECONDS = 1.0

#: Serializes one-time WAL journal-mode setup within this process.
_WAL_SETUP_LOCK = threading.Lock()

#: Database paths for which WAL journal mode has already been confirmed.
_WAL_CONFIGURED_PATHS: set[str] = set()


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
# Lock-contention helpers
# ---------------------------------------------------------------------------

def _is_retryable_lock_error(exc: BaseException) -> bool:
    """Return True when ``exc`` is a transient SQLite lock/busy error.

    Only ``OperationalError`` messages that explicitly report the database
    being locked or busy are retried. Constraint violations, corrupt files,
    read-only databases, and every other error must surface immediately.
    """
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    message = str(exc).lower()
    return "locked" in message or "busy" in message


def _call_with_lock_retry(operation):
    """Run ``operation()`` retrying only transient lock/busy errors.

    ``operation`` must be safe to re-run from scratch: it must roll back or
    discard any partially applied state before raising (each attempt opens a
    fresh connection and closes it in a ``finally`` block). Non-retryable
    errors are re-raised unchanged after the first attempt.
    """
    for attempt in range(_MAX_LOCK_RETRIES):
        try:
            return operation()
        except sqlite3.OperationalError as exc:
            if (
                not _is_retryable_lock_error(exc)
                or attempt == _MAX_LOCK_RETRIES - 1
            ):
                raise
            delay = min(
                _LOCK_RETRY_INITIAL_BACKOFF_SECONDS * (2**attempt),
                _LOCK_RETRY_MAX_BACKOFF_SECONDS,
            )
            time.sleep(delay)


def _source_condition(source: str | None) -> tuple[str, list]:
    """Return the SQL condition that filters a source column, and its params.

    The condition is returned without an ``AND`` prefix so it can be used
    both as a ``WHERE`` body and as an additional filter.

    The filter matches on the *classified* source, so ``"unknown"`` also
    matches legacy rows whose stored source is NULL, empty, or a value RESON
    does not recognize. The known-source list is an internal constant, so
    interpolating it is safe; the caller's value is always bound as a
    parameter.
    """
    if source is None:
        return "", []
    resolved = classify_source(source)
    if resolved == SOURCE_UNKNOWN:
        known = ", ".join(
            "'" + value + "'"
            for value in SOURCE_VALUES
            if value != SOURCE_UNKNOWN
        )
        return (
            f"(source IS NULL OR source = '' OR source NOT IN ({known}))",
            [],
        )
    return "source = ?", [resolved]


def _source_clause(source: str | None) -> tuple[str, list]:
    """Return the ``AND``-prefixed SQL fragment that filters a source column."""
    condition, params = _source_condition(source)
    if not condition:
        return "", []
    return " AND " + condition, params


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Return True when ``table`` already has a column named ``column``.

    Table names come from a fixed internal set, so interpolating them into the
    PRAGMA is safe; user input is never interpolated here.
    """
    if table not in _PROVENANCE_TABLES:
        raise ValueError(f"unsupported table for introspection: {table!r}")
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def _applied_migration_versions(conn: sqlite3.Connection) -> set[int]:
    """Return the set of schema migration versions already applied."""
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {int(row[0]) for row in rows}


def _ensure_index(
    conn: sqlite3.Connection,
    index_name: str,
    table: str,
    columns: tuple[str, ...],
) -> None:
    """Create ``index_name`` on ``table`` if it does not exist yet.

    Table and column names come from the internal migration definitions, not
    from user input.
    """
    existing = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name = ?",
        (index_name,),
    ).fetchone()
    if existing is not None:
        return
    column_list = ", ".join(columns)
    conn.execute(f"CREATE INDEX {index_name} ON {table}({column_list})")


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """Apply pending non-destructive schema migrations idempotently.

    Each migration runs at most once (tracked in ``schema_migrations``).
    Column additions are skipped when the column already exists, so re-running
    a migration is a no-op rather than an error. Tables are never dropped or
    recreated.
    """
    applied = _applied_migration_versions(conn)
    for migration in _MIGRATIONS:
        version = int(migration["version"])
        if version in applied:
            continue

        for table, columns in migration.get("add_columns", {}).items():
            for column in columns:
                if _column_exists(conn, table, column):
                    continue
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} TEXT"
                )

        #: Create provenance indexes on the newly added columns, guarded so a
        #: re-run is a no-op when the columns/indexes already exist.
        for table in migration.get("index_tables", ()):
            if not all(_column_exists(conn, table, col)
                       for col in ("source", "host_id")):
                continue
            _ensure_index(conn, f"idx_{table}_source_host", table,
                          ("source", "host_id"))
            _ensure_index(conn, f"idx_{table}_source_timestamp", table,
                          ("source", "timestamp"))

        #: Explicitly declared indexes. The index columns are named per
        #: table because not every table uses the same time column
        #: (``incidents`` stores ``start_time``, not ``timestamp``).
        for index_name, table, columns in migration.get("indexes", ()):
            if not all(_column_exists(conn, table, col) for col in columns):
                continue
            _ensure_index(conn, index_name, table, columns)

        conn.execute(
            "INSERT INTO schema_migrations "
            "(version, name, applied_at) VALUES (?, ?, ?)",
            (
                version,
                str(migration["name"]),
                _iso(datetime.now(timezone.utc)),
            ),
        )


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
CREATE INDEX IF NOT EXISTS idx_metrics_source_timestamp ON metrics(source, timestamp);

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
    affected_services TEXT,
    source TEXT,
    host_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_incidents_start_time ON incidents(start_time);
CREATE INDEX IF NOT EXISTS idx_incidents_severity_status ON incidents(severity, status);
CREATE INDEX IF NOT EXISTS idx_incidents_scenario ON incidents(scenario);
-- The provenance indexes on incidents/evidence are created by migration v4
-- instead of here: this script also runs against legacy databases whose
-- tables do not yet have the source/host_id columns.

CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    target_type TEXT NOT NULL,
    relationship_type TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    strength REAL NOT NULL,
    explanation TEXT NOT NULL,
    source TEXT,
    host_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_evidence_source ON evidence(source_id, source_type);
CREATE INDEX IF NOT EXISTS idx_evidence_target ON evidence(target_id, target_type);
CREATE INDEX IF NOT EXISTS idx_evidence_timestamp ON evidence(timestamp);
"""


def _ensure_wal_mode(conn: sqlite3.Connection, db_path: Path) -> None:
    """Enable WAL journal mode for ``db_path`` once per process.

    WAL mode persists in the database file header, so it only has to be set
    when a process first opens the file. The journal-mode change itself is
    not covered by SQLite's busy handler: if it runs on every connection open
    while other threads are writing during initial database setup, it fails
    immediately with ``database is locked``. Setup is therefore serialized
    with a process-wide lock and retried (bounded, with backoff) against
    transient contention from other processes.
    """
    key = str(Path(db_path).resolve())
    if key in _WAL_CONFIGURED_PATHS:
        return
    with _WAL_SETUP_LOCK:
        if key in _WAL_CONFIGURED_PATHS:
            return

        def attempt() -> None:
            row = conn.execute("PRAGMA journal_mode = WAL").fetchone()
            if row is not None and str(row[0]).lower() == "wal":
                return
            raise sqlite3.OperationalError(
                f"could not enable WAL journal mode for {db_path}"
            )

        _call_with_lock_retry(attempt)
        _WAL_CONFIGURED_PATHS.add(key)


def _connect(db_path: Path) -> sqlite3.Connection:
    """Open a connection to the database file, creating the parent directory.

    ``busy_timeout`` lets concurrent readers and the single writer (for example
    the real-time collector thread publishing while the UI reads) wait instead
    of immediately failing with ``database is locked``. WAL mode (confirmed
    once per process by :func:`_ensure_wal_mode`) allows readers to proceed
    while a write transaction is active and persists in the database file
    header.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        _ensure_wal_mode(conn, db_path)
    except Exception:
        conn.close()
        raise
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
        #: Serializes schema setup so only one thread runs the DDL script.
        self._schema_lock = threading.Lock()
        #: Serializes write transactions within this instance.
        self._write_lock = threading.Lock()

    @property
    def db_path(self) -> Path:
        """Return the configured database file path."""
        return self._db_path

    # ------------------------------------------------------------------
    # Connection / schema
    # ------------------------------------------------------------------

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        """Create tables and record the schema migration (idempotent).

        Thread-safe: concurrent operations on this instance wait here until
        one thread has finished running the (write-locking) DDL script, so
        no other transaction can observe a half-created schema.
        """
        if self._schema_ensured:
            return
        with self._schema_lock:
            if self._schema_ensured:
                return
            conn.executescript(_SCHEMA.format(version=SCHEMA_VERSION))
            #: Record the baseline schema as version 0 so that versioned
            #: migrations (1, 2, ...) are tracked independently and applied
            #: exactly once. Recording SCHEMA_VERSION here would mask pending
            #: migrations.
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations "
                "(version, name, applied_at) VALUES (?, ?, ?)",
                (
                    0,
                    "initial_schema",
                    _iso(datetime.now(timezone.utc)),
                ),
            )
            _apply_migrations(conn)
            conn.commit()
            self._schema_ensured = True

    def _op(self, func):
        """Run ``func(conn)`` inside a read-oriented connection.

        The connection is committed on success, rolled back on failure, and
        always closed. Reads are not serialized with a lock; WAL mode lets
        them run while a write transaction is active. Transient lock errors
        are retried with a fresh connection (bounded, with backoff).
        """
        return self._run(func, write=False)

    def _write_op(self, func):
        """Run ``func(conn)`` as a serialized write transaction.

        Identical to :meth:`_op` except the operation holds this instance's
        write lock, so concurrent ``save_*`` calls queue in-process instead
        of competing for SQLite's single-writer slot. Every write path
        (``save_*``) must go through this method.
        """
        return self._run(func, write=True)

    def _run(self, func, *, write: bool):
        """Run ``func(conn)`` with safe connection/transaction management.

        Each attempt opens a fresh connection, ensures the schema, runs
        ``func``, commits on success, and rolls back and closes in all
        cases. Retryable lock errors trigger a bounded retry (re-running
        the whole transaction on a fresh connection, which is safe because
        the failed attempt was rolled back); all other errors propagate.
        """

        def attempt():
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

        if write:
            with self._write_lock:
                return _call_with_lock_retry(attempt)
        return _call_with_lock_retry(attempt)

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
        def func(conn: sqlite3.Connection) -> dict:
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

        return self._op(func)

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

        return self._write_op(func)

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

        return self._write_op(func)

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

        return self._write_op(func)

    def get_metrics(
        self,
        service: str | None = None,
        metric_name: str | None = None,
        source: str | None = None,
        host_id: str | None = None,
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
            source_clause, source_params = _source_clause(source)
            query += source_clause
            params.extend(source_params)
            if host_id is not None:
                query += " AND host_id = ?"
                params.append(host_id)
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
                    (id, timestamp, service, environment, level, message, metadata,
                     source, host_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(log.id),
                    _iso(log.timestamp),
                    log.service,
                    str(log.environment),
                    str(log.level),
                    log.message,
                    json.dumps(log.metadata),
                    log.source,
                    log.host_id,
                ),
            )
            return log

        return self._write_op(func)

    def save_logs(self, logs: list[Log]) -> list[Log]:
        def func(conn: sqlite3.Connection) -> list[Log]:
            if not logs:
                return []
            conn.executemany(
                """
                INSERT INTO logs
                    (id, timestamp, service, environment, level, message, metadata,
                     source, host_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        l.source,
                        l.host_id,
                    )
                    for l in logs
                ],
            )
            return list(logs)

        return self._write_op(func)

    def get_logs(
        self,
        service: str | None = None,
        level: str | None = None,
        source: str | None = None,
        host_id: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Log]:
        def func(conn: sqlite3.Connection) -> list[Log]:
            query = (
                "SELECT id, timestamp, service, environment, level, message, "
                "metadata, source, host_id FROM logs WHERE 1=1"
            )
            params: list = []
            if service is not None:
                query += " AND service = ?"
                params.append(service)
            if level is not None:
                query += " AND level = ?"
                params.append(level)
            source_clause, source_params = _source_clause(source)
            query += source_clause
            params.extend(source_params)
            if host_id is not None:
                query += " AND host_id = ?"
                params.append(host_id)
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
            source=row["source"],
            host_id=row["host_id"],
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
                     description, metadata, source, host_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(event.id),
                    _iso(event.timestamp),
                    event.service,
                    str(event.environment),
                    str(event.event_type),
                    event.description,
                    json.dumps(event.metadata),
                    event.source,
                    event.host_id,
                ),
            )
            return event

        return self._write_op(func)

    def save_events(self, events: list[Event]) -> list[Event]:
        def func(conn: sqlite3.Connection) -> list[Event]:
            if not events:
                return []
            conn.executemany(
                """
                INSERT INTO events
                    (id, timestamp, service, environment, event_type,
                     description, metadata, source, host_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        e.source,
                        e.host_id,
                    )
                    for e in events
                ],
            )
            return list(events)

        return self._write_op(func)

    def get_events(
        self,
        service: str | None = None,
        event_type: str | None = None,
        source: str | None = None,
        host_id: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Event]:
        def func(conn: sqlite3.Connection) -> list[Event]:
            query = (
                "SELECT id, timestamp, service, environment, event_type, "
                "description, metadata, source, host_id FROM events WHERE 1=1"
            )
            params: list = []
            if service is not None:
                query += " AND service = ?"
                params.append(service)
            if event_type is not None:
                query += " AND event_type = ?"
                params.append(event_type)
            source_clause, source_params = _source_clause(source)
            query += source_clause
            params.extend(source_params)
            if host_id is not None:
                query += " AND host_id = ?"
                params.append(host_id)
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
            source=row["source"],
            host_id=row["host_id"],
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
                     metadata, source, host_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(deployment.id),
                    _iso(deployment.timestamp),
                    deployment.service,
                    str(deployment.environment),
                    deployment.version,
                    str(deployment.status),
                    json.dumps(deployment.metadata),
                    deployment.source,
                    deployment.host_id,
                ),
            )
            return deployment

        return self._write_op(func)

    def save_deployments(self, deployments: list[Deployment]) -> list[Deployment]:
        def func(conn: sqlite3.Connection) -> list[Deployment]:
            if not deployments:
                return []
            conn.executemany(
                """
                INSERT INTO deployments
                    (id, timestamp, service, environment, version, status,
                     metadata, source, host_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        d.source,
                        d.host_id,
                    )
                    for d in deployments
                ],
            )
            return list(deployments)

        return self._write_op(func)

    def get_deployments(
        self,
        service: str | None = None,
        status: str | None = None,
        source: str | None = None,
        host_id: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Deployment]:
        def func(conn: sqlite3.Connection) -> list[Deployment]:
            query = (
                "SELECT id, timestamp, service, environment, version, status, "
                "metadata, source, host_id FROM deployments WHERE 1=1"
            )
            params: list = []
            if service is not None:
                query += " AND service = ?"
                params.append(service)
            if status is not None:
                query += " AND status = ?"
                params.append(status)
            source_clause, source_params = _source_clause(source)
            query += source_clause
            params.extend(source_params)
            if host_id is not None:
                query += " AND host_id = ?"
                params.append(host_id)
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
            source=row["source"],
            host_id=row["host_id"],
        )

    # ------------------------------------------------------------------
    # Incidents
    # ------------------------------------------------------------------

    def save_incident(self, incident: Incident) -> Incident:
        """Persist an incident and return the stored incident.

        The write is an upsert on the incident id: re-saving an incident (for
        example when it moves from ``open`` to ``investigating``) updates the
        stored row instead of failing on the primary key. This matches the
        in-memory backend, which is keyed by id.
        """

        def func(conn: sqlite3.Connection) -> Incident:
            conn.execute(
                """
                INSERT INTO incidents
                    (id, title, start_time, end_time, severity, status,
                     scenario, affected_services, source, host_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    start_time = excluded.start_time,
                    end_time = excluded.end_time,
                    severity = excluded.severity,
                    status = excluded.status,
                    scenario = excluded.scenario,
                    affected_services = excluded.affected_services,
                    source = excluded.source,
                    host_id = excluded.host_id
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
                    incident.source,
                    incident.host_id,
                ),
            )
            return incident

        return self._write_op(func)

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
        def func(conn: sqlite3.Connection) -> list[Incident]:
            query = (
                "SELECT id, title, start_time, end_time, severity, status, "
                "scenario, affected_services, source, host_id "
                "FROM incidents WHERE 1=1"
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
            if source is not None:
                clause, source_params = _source_clause(source)
                query += clause
                params.extend(source_params)
            if host_id is not None:
                query += " AND host_id = ?"
                params.append(host_id)
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
            source=row["source"],
            host_id=row["host_id"],
        )

    # ------------------------------------------------------------------
    # Evidence
    # ------------------------------------------------------------------

    def save_evidence(self, evidence: Evidence) -> Evidence:
        """Persist an evidence relationship and return the stored evidence.

        The write is an upsert on the evidence id, so re-saving the same
        evidence link updates the stored row instead of failing on the primary
        key. This matches the in-memory backend, which is keyed by id.
        """

        def func(conn: sqlite3.Connection) -> Evidence:
            conn.execute(
                """
                INSERT INTO evidence
                    (id, source_id, source_type, target_id, target_type,
                     relationship_type, timestamp, strength, explanation,
                     source, host_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source_id = excluded.source_id,
                    source_type = excluded.source_type,
                    target_id = excluded.target_id,
                    target_type = excluded.target_type,
                    relationship_type = excluded.relationship_type,
                    timestamp = excluded.timestamp,
                    strength = excluded.strength,
                    explanation = excluded.explanation,
                    source = excluded.source,
                    host_id = excluded.host_id
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
                    evidence.source,
                    evidence.host_id,
                ),
            )
            return evidence

        return self._write_op(func)

    def save_evidence_batch(self, evidence: list[Evidence]) -> list[Evidence]:
        """Persist many evidence relationships in one transaction.

        The correlation engine can materialize tens of thousands of evidence
        links in a single analysis. Writing them one row per transaction
        dominated the runtime of a local demonstration; one batched write
        performs the whole set in a single transaction.
        """
        if not evidence:
            return []

        def func(conn: sqlite3.Connection) -> list[Evidence]:
            conn.executemany(
                """
                INSERT INTO evidence
                    (id, source_id, source_type, target_id, target_type,
                     relationship_type, timestamp, strength, explanation,
                     source, host_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source_id = excluded.source_id,
                    source_type = excluded.source_type,
                    target_id = excluded.target_id,
                    target_type = excluded.target_type,
                    relationship_type = excluded.relationship_type,
                    timestamp = excluded.timestamp,
                    strength = excluded.strength,
                    explanation = excluded.explanation,
                    source = excluded.source,
                    host_id = excluded.host_id
                """,
                [
                    (
                        str(item.id),
                        str(item.source_id),
                        str(item.source_type),
                        str(item.target_id),
                        str(item.target_type),
                        str(item.relationship_type),
                        _iso(item.timestamp),
                        item.strength,
                        item.explanation,
                        item.source,
                        item.host_id,
                    )
                    for item in evidence
                ],
            )
            return list(evidence)

        return self._write_op(func)

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
        def func(conn: sqlite3.Connection) -> list[Evidence]:
            query = (
                "SELECT id, source_id, source_type, target_id, target_type, "
                "relationship_type, timestamp, strength, explanation, "
                "source, host_id "
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
            if source is not None:
                clause, source_params = _source_clause(source)
                query += clause
                params.extend(source_params)
            if host_id is not None:
                query += " AND host_id = ?"
                params.append(host_id)
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
            source=row["source"],
            host_id=row["host_id"],
        )

    # ------------------------------------------------------------------
    # Retention
    # ------------------------------------------------------------------

    def purge_before(self, cutoff: datetime) -> dict[str, int]:
        """Delete telemetry records older than ``cutoff``.

        Used to bound local database growth. Only rows strictly older than the
        cutoff are removed; incidents are retained because they are the
        historical record the user cares about, and their evidence chain is
        kept intact.
        """
        threshold = _iso(cutoff)

        def func(conn: sqlite3.Connection) -> dict[str, int]:
            removed: dict[str, int] = {}
            for table in ("metrics", "logs", "events", "deployments"):
                cursor = conn.execute(
                    f"DELETE FROM {table} WHERE timestamp < ?", (threshold,)
                )
                removed[table] = cursor.rowcount if cursor.rowcount > 0 else 0
            # Evidence referencing purged records would dangle; remove
            # evidence whose own timestamp is also older than the cutoff.
            cursor = conn.execute(
                "DELETE FROM evidence WHERE timestamp < ?", (threshold,)
            )
            removed["evidence"] = cursor.rowcount if cursor.rowcount > 0 else 0
            return removed

        return self._write_op(func)

    # ------------------------------------------------------------------
    # Dashboard-optimized reads
    # ------------------------------------------------------------------

    def get_latest_metrics(
        self,
        source: str | None = None,
        host_id: str | None = None,
    ) -> list[Metric]:
        """Return the most recent metric record for each metric name.

        Uses a window function so the database answers the question without
        materializing every retained row. Falls back to the generic scan on
        SQLite versions without window-function support.
        """
        source_clause, source_params = _source_clause(source)
        host_clause = ""
        params = list(source_params)
        if host_id is not None:
            host_clause = " AND host_id = ?"
            params.append(host_id)

        query = (
            "SELECT id, timestamp, service, environment, metric_name, value, "
            "unit, source, host_id FROM ("
            "SELECT *, ROW_NUMBER() OVER ("
            "PARTITION BY metric_name ORDER BY timestamp DESC, id DESC"
            ") AS _rn FROM metrics WHERE 1=1"
            f"{source_clause}{host_clause}) WHERE _rn = 1"
        )

        def func(conn: sqlite3.Connection) -> list[Metric]:
            try:
                rows = conn.execute(query, params).fetchall()
            except sqlite3.OperationalError:
                # Window functions need SQLite 3.25+.
                return Storage.get_latest_metrics(self, source, host_id)
            return [self._row_to_metric(row) for row in rows]

        return self._op(func)

    def count_records(
        self,
        source: str | None = None,
        host_id: str | None = None,
    ) -> dict[str, int]:
        """Return record counts by type using database-side aggregation."""
        clauses = []
        params: list = []
        source_condition, source_params = _source_condition(source)
        if source_condition:
            clauses.append(source_condition)
            params.extend(source_params)
        if host_id is not None:
            clauses.append("host_id = ?")
            params.append(host_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

        def func(conn: sqlite3.Connection) -> dict[str, int]:
            counts: dict[str, int] = {}
            for table in ("metrics", "logs", "events", "deployments"):
                counts[table] = conn.execute(
                    f"SELECT COUNT(*) FROM {table}{where}", params
                ).fetchone()[0]
            return counts

        return self._op(func)

    def list_sources(self) -> list[str]:
        """Return the distinct telemetry sources using indexed queries."""
        def func(conn: sqlite3.Connection) -> list[str]:
            found: set[str] = set()
            for table in ("metrics", "logs", "events", "deployments"):
                rows = conn.execute(
                    f"SELECT DISTINCT source FROM {table}"
                ).fetchall()
                found.update(row[0] or "" for row in rows)
            return sorted(found)

        return self._op(func)

    def list_hosts(self, source: str | None = None) -> list[str]:
        """Return the distinct host identifiers for a source."""
        condition, params = _source_condition(source)

        def func(conn: sqlite3.Connection) -> list[str]:
            if condition:
                rows = conn.execute(
                    "SELECT DISTINCT host_id FROM metrics "
                    f"WHERE host_id IS NOT NULL AND {condition}",
                    params,
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT DISTINCT host_id FROM metrics "
                    "WHERE host_id IS NOT NULL"
                ).fetchall()
            return sorted(row[0] for row in rows if row[0])

        return self._op(func)

    def latest_observation(
        self,
        source: str | None = None,
        host_id: str | None = None,
    ) -> datetime | None:
        """Return the newest observation timestamp using a MAX() query."""
        source_clause, source_params = _source_clause(source)
        host_clause = ""
        params = list(source_params)
        if host_id is not None:
            host_clause = " AND host_id = ?"
            params.append(host_id)

        def func(conn: sqlite3.Connection) -> datetime | None:
            row = conn.execute(
                "SELECT MAX(timestamp) FROM metrics WHERE 1=1"
                + source_clause
                + host_clause,
                params,
            ).fetchone()
            if row is None or row[0] is None:
                return None
            return _parse(row[0])

        return self._op(func)
