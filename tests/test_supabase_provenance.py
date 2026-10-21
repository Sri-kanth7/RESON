"""Tests for the Supabase storage backend's telemetry provenance.

The Supabase backend is the production backend, and its project tables are
created outside RESON. These tests use a fake PostgREST-style client so the
behaviour can be verified offline, without credentials and without network.

They cover:
* the provenance columns are written and read back;
* source/host filters are applied to the query;
* a table without the provenance columns is handled honestly instead of
  silently mixing sources;
* a source filter against such a table is an error, never a no-op.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.data.schemas import (
    SOURCE_LINUX_LOCAL,
    SOURCE_SIMULATION,
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
    RelationshipType,
)
from src.data.supabase_storage import SupabaseStorage

START = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)


class _MissingColumnError(Exception):
    """Stands in for the PostgREST "column not found" error."""

    code = "PGRST204"
    message = (
        "Could not find the 'source' column of 'public.metrics' in the "
        "schema cache"
    )


class _FakeResponse:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class _FakeQuery:
    """Records the query the storage backend builds."""

    def __init__(self, client, table, columns):
        self._client = client
        self._table = table
        self._columns = columns
        self.filters: list[tuple[str, object]] = []
        self._order: tuple[str, bool] | None = None
        self._limit: int | None = None
        self._count_exact = False
        self._pending: list | None = None

    # -- builder surface -------------------------------------------------
    def select(self, columns, count=None):
        self._columns = columns
        self._count_exact = count == "exact"
        return self

    def insert(self, payload):
        rows = payload if isinstance(payload, list) else [payload]
        self._pending = [dict(row) for row in rows]
        self._client.inserted.append((self._table, payload))
        return self

    def upsert(self, payload):
        rows = payload if isinstance(payload, list) else [payload]
        self._pending = [dict(row) for row in rows]
        self._client.inserted.append((self._table, payload))
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def gte(self, column, value):
        self.filters.append((column, value))
        return self

    def lte(self, column, value):
        self.filters.append((column, value))
        return self

    def order(self, column, desc=False):
        self._order = (column, desc)
        return self

    def limit(self, count):
        self._limit = count
        return self

    def execute(self):
        if self._pending is not None:
            rows = self._pending
            self._pending = None
            self._client.tables.setdefault(self._table, []).extend(rows)
            return _FakeResponse(rows, count=len(rows))

        requested = [
            column.strip()
            for column in self._columns.split(",")
            if column.strip() not in ("id", "*")
        ]
        if self._client.missing_columns:
            for column in requested:
                if column in self._client.missing_columns:
                    raise _MissingColumnError(self._client.missing_columns)
        rows = self._client.rows_for(self._table)
        for column, value in self.filters:
            rows = [row for row in rows if row.get(column) == value]
        if self._order is not None:
            column, desc = self._order
            rows = sorted(
                rows,
                key=lambda row: str(row.get(column)),
                reverse=desc,
            )
        if self._limit is not None:
            rows = rows[: self._limit]
        return _FakeResponse(rows, count=len(rows))


class _FakeClient:
    """A minimal in-memory stand-in for the Supabase client."""

    def __init__(self, tables=None, missing_columns=None):
        self.tables = tables or {}
        self.missing_columns = missing_columns or set()
        self.inserted: list[tuple[str, object]] = []
        self.queries: list[_FakeQuery] = []

    def table(self, name):
        query = _FakeQuery(self, name, "id")
        self.queries.append(query)
        return query

    def rows_for(self, table):
        return list(self.tables.get(table, []))


def _metric(source=None, host_id=None):
    return Metric(
        timestamp=START,
        service="api-service",
        environment=Environment.PRODUCTION,
        metric_name="latency_ms",
        value=12.0,
        unit="ms",
        source=source,
        host_id=host_id,
    )


def _row(**overrides):
    row = {
        "id": "11111111-1111-1111-1111-111111111111",
        "timestamp": START.isoformat(),
        "service": "api-service",
        "environment": "production",
        "metric_name": "latency_ms",
        "value": 12.0,
        "unit": "ms",
        "source": SOURCE_LINUX_LOCAL,
        "host_id": "host-1",
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# The defect that was fixed
# ---------------------------------------------------------------------------


def test_count_records_no_longer_raises_on_the_supabase_backend():
    """``count_records`` must work on the Supabase backend.

    The generic implementation calls ``get_metrics(source=..., host_id=...)``.
    The Supabase backend did not accept those arguments, so every dashboard
    read against the production backend raised ``TypeError``.
    """
    client = _FakeClient(tables={"metrics": [_row()]})
    storage = SupabaseStorage(client)
    counts = storage.count_records(source=SOURCE_LINUX_LOCAL)
    assert counts["metrics"] == 1


def test_source_filter_is_applied_to_the_query():
    """A source filter becomes a query filter, not an ignored argument."""
    client = _FakeClient(tables={"metrics": [_row()]})
    storage = SupabaseStorage(client)
    storage.get_metrics(source=SOURCE_LINUX_LOCAL)
    query = client.queries[-1]
    assert ("source", SOURCE_LINUX_LOCAL) in query.filters


def test_list_sources_reads_the_stored_source():
    """The distinct sources are reported from the remote rows."""
    client = _FakeClient(
        tables={
            "metrics": [
                _row(source=SOURCE_LINUX_LOCAL),
                _row(source=SOURCE_SIMULATION),
            ]
        }
    )
    storage = SupabaseStorage(client)
    assert storage.list_sources() == [SOURCE_LINUX_LOCAL, SOURCE_SIMULATION]


def test_latest_observation_uses_the_newest_row():
    """The newest observation timestamp is returned, honouring the filter."""
    newer = _row(timestamp="2026-03-01T13:00:00+00:00")
    client = _FakeClient(tables={"metrics": [_row(), newer]})
    storage = SupabaseStorage(client)
    latest = storage.latest_observation(source=SOURCE_LINUX_LOCAL)
    assert latest == datetime(2026, 3, 1, 13, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def test_saved_metric_carries_its_provenance():
    """The provenance of a metric is written to the remote table."""
    client = _FakeClient(tables={"metrics": [_row()]})
    storage = SupabaseStorage(client)
    storage.save_metric(_metric(SOURCE_LINUX_LOCAL, "host-1"))
    table, payload = client.inserted[-1]
    assert table == "metrics"
    assert payload["source"] == SOURCE_LINUX_LOCAL
    assert payload["host_id"] == "host-1"


def test_saved_incident_carries_its_provenance():
    """An incident's provenance is written to the remote table."""
    incident = Incident(
        title="api-service degradation",
        start_time=START,
        severity=IncidentSeverity.HIGH,
        status=IncidentStatus.OPEN,
        source=SOURCE_LINUX_LOCAL,
        host_id="host-1",
    )
    client = _FakeClient(
        tables={
            "incidents": [
                {
                    "id": incident.id,
                    "title": incident.title,
                    "start_time": START.isoformat(),
                    "end_time": None,
                    "severity": "high",
                    "status": "open",
                    "scenario": None,
                    "affected_services": [],
                    "source": SOURCE_LINUX_LOCAL,
                    "host_id": "host-1",
                }
            ]
        }
    )
    storage = SupabaseStorage(client)
    stored = storage.save_incident(incident)
    _, payload = client.inserted[-1]
    assert payload["source"] == SOURCE_LINUX_LOCAL
    assert payload["host_id"] == "host-1"
    assert stored.source == SOURCE_LINUX_LOCAL


