"""Tests for the persistent SQLite storage backend.

Uses temporary database files only; never touches a production database.
All tests run offline with no Supabase credentials required.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from src.common.config import get_config
from src.data.ingestion import TelemetryIngestionService
from src.data.sqlite_storage import (
    DEFAULT_DB_FILE_NAME,
    SCHEMA_VERSION,
    SQLiteStorage,
    default_sqlite_path,
)
from src.data.supabase_client import get_supabase_client
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
    SOURCE_SIMULATION,
    Service,
)
from src.data.windows_collector import (
    HostMeasurement,
    ProcessMeasurement,
    SOURCE_WINDOWS_LOCAL,
    observations_to_metrics,
)
from src.orchestrator import RESONApplication, create_application


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db_path(tmp_path):
    """A temporary database file path for each test."""
    return tmp_path / "test_reson.db"


@pytest.fixture
def fresh_config(monkeypatch):
    """Isolate cached configuration and client factory from env edits."""
    get_config.cache_clear()
    get_supabase_client.cache_clear()
    yield monkeypatch
    get_config.cache_clear()
    get_supabase_client.cache_clear()


def forbid_supabase_client(monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("Supabase client must not initialize in local mode")

    monkeypatch.setattr("src.orchestrator.get_supabase_client", explode)
    monkeypatch.setattr("src.data.supabase_client.get_supabase_client", explode)


# ---------------------------------------------------------------------------
# Default path
# ---------------------------------------------------------------------------


def test_default_sqlite_path_returns_reson_db(tmp_path, monkeypatch):
    monkeypatch.setenv("RESON_SQLITE_PATH", str(tmp_path / "custom.db"))
    assert default_sqlite_path() == tmp_path / "custom.db"


def test_default_sqlite_path_ends_in_reson_db(monkeypatch):
    monkeypatch.delenv("RESON_SQLITE_PATH", raising=False)
    path = default_sqlite_path()
    assert isinstance(path, Path)
    assert str(path).endswith(DEFAULT_DB_FILE_NAME)


# ---------------------------------------------------------------------------
# Initialization and schema
# ---------------------------------------------------------------------------


def test_init_uses_default_path_when_none(tmp_db_path, monkeypatch):
    monkeypatch.setenv("RESON_SQLITE_PATH", str(tmp_db_path))
    storage = SQLiteStorage()
    assert storage.db_path == tmp_db_path


def test_init_creates_database_file(tmp_db_path):
    assert not tmp_db_path.exists()
    storage = SQLiteStorage(tmp_db_path)
    storage.save_service(
        Service(
            name="svc",
            environment=Environment.DEVELOPMENT,
            version="1.0.0",
        )
    )
    assert tmp_db_path.exists()


def test_repeated_initialization_is_safe(tmp_db_path):
    first = SQLiteStorage(tmp_db_path)
    second = SQLiteStorage(tmp_db_path)
    first.save_service(
        Service(name="s", environment=Environment.DEVELOPMENT, version="1.0.0")
    )
    # A second instance against the same file reads the same data.
    second.get_metrics()  # sanity call: the file is readable
    # Construct a third instance and confirm schema is still intact.
    third = SQLiteStorage(tmp_db_path)
    third._op(lambda conn: conn.execute("SELECT 1").fetchone())


def test_schema_version_recorded(tmp_db_path):
    storage = SQLiteStorage(tmp_db_path)
    storage._op(lambda conn: conn.execute("SELECT 1").fetchone())
    # Re-open: schema migration table persists and version is stable.
    again = SQLiteStorage(tmp_db_path)
    again._op(lambda conn: conn.execute("SELECT 1").fetchone())
    assert SCHEMA_VERSION == 4


def test_schema_migration_table_records_every_version(tmp_db_path):
    """Every defined migration is recorded exactly once in the database."""
    from src.data.sqlite_storage import _MIGRATIONS

    storage = SQLiteStorage(tmp_db_path)
    storage._op(lambda conn: conn.execute("SELECT 1").fetchone())
    rows = storage._op(
        lambda conn: conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
    )
    versions = [row[0] for row in rows]
    assert versions == sorted(set(versions))
    # Version 0 is the baseline schema; every defined migration is applied.
    assert 0 in versions
    for migration in _MIGRATIONS:
        assert int(migration["version"]) in versions
    assert max(versions) == SCHEMA_VERSION


def test_incident_and_evidence_gain_provenance_columns(tmp_db_path):
    """Migration v4 adds source/host_id to the incidents and evidence tables."""
    storage = SQLiteStorage(tmp_db_path)
    storage._op(lambda conn: conn.execute("SELECT 1").fetchone())
    for table in ("incidents", "evidence"):
        columns = storage._op(
            lambda conn, table=table: [
                row[1]
                for row in conn.execute(
                    f"PRAGMA table_info({table})"
                ).fetchall()
            ]
        )
        assert "source" in columns, table
        assert "host_id" in columns, table


def test_pre_existing_database_migrates_and_preserves_records(tmp_db_path):
    # Simulate an older database at schema version 0 with existing data.
    conn = sqlite3.connect(str(tmp_db_path))
    conn.execute("PRAGMA user_version = 0")
    conn.execute(
        "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, "
        "name TEXT, applied_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE services (id TEXT PRIMARY KEY, name TEXT, "
        "environment TEXT, version TEXT, description TEXT)"
    )
    conn.execute(
        "INSERT INTO services (id, name, environment, version, description) "
        "VALUES (?, ?, ?, ?, ?)",
        (str(uuid4()), "legacy", "development", "1.0.0", None),
    )
    conn.commit()
    conn.close()

    storage = SQLiteStorage(tmp_db_path)
    # The migrated database should expose its services through get_service.
    # Re-fetch the id we just inserted by querying raw SQLite.
    raw = sqlite3.connect(str(tmp_db_path))
    row = raw.execute("SELECT id FROM services").fetchone()
    raw.close()
    assert row is not None
    fetched = storage.get_service(row[0])
    assert fetched is not None
    assert fetched.name == "legacy"


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


def test_service_save_and_get_round_trip(tmp_db_path):
    storage = SQLiteStorage(tmp_db_path)
    service = Service(
        name="api",
        environment=Environment.PRODUCTION,
        version="2.0.0",
        description="gateway",
    )
    stored = storage.save_service(service)
    assert stored.id == service.id

    retrieved = storage.get_service(str(service.id))
    assert retrieved is not None
    assert retrieved.id == service.id
    assert retrieved.name == "api"
    assert retrieved.environment == Environment.PRODUCTION
    assert retrieved.version == "2.0.0"
    assert retrieved.description == "gateway"


def test_service_get_missing_returns_none(tmp_db_path):
    assert SQLiteStorage(tmp_db_path).get_service(str(uuid4())) is None


def test_service_upsert_by_id(tmp_db_path):
    storage = SQLiteStorage(tmp_db_path)
    sid = uuid4()
    storage.save_service(
        Service(id=sid, name="v1", environment=Environment.DEVELOPMENT, version="1")
    )
    # Re-using the same id updates the row rather than duplicating it.
    storage.save_service(
        Service(id=sid, name="v2", environment=Environment.DEVELOPMENT, version="2")
    )
    fetched = storage.get_service(str(sid))
    assert fetched is not None
    assert fetched.name == "v2"
    assert fetched.version == "2"


# ---------------------------------------------------------------------------
# Metrics (incl. source and host_id)
# ---------------------------------------------------------------------------


def _make_metric(
    storage_service="test-service",
    source=None,
    host_id=None,
    value=1.0,
    name="cpu_percent",
):
    return Metric(
        timestamp=datetime(2026, 1, 15, 9, 30, 0, tzinfo=timezone.utc),
        service=storage_service,
        environment=Environment.PRODUCTION,
        metric_name=name,
        value=value,
        unit="percent",
        source=source,
        host_id=host_id,
    )


def test_metric_save_and_get_round_trip(tmp_db_path):
    storage = SQLiteStorage(tmp_db_path)
    metric = _make_metric(source="windows_local", host_id="host-1")
    stored = storage.save_metric(metric)
    assert stored.id == metric.id
    assert stored.source == "windows_local"
    assert stored.host_id == "host-1"

    results = storage.get_metrics(service="test-service")
    assert len(results) == 1
    assert results[0].source == "windows_local"
    assert results[0].host_id == "host-1"
    assert results[0].value == 1.0


def test_metric_persists_source_and_host_id(tmp_db_path):
    storage = SQLiteStorage(tmp_db_path)
    storage.save_metric(_make_metric(source="windows_local", host_id="host-a"))
    storage.save_metric(_make_metric(source=None, host_id=None, name="mem"))

    all_metrics = storage.get_metrics()
    by_name = {m.metric_name: m for m in all_metrics}
    assert by_name["cpu_percent"].source == "windows_local"
    assert by_name["cpu_percent"].host_id == "host-a"
    assert by_name["mem"].source is None
    assert by_name["mem"].host_id is None


def test_metric_save_multiple(tmp_db_path):
    storage = SQLiteStorage(tmp_db_path)
    metrics = [
        _make_metric(value=1.0, name="cpu_percent"),
        _make_metric(value=2.0, name="memory_percent"),
        _make_metric(value=3.0, name="disk_percent"),
    ]
    stored = storage.save_metrics(metrics)
    assert len(stored) == 3
    fetched = storage.get_metrics(service="test-service")
    assert len(fetched) == 3
    assert {m.value for m in fetched} == {1.0, 2.0, 3.0}


def test_metric_empty_database_returns_empty(tmp_db_path):
    assert SQLiteStorage(tmp_db_path).get_metrics() == []


def test_metric_duplicate_id_raises(tmp_db_path):
    storage = SQLiteStorage(tmp_db_path)
    metric = _make_metric()
    storage.save_metric(metric)
    with pytest.raises(sqlite3.IntegrityError):
        storage.save_metric(metric)


# ---------------------------------------------------------------------------
# Time-range queries (inclusive bounds)
# ---------------------------------------------------------------------------


def _metric_at(storage, service, name, when, value, source="sim"):
    m = Metric(
        timestamp=when,
        service=service,
        environment=Environment.PRODUCTION,
        metric_name=name,
        value=value,
        unit="percent",
        source=source,
        host_id=None,
    )
    storage.save_metric(m)
    return m


def test_time_range_inclusive_both_ends(tmp_db_path):
    storage = SQLiteStorage(tmp_db_path)
    base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    _metric_at(storage, "svc", "m", base, 1.0)
    _metric_at(storage, "svc", "m", base + timedelta(minutes=1), 2.0)
    _metric_at(storage, "svc", "m", base + timedelta(minutes=2), 3.0)

    # Inclusive on both ends: exactly the middle point matches.
    middle = storage.get_metrics(
        service="svc",
        metric_name="m",
        start_time=base + timedelta(minutes=1),
        end_time=base + timedelta(minutes=1),
    )
    assert len(middle) == 1
    assert middle[0].value == 2.0

    # End exclusive of the boundary (start == end + 1s) -> empty.
    empty = storage.get_metrics(
        service="svc",
        metric_name="m",
        start_time=base + timedelta(minutes=1),
        end_time=base + timedelta(minutes=1) - timedelta(seconds=1),
    )
    assert empty == []


def test_time_range_filters_by_service_and_name(tmp_db_path):
    storage = SQLiteStorage(tmp_db_path)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _metric_at(storage, "svc-a", "cpu", base, 1.0)
    _metric_at(storage, "svc-b", "cpu", base, 2.0)
    _metric_at(storage, "svc-a", "mem", base, 3.0)

    results = storage.get_metrics(service="svc-a", metric_name="cpu")
    assert len(results) == 1
    assert results[0].value == 1.0


# ---------------------------------------------------------------------------
# Logs, events, deployments
# ---------------------------------------------------------------------------


def test_log_round_trip(tmp_db_path):
    storage = SQLiteStorage(tmp_db_path)
    log = Log(
        timestamp=datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
        service="svc",
        environment=Environment.DEVELOPMENT,
        level=LogLevel.ERROR,
        message="boom",
        metadata={"request_id": "r1"},
    )
    stored = storage.save_log(log)
    assert stored.id == log.id
    fetched = storage.get_logs(service="svc", level="ERROR")
    assert len(fetched) == 1
    assert fetched[0].message == "boom"
    assert fetched[0].metadata == {"request_id": "r1"}


def test_event_round_trip(tmp_db_path):
    storage = SQLiteStorage(tmp_db_path)
    event = Event(
        timestamp=datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
        service="svc",
        environment=Environment.PRODUCTION,
        event_type=EventType.RESTART,
        description="restarted",
        metadata={"pod": "p1"},
    )
    storage.save_event(event)
    fetched = storage.get_events(service="svc", event_type="restart")
    assert len(fetched) == 1
    assert fetched[0].description == "restarted"


def test_deployment_round_trip(tmp_db_path):
    storage = SQLiteStorage(tmp_db_path)
    deployment = Deployment(
        timestamp=datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
        service="svc",
        environment=Environment.PRODUCTION,
        version="1.2.3",
        status=DeploymentStatus.SUCCESS,
        metadata={"commit": "abc"},
    )
    storage.save_deployment(deployment)
    fetched = storage.get_deployments(service="svc", status="success")
    assert len(fetched) == 1
    assert fetched[0].version == "1.2.3"


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------


def test_incident_round_trip(tmp_db_path):
    storage = SQLiteStorage(tmp_db_path)
    incident = Incident(
        title="latency spike",
        start_time=datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 1, 15, 10, 5, 0, tzinfo=timezone.utc),
        severity=IncidentSeverity.HIGH,
        status=IncidentStatus.RESOLVED,
        scenario="database_latency",
        affected_services=["api", "db"],
    )
    stored = storage.save_incident(incident)
    assert stored.id == incident.id
    fetched = storage.get_incidents(
        severity="high", status="resolved", scenario="database_latency"
    )
    assert len(fetched) == 1
    assert fetched[0].affected_services == ["api", "db"]
    assert fetched[0].end_time is not None


def test_incident_get_missing_returns_none(tmp_db_path):
    assert SQLiteStorage(tmp_db_path).get_incidents() == []


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


def test_evidence_round_trip(tmp_db_path):
    storage = SQLiteStorage(tmp_db_path)
    source_id = uuid4()
    target_id = uuid4()
    evidence = Evidence(
        source_id=source_id,
        source_type=EvidenceType.DEPLOYMENT,
        target_id=target_id,
        target_type=EvidenceType.INCIDENT,
        relationship_type="temporal",
        timestamp=datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
        strength=0.92,
        explanation="supports",
    )
    stored = storage.save_evidence(evidence)
    assert stored.id == evidence.id
    fetched = storage.get_evidence(
        source_id=str(source_id),
        target_id=str(target_id),
        relationship_type="temporal",
    )
    assert len(fetched) == 1
    assert fetched[0].strength == 0.92


# ---------------------------------------------------------------------------
# Persistence across re-open (restart)
# ---------------------------------------------------------------------------


def test_persistence_across_reopen(tmp_db_path):
    first = SQLiteStorage(tmp_db_path)
    service = Service(
        name="persist-svc",
        environment=Environment.DEVELOPMENT,
        version="1.0.0",
    )
    first.save_service(service)
    metric = _make_metric(source="windows_local", host_id="host-1")
    first.save_metric(metric)
    sid = str(service.id)

    # Close the first instance (connections are per-operation; nothing held).
    del first

    # Re-open against the same file: data survives.
    second = SQLiteStorage(tmp_db_path)
    assert second.get_service(sid) is not None
    metrics = second.get_metrics(service="test-service")
    assert len(metrics) == 1
    assert metrics[0].source == "windows_local"
    assert metrics[0].host_id == "host-1"


def test_counts_across_reopen(tmp_db_path):
    storage = SQLiteStorage(tmp_db_path)
    storage.save_metric(_make_metric())
    storage.save_log(
        Log(
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            service="s",
            environment=Environment.DEVELOPMENT,
            level=LogLevel.INFO,
            message="hi",
            metadata={},
        )
    )
    assert storage._counts()["metrics"] == 1
    assert storage._counts()["logs"] == 1

    again = SQLiteStorage(tmp_db_path)
    assert again._counts()["metrics"] == 1
    assert again._counts()["logs"] == 1


# ---------------------------------------------------------------------------
# Concurrent access
# ---------------------------------------------------------------------------


def test_concurrent_writes_from_multiple_threads(tmp_db_path):
    """Repeated concurrent writes must all survive with no lost records.

    The collector publishes to SQLite from a background thread while other code
    reads; this exercises that path with shared file access.
    """
    storage = SQLiteStorage(tmp_db_path)
    errors: list[Exception] = []

    def worker(index: int) -> None:
        try:
            for batch in range(25):
                metrics = [
                    _make_metric(
                        value=float(index * 100 + batch * 5 + offset),
                        name=f"cpu_percent-{index}",
                    )
                    for offset in range(5)
                ]
                storage.save_metrics(metrics)
        except Exception as exc:  # surface, do not swallow
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, errors
    persisted = storage.get_metrics()
    assert len(persisted) == 8 * 25 * 5
    # Every written value must be present exactly once.
    assert {m.value for m in persisted} == {
        float(index * 100 + batch * 5 + offset)
        for index in range(8)
        for batch in range(25)
        for offset in range(5)
    }


def test_concurrent_reads_while_writes_are_in_progress(tmp_db_path):
    """Reads may run while writer threads are publishing; neither fails.

    The UI reads storage while the collector writes from a background
    thread. WAL mode allows this, and the write path must not block reads
    into errors.
    """
    storage = SQLiteStorage(tmp_db_path)
    errors: list[Exception] = []
    reader_count = 4
    writer_count = 4
    batches_per_writer = 10
    metrics_per_batch = 5
    barrier = threading.Barrier(reader_count + writer_count)
    # The exact set of records the writers commit; any row a reader observes
    # mid-write must be one of these, fully formed.
    expected_values = {
        float(index * 1000 + batch * 10 + offset)
        for index in range(writer_count)
        for batch in range(batches_per_writer)
        for offset in range(metrics_per_batch)
    }

    def writer(index: int) -> None:
        try:
            barrier.wait()
            for batch in range(batches_per_writer):
                storage.save_metrics(
                    [
                        _make_metric(
                            value=float(index * 1000 + batch * 10 + offset),
                            name=f"writer-{index}",
                        )
                        for offset in range(metrics_per_batch)
                    ]
                )
        except Exception as exc:  # surface, do not swallow
            errors.append(exc)

    def reader() -> None:
        previous_count = 0
        try:
            barrier.wait()
            for _ in range(10):
                # Writers only insert, so each successive read must observe a
                # committed count at least as large as the previous one.
                current = storage._counts()["metrics"]
                assert current >= previous_count, (previous_count, current)
                previous_count = current
                # Every row observed mid-write must be a fully committed
                # record with the exact shape the writers produce; a torn or
                # partial row would break one of these invariants.
                for row in storage.get_metrics():
                    writer_index = int(row.value) // 1000
                    assert row.service == "test-service"
                    assert row.unit == "percent"
                    assert row.metric_name == f"writer-{writer_index}"
                    assert row.value in expected_values
        except Exception as exc:  # surface, do not swallow
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(index,)) for index in range(writer_count)]
    threads += [threading.Thread(target=reader) for _ in range(reader_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, errors
    expected = writer_count * batches_per_writer * metrics_per_batch
    assert len(storage.get_metrics()) == expected


def test_failed_write_rolls_back_completely_and_releases_lock(tmp_db_path):
    """A failed batch rolls back atomically and later writes still succeed.

    Error propagation must survive the concurrency fix: constraint errors
    surface unchanged (no retry, no swallowing), partial batches leave no
    rows behind, and the write lock is released for subsequent transactions.
    """
    storage = SQLiteStorage(tmp_db_path)
    storage.save_metrics([_make_metric(value=1.0, name="kept")])

    duplicate = _make_metric(value=2.0, name="dup")
    storage.save_metric(duplicate)

    with pytest.raises(sqlite3.IntegrityError):
        storage.save_metrics(
            [
                _make_metric(value=3.0, name="before"),
                duplicate,  # duplicate primary key aborts the batch
                _make_metric(value=4.0, name="after"),
            ]
        )

    # Nothing from the failed batch persisted; prior rows are untouched.
    assert {m.value for m in storage.get_metrics()} == {1.0, 2.0}

    # The write lock was released: subsequent transactions still work.
    storage.save_metrics([_make_metric(value=5.0, name="later")])
    assert {m.value for m in storage.get_metrics()} == {1.0, 2.0, 5.0}


def test_wal_journal_mode_is_enabled_and_persistent(tmp_db_path):
    """Connections run in WAL mode, confirmed once per open database."""
    storage = SQLiteStorage(tmp_db_path)
    storage.save_metric(_make_metric())
    mode = storage._op(
        lambda conn: conn.execute("PRAGMA journal_mode").fetchone()[0]
    )
    assert str(mode).lower() == "wal"

    # A second instance against the same file inherits the persisted WAL mode.
    again = SQLiteStorage(tmp_db_path)
    mode2 = again._op(
        lambda conn: conn.execute("PRAGMA journal_mode").fetchone()[0]
    )
    assert str(mode2).lower() == "wal"


# ---------------------------------------------------------------------------
# Health reporting
# ---------------------------------------------------------------------------


def test_health_reports_sqlite_and_path(tmp_db_path):
    storage = SQLiteStorage(tmp_db_path)
    health = storage.health()
    assert health["operational"] is True
    assert health["error"] is None
    assert health["backend"] == "SQLiteStorage"
    assert health["database_path"] == str(tmp_db_path)
    assert health["counts"]["metrics"] == 0


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------


def test_create_sqlite_application_without_supabase_credentials(
    fresh_config, tmp_path, monkeypatch
):
    db_path = tmp_path / "sel.db"
    monkeypatch.setenv("RESON_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("RESON_SQLITE_PATH", str(db_path))
    forbid_supabase_client(monkeypatch)

    application = create_application()
    assert isinstance(application, RESONApplication)
    assert application.config.storage_backend == "sqlite"
    assert isinstance(application.storage, SQLiteStorage)
    assert application.config.supabase is None
    assert isinstance(application.ingestion, TelemetryIngestionService)
    assert application.ingestion._storage is application.storage


def test_create_sqlite_application_explicit_argument(fresh_config, tmp_path):
    db_path = tmp_path / "expl.db"
    monkeypatch = fresh_config
    monkeypatch.setenv("RESON_SQLITE_PATH", str(db_path))
    monkeypatch.delenv("RESON_STORAGE_BACKEND", raising=False)
    forbid_supabase_client(monkeypatch)

    application = create_application("sqlite")
    assert application.config.storage_backend == "sqlite"
    assert isinstance(application.storage, SQLiteStorage)
    assert application.config.supabase is None


def test_sqlite_backend_requires_no_supabase_credentials(
    fresh_config, tmp_path, monkeypatch
):
    monkeypatch.setenv("RESON_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("RESON_SQLITE_PATH", str(tmp_path / "n.db"))
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
    forbid_supabase_client(monkeypatch)

    # Local SQLite loads and constructs without any Supabase configuration.
    application = create_application()
    assert application.config.storage_backend == "sqlite"
    assert application.config.supabase is None


def test_supabase_backend_without_credentials_fails_without_fallback(
    fresh_config,
):
    monkeypatch = fresh_config
    monkeypatch.setenv("RESON_STORAGE_BACKEND", "supabase")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="SUPABASE_URL"):
        create_application()


# ---------------------------------------------------------------------------
# Switching backends does not migrate history
# ---------------------------------------------------------------------------


def test_switching_backends_does_not_migrate_history(fresh_config, tmp_path):
    sqlite_db = tmp_path / "switch.db"
    monkeypatch = fresh_config
    monkeypatch.setenv("RESON_SQLITE_PATH", str(sqlite_db))
    monkeypatch.delenv("RESON_STORAGE_BACKEND", raising=False)
    forbid_supabase_client(monkeypatch)

    # Memory backend: save an incident.
    memory_app = create_application("memory")
    memory_incident = Incident(
        title="mem incident",
        start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end_time=None,
        severity=IncidentSeverity.HIGH,
        status=IncidentStatus.OPEN,
        scenario="s",
        affected_services=[],
    )
    memory_app.storage.save_incident(memory_incident)
    assert len(memory_app.storage.get_incidents()) == 1

    # Switch to SQLite: the SQLite backend starts empty (no migration).
    sqlite_app = create_application("sqlite")
    assert sqlite_app.config.storage_backend == "sqlite"
    assert sqlite_app.storage.get_incidents() == []
    assert len(sqlite_app.storage.get_incidents()) != len(
        memory_app.storage.get_incidents()
    )

    # Memory still has its incident; SQLite has none (independent histories).
    assert len(memory_app.storage.get_incidents()) == 1


# ---------------------------------------------------------------------------
# Historical comparison using stored SQLite data
# ---------------------------------------------------------------------------


def test_historical_comparison_with_sqlite_stored_data(tmp_db_path):
    from src.intelligence.historical_comparison import compare_periods

    storage = SQLiteStorage(tmp_db_path)
    host_id = "compare-host"
    base = datetime(2026, 1, 2, 12, 0, 0, tzinfo=timezone.utc)
    yesterday = base - timedelta(days=1)

    # Historical window: five cpu samples yesterday.
    for index, value in enumerate([40.0, 41.0, 42.0, 41.0, 40.0]):
        storage.save_metric(
            Metric(
                timestamp=yesterday + timedelta(minutes=index * 5),
                service=host_id,
                environment=Environment.PRODUCTION,
                metric_name="cpu_percent",
                value=value,
                unit="percent",
                source="windows_local",
                host_id=host_id,
            )
        )
    # Current window: one anomalous sample today.
    storage.save_metric(
        Metric(
            timestamp=base,
            service=host_id,
            environment=Environment.PRODUCTION,
            metric_name="cpu_percent",
            value=90.0,
            unit="percent",
            source="windows_local",
            host_id=host_id,
        )
    )

    result = compare_periods(
        storage,
        host_id=host_id,
        source="windows_local",
        current_start=base,
        current_end=base + timedelta(microseconds=1),
        historical_start=yesterday,
        historical_end=base,
    )

    assert result.sufficient is True
    assert any(d.is_anomalous for d in result.deviations)
    assert result.source == "windows_local"


def test_historical_comparison_no_history_reported_not_fabricated(tmp_db_path):
    from src.intelligence.historical_comparison import compare_periods

    storage = SQLiteStorage(tmp_db_path)
    result = compare_periods(
        storage,
        host_id="ghost",
        source="windows_local",
        current_start=datetime(2026, 1, 2, 12, 0, 0, tzinfo=timezone.utc),
        current_end=datetime(2026, 1, 2, 12, 0, 0, tzinfo=timezone.utc)
        + timedelta(microseconds=1),
        historical_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        historical_end=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    assert result.sufficient is False
    assert result.analysis is None
    assert any("no historical" in reason.lower() for reason in result.reasons)


# ---------------------------------------------------------------------------
# Collector ingestion into SQLite
# ---------------------------------------------------------------------------


def test_collector_ingestion_into_sqlite_round_trip(tmp_db_path):
    storage = SQLiteStorage(tmp_db_path)
    ingestion = TelemetryIngestionService(storage)

    now = datetime(2026, 1, 15, 9, 30, 0, tzinfo=timezone.utc)
    host = HostMeasurement(
        timestamp=now,
        host_id="test-host",
        source=SOURCE_WINDOWS_LOCAL,
        cpu_percent=42.5,
        memory_percent=60.0,
        memory_used_bytes=8_000_000_000,
        memory_total_bytes=16_000_000_000,
        ok=True,
    )
    processes = (
        ProcessMeasurement(
            timestamp=now,
            host_id="test-host",
            source=SOURCE_WINDOWS_LOCAL,
            pid=101,
            name="svchost.exe",
            cpu_percent=5.5,
            memory_rss_bytes=42_000_000,
            status="running",
            ok=True,
        ),
    )

    records = observations_to_metrics(host, processes)
    result = ingestion.ingest(records)
    assert result.metrics == 6  # 4 host + 2 process

    host_metrics = storage.get_metrics(service="test-host")
    process_metrics = storage.get_metrics(service="test-host:101")
    assert len(host_metrics) == 4
    assert len(process_metrics) == 2
    assert all(m.source == SOURCE_WINDOWS_LOCAL for m in [*host_metrics, *process_metrics])
    assert all(m.host_id == "test-host" for m in [*host_metrics, *process_metrics])


def test_collector_ingestion_omits_unavailable_measurements(tmp_db_path):
    storage = SQLiteStorage(tmp_db_path)
    ingestion = TelemetryIngestionService(storage)

    now = datetime(2026, 1, 15, 9, 30, 0, tzinfo=timezone.utc)
    host = HostMeasurement(
        timestamp=now,
        host_id="test-host",
        source=SOURCE_WINDOWS_LOCAL,
        cpu_percent=None,
        memory_percent=None,
        memory_used_bytes=None,
        memory_total_bytes=None,
        ok=False,
        error="wmi down",
    )

    records = observations_to_metrics(host, ())
    assert records == []
    ingestion.ingest(records)
    assert storage.get_metrics(service="test-host") == []


# ---------------------------------------------------------------------------
# Malformed / edge input behavior
# ---------------------------------------------------------------------------


def test_get_metrics_with_no_filters_on_empty_db(tmp_db_path):
    assert SQLiteStorage(tmp_db_path).get_metrics() == []


def test_get_incidents_with_no_filters_on_empty_db(tmp_db_path):
    assert SQLiteStorage(tmp_db_path).get_incidents() == []


def test_service_with_empty_description(tmp_db_path):
    storage = SQLiteStorage(tmp_db_path)
    service = Service(
        name="no-desc",
        environment=Environment.STAGING,
        version="0.1.0",
        description=None,
    )
    stored = storage.save_service(service)
    assert stored.description is None
    fetched = storage.get_service(str(service.id))
    assert fetched is not None
    assert fetched.description is None


# ---------------------------------------------------------------------------
# Source attribution (Phase 1)
# ---------------------------------------------------------------------------


def _log_at(storage, when, source="sim", host_id="host-1"):
    log = Log(
        timestamp=when,
        service="svc",
        environment=Environment.DEVELOPMENT,
        level=LogLevel.INFO,
        message="msg",
        source=source,
        host_id=host_id,
    )
    storage.save_log(log)
    return log


def test_log_event_deployment_round_trip_preserves_source_and_host_id(
    tmp_db_path,
):
    storage = SQLiteStorage(tmp_db_path)
    when = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
    storage.save_log(
        Log(
            timestamp=when,
            service="svc",
            environment=Environment.DEVELOPMENT,
            level=LogLevel.INFO,
            message="msg",
            source=SOURCE_WINDOWS_LOCAL,
            host_id="host-1",
        )
    )
    storage.save_event(
        Event(
            timestamp=when,
            service="svc",
            environment=Environment.DEVELOPMENT,
            event_type=EventType.RESTART,
            description="restarted",
            source=SOURCE_WINDOWS_LOCAL,
            host_id="host-1",
        )
    )
    storage.save_deployment(
        Deployment(
            timestamp=when,
            service="svc",
            environment=Environment.DEVELOPMENT,
            version="1.0",
            status=DeploymentStatus.SUCCESS,
            source=SOURCE_SIMULATION,
            host_id="host-1",
        )
    )

    logs = storage.get_logs(source=SOURCE_WINDOWS_LOCAL)
    events = storage.get_events(source=SOURCE_WINDOWS_LOCAL)
    deps = storage.get_deployments(source=SOURCE_SIMULATION)
    assert len(logs) == 1 and logs[0].host_id == "host-1"
    assert len(events) == 1 and events[0].host_id == "host-1"
    assert len(deps) == 1 and deps[0].host_id == "host-1"

    # Filtering by a different source returns nothing.
    assert storage.get_logs(source="linux_local") == []
    assert storage.get_events(source="linux_local") == []
    assert storage.get_deployments(source="linux_local") == []


def test_source_filter_combines_with_time_range(tmp_db_path):
    storage = SQLiteStorage(tmp_db_path)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _log_at(storage, base, source=SOURCE_WINDOWS_LOCAL)
    _log_at(storage, base + timedelta(days=1), source=SOURCE_SIMULATION)

    results = storage.get_logs(
        source=SOURCE_WINDOWS_LOCAL,
        start_time=base,
        end_time=base + timedelta(days=1),
    )
    assert len(results) == 1
    assert results[0].source == SOURCE_WINDOWS_LOCAL


def test_legacy_record_without_source_is_preserved_as_none(tmp_db_path):
    """Legacy records carry no source; they must stay None/unknown, never be
    relabeled as real telemetry."""
    storage = SQLiteStorage(tmp_db_path)
    when = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
    storage.save_log(
        Log(
            timestamp=when,
            service="svc",
            environment=Environment.DEVELOPMENT,
            level=LogLevel.INFO,
            message="legacy",
        )
    )
    fetched = storage.get_logs(service="svc")
    assert len(fetched) == 1
    assert fetched[0].source is None
    assert fetched[0].host_id is None


def test_migration_adds_source_host_id_to_legacy_telemetry_tables(tmp_db_path):
    """A pre-existing database at schema version 1 (logs/events/deployments
    without source/host_id columns) is migrated to version 2 non-destructively.
    """
    conn = sqlite3.connect(str(tmp_db_path))
    conn.execute("PRAGMA user_version = 0")
    conn.execute(
        "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, "
        "name TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO schema_migrations (version, name, applied_at) "
        "VALUES (1, 'initial_schema', '2026-01-01T00:00:00+00:00')"
    )
    conn.execute(
        "CREATE TABLE logs (id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, "
        "service TEXT NOT NULL, environment TEXT NOT NULL, level TEXT NOT NULL, "
        "message TEXT NOT NULL, metadata TEXT)"
    )
    conn.execute(
        "CREATE TABLE events (id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, "
        "service TEXT NOT NULL, environment TEXT NOT NULL, event_type TEXT NOT NULL, "
        "description TEXT NOT NULL, metadata TEXT)"
    )
    conn.execute(
        "CREATE TABLE deployments (id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, "
        "service TEXT NOT NULL, environment TEXT NOT NULL, version TEXT NOT NULL, "
        "status TEXT NOT NULL, metadata TEXT)"
    )
    conn.execute(
        "CREATE TABLE metrics (id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, "
        "service TEXT NOT NULL, environment TEXT NOT NULL, metric_name TEXT NOT NULL, "
        "value REAL NOT NULL, unit TEXT NOT NULL, source TEXT, host_id TEXT)"
    )
    conn.commit()
    conn.close()

    storage = SQLiteStorage(tmp_db_path)
    # Trigger schema setup + migration.
    storage.health()

    for table in ("logs", "events", "deployments"):
        cols = storage._op(
            lambda conn, t=table: [r[1] for r in conn.execute(
                f"PRAGMA table_info({t})"
            ).fetchall()]
        )
        assert "source" in cols, table
        assert "host_id" in cols, table

    applied = storage._op(
        lambda conn: [r[0] for r in conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()]
    )
    assert 2 in applied, applied

    # Re-running the migration is a no-op (idempotent).
    storage2 = SQLiteStorage(tmp_db_path)
    storage2.health()
    applied2 = storage2._op(
        lambda conn: [r[0] for r in conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()]
    )
    assert applied2 == applied


def test_naive_timestamp_is_normalized_to_utc_on_round_trip(tmp_db_path):
    """Naive (legacy) timestamps are stored as UTC and read back as aware
    UTC datetimes; they are never silently treated as wall-clock local time."""
    storage = SQLiteStorage(tmp_db_path)
    naive = datetime(2026, 6, 15, 10, 0, 0)  # no tzinfo
    storage.save_metric(
        Metric(
            timestamp=naive,
            service="svc",
            environment=Environment.PRODUCTION,
            metric_name="cpu_percent",
            value=42.0,
            unit="percent",
            source=SOURCE_WINDOWS_LOCAL,
            host_id="host-1",
        )
    )
    fetched = storage.get_metrics(service="svc")
    assert len(fetched) == 1
    assert fetched[0].timestamp.tzinfo is not None
    assert fetched[0].timestamp.utcoffset() == timedelta(hours=0)
    # The wall-clock value is preserved as a UTC instant.
    assert fetched[0].timestamp.hour == 10


def test_demo_records_are_tagged_simulation_and_never_live(tmp_db_path):
    """Demonstration data written through the dataset service is tagged
    source=simulation and is distinguishable from live host telemetry."""
    from src.data.dataset_service import HistoricalDatasetService
    from src.data.dataset import HistoricalDatasetBuilder
    from src.data.ingestion import TelemetryIngestionService

    storage = SQLiteStorage(tmp_db_path)
    ingestion = TelemetryIngestionService(storage)
    service = HistoricalDatasetService(
        HistoricalDatasetBuilder(), ingestion
    )
    service.build_and_persist(start_time=datetime(2026, 1, 1))

    metrics = storage.get_metrics()
    assert metrics, "demo metrics should be persisted"
    assert all(m.source == SOURCE_SIMULATION for m in metrics)
    # A source-aware read for real host telemetry finds no demo records.
    assert storage.get_metrics(source=SOURCE_WINDOWS_LOCAL) == []


# ---------------------------------------------------------------------------
# Dashboard-optimized reads
# ---------------------------------------------------------------------------


def test_get_latest_metrics_returns_the_newest_per_metric_name(tmp_db_path):
    storage = SQLiteStorage(tmp_db_path)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    storage.save_metric(
        Metric(
            timestamp=base,
            service="svc",
            environment=Environment.PRODUCTION,
            metric_name="cpu_percent",
            value=10.0,
            unit="percent",
            source=SOURCE_WINDOWS_LOCAL,
            host_id="host-1",
        )
    )
    storage.save_metric(
        Metric(
            timestamp=base + timedelta(minutes=5),
            service="svc",
            environment=Environment.PRODUCTION,
            metric_name="cpu_percent",
            value=20.0,
            unit="percent",
            source=SOURCE_WINDOWS_LOCAL,
            host_id="host-1",
        )
    )
    storage.save_metric(
        Metric(
            timestamp=base + timedelta(minutes=1),
            service="svc",
            environment=Environment.PRODUCTION,
            metric_name="memory_percent",
            value=30.0,
            unit="percent",
            source=SOURCE_WINDOWS_LOCAL,
            host_id="host-1",
        )
    )

    latest = {m.metric_name: m for m in storage.get_latest_metrics()}
    assert set(latest) == {"cpu_percent", "memory_percent"}
    assert latest["cpu_percent"].value == 20.0
    assert latest["memory_percent"].value == 30.0


def test_get_latest_metrics_respects_source_and_host(tmp_db_path):
    storage = SQLiteStorage(tmp_db_path)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    storage.save_metric(
        Metric(
            timestamp=now,
            service="svc",
            environment=Environment.PRODUCTION,
            metric_name="cpu_percent",
            value=1.0,
            unit="percent",
            source=SOURCE_SIMULATION,
            host_id="sim-host",
        )
    )
    storage.save_metric(
        Metric(
            timestamp=now,
            service="svc",
            environment=Environment.PRODUCTION,
            metric_name="cpu_percent",
            value=2.0,
            unit="percent",
            source=SOURCE_WINDOWS_LOCAL,
            host_id="live-host",
        )
    )
    latest = storage.get_latest_metrics(source=SOURCE_WINDOWS_LOCAL)
    assert len(latest) == 1
    assert latest[0].value == 2.0


def test_count_records_filters_by_source(tmp_db_path):
    storage = SQLiteStorage(tmp_db_path)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    storage.save_metric(
        Metric(
            timestamp=now,
            service="svc",
            environment=Environment.PRODUCTION,
            metric_name="cpu_percent",
            value=1.0,
            unit="percent",
            source=SOURCE_WINDOWS_LOCAL,
            host_id="host-1",
        )
    )
    storage.save_metric(
        Metric(
            timestamp=now,
            service="svc",
            environment=Environment.PRODUCTION,
            metric_name="cpu_percent",
            value=2.0,
            unit="percent",
            source=SOURCE_SIMULATION,
            host_id="sim-host",
        )
    )
    live = storage.count_records(source=SOURCE_WINDOWS_LOCAL)
    assert live["metrics"] == 1
    assert storage.count_records()["metrics"] == 2


def test_list_sources_and_hosts(tmp_db_path):
    storage = SQLiteStorage(tmp_db_path)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for source, host in (
        (SOURCE_WINDOWS_LOCAL, "host-1"),
        (SOURCE_SIMULATION, "sim-host"),
    ):
        storage.save_metric(
            Metric(
                timestamp=now,
                service="svc",
                environment=Environment.PRODUCTION,
                metric_name="cpu_percent",
                value=1.0,
                unit="percent",
                source=source,
                host_id=host,
            )
        )
    assert storage.list_sources() == [SOURCE_SIMULATION, SOURCE_WINDOWS_LOCAL]
    assert storage.list_hosts(source=SOURCE_WINDOWS_LOCAL) == ["host-1"]


def test_latest_observation_returns_the_newest_timestamp(tmp_db_path):
    storage = SQLiteStorage(tmp_db_path)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for offset in (0, 5, 2):
        storage.save_metric(
            Metric(
                timestamp=base + timedelta(minutes=offset),
                service="svc",
                environment=Environment.PRODUCTION,
                metric_name="cpu_percent",
                value=1.0,
                unit="percent",
                source=SOURCE_WINDOWS_LOCAL,
                host_id="host-1",
            )
        )
    latest = storage.latest_observation(source=SOURCE_WINDOWS_LOCAL)
    assert latest == base + timedelta(minutes=5)


def test_unknown_source_filter_matches_legacy_null_sources(tmp_db_path):
    """Legacy records with a NULL source are classified as 'unknown' and
    must be reachable through the 'unknown' source filter."""
    storage = SQLiteStorage(tmp_db_path)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    storage.save_metric(
        Metric(
            timestamp=now,
            service="legacy-host",
            environment=Environment.PRODUCTION,
            metric_name="cpu_percent",
            value=1.0,
            unit="percent",
            host_id="legacy-host",
        )
    )
    storage.save_metric(
        Metric(
            timestamp=now,
            service="live-host",
            environment=Environment.PRODUCTION,
            metric_name="cpu_percent",
            value=2.0,
            unit="percent",
            source=SOURCE_WINDOWS_LOCAL,
            host_id="live-host",
        )
    )

    unknown = storage.get_metrics(source="unknown")
    assert len(unknown) == 1
    assert unknown[0].source is None
    assert storage.count_records(source="unknown")["metrics"] == 1
    assert storage.list_hosts(source="unknown") == ["legacy-host"]
    assert storage.latest_observation(source="unknown") is not None
    # A real source never matches the unknown bucket.
    assert storage.get_metrics(source=SOURCE_WINDOWS_LOCAL)[0].host_id == (
        "live-host"
    )