def test_saved_evidence_carries_its_provenance():
    """An evidence link's provenance is written to the remote table."""
    evidence = Evidence(
        source_id="22222222-2222-2222-2222-222222222222",
        source_type=EvidenceType.INCIDENT,
        target_id="33333333-3333-3333-3333-333333333333",
        target_type=EvidenceType.LOG,
        relationship_type=RelationshipType.CORRELATION,
        timestamp=START,
        strength=0.5,
        explanation="supporting evidence",
        source=SOURCE_SIMULATION,
        host_id="host-2",
    )
    client = _FakeClient(tables={"evidence": []})
    storage = SupabaseStorage(client)
    storage.save_evidence(evidence)
    _, payload = client.inserted[-1]
    assert payload["source"] == SOURCE_SIMULATION
    assert payload["host_id"] == "host-2"


# ---------------------------------------------------------------------------
# A remote schema without provenance columns
# ---------------------------------------------------------------------------


def test_missing_provenance_columns_are_reported_not_hidden():
    """A table without the columns is reported as unsupported."""
    client = _FakeClient(
        tables={"metrics": [_row()]},
        missing_columns={"source", "host_id"},
    )
    storage = SupabaseStorage(client)

    capability = storage.provenance_capability()
    assert capability["supported"] is False
    assert capability["notes"], "the limitation must be explained"


def test_source_filter_against_a_table_without_the_column_is_an_error():
    """Ignoring the filter would mix sources, so it must fail loudly."""
    client = _FakeClient(
        tables={"metrics": [_row()]},
        missing_columns={"source", "host_id"},
    )
    storage = SupabaseStorage(client)

    with pytest.raises(RuntimeError) as excinfo:
        storage.get_metrics(source=SOURCE_LINUX_LOCAL)
    assert "source" in str(excinfo.value)


def test_records_without_provenance_are_not_treated_as_live():
    """A row without a source is read back with no provenance."""
    row = _row()
    row.pop("source")
    row.pop("host_id")
    client = _FakeClient(tables={"metrics": [row]})
    storage = SupabaseStorage(client)
    metrics = storage.get_metrics()
    assert len(metrics) == 1
    assert metrics[0].source is None
    assert metrics[0].host_id is None
