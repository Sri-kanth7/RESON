"""Tests for telemetry provenance on incidents and evidence.

Covers the complete provenance chain:

    Metric/Log/Event/Deployment -> Signal -> Evidence -> Incident -> storage

The chain must survive into every storage backend, and a source filter must
never return records from another source.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.data.memory_storage import InMemoryStorage
from src.data.schemas import (
    SOURCE_LINUX_LOCAL,
    SOURCE_SIMULATION,
    SOURCE_UNKNOWN,
    Environment,
    Evidence,
    EvidenceType,
    Incident,
    IncidentSeverity,
    IncidentStatus,
    Log,
    LogLevel,
    Metric,
    RelationshipType,
    classify_source,
)
from src.data.sqlite_storage import SQLiteStorage
from src.incidents.evidence import dominant_provenance, link_incident_signals
from src.incidents.models import Signal

START = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)

#: A short, deterministic simulation timeline. It is a complete run of the
#: same pipeline as the full default timeline, but small enough to keep the
#: test suite fast. The full timeline correlates every pair of signals and
#: takes far longer.
DEMO_SCENARIOS = (
    "normal",
    "normal",
    "normal",
    "error_spike",
    "normal",
)


def _metric(source: str | None, host_id: str | None = None) -> Metric:
    return Metric(
        timestamp=START,
        service="api-service",
        environment=Environment.PRODUCTION,
        metric_name="latency_ms",
        value=42.0,
        unit="ms",
        source=source,
        host_id=host_id,
    )


def _log(source: str | None, host_id: str | None = None) -> Log:
    return Log(
        timestamp=START,
        service="api-service",
        environment=Environment.PRODUCTION,
        level=LogLevel.ERROR,
        message="request failed",
        source=source,
        host_id=host_id,
    )


def _signal(
    source: str | None = None,
    host_id: str | None = None,
    signal_id=None,
) -> Signal:
    return Signal(
        source_id=signal_id or uuid4(),
        source_type=EvidenceType.LOG,
        timestamp=START,
        service="api-service",
        environment="production",
        description="request failed",
        strength=0.8,
        trigger=True,
        source_record_ids=[uuid4()],
        source=source,
        host_id=host_id,
    )


def _incident(source: str | None, host_id: str | None = None) -> Incident:
    return Incident(
        title="api-service degradation",
        start_time=START,
        end_time=START + timedelta(minutes=1),
        severity=IncidentSeverity.HIGH,
        status=IncidentStatus.OPEN,
        affected_services=["api-service"],
        source=source,
        host_id=host_id,
    )


def _evidence(source: str | None, host_id: str | None = None) -> Evidence:
    return Evidence(
        source_id=uuid4(),
        source_type=EvidenceType.INCIDENT,
        target_id=uuid4(),
        target_type=EvidenceType.LOG,
        relationship_type=RelationshipType.CORRELATION,
        timestamp=START,
        strength=0.8,
        explanation="supporting evidence",
        source=source,
        host_id=host_id,
    )


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_incident_and_evidence_accept_provenance():
    """The contracts accept provenance and default it to None."""
    incident = _incident(None)
    assert incident.source is None
    assert incident.host_id is None

    evidence = _evidence(None)
    assert evidence.source is None
    assert evidence.host_id is None


def test_classify_source_treats_missing_incident_source_as_unknown():
    """A missing source is unknown, never inferred to be real."""
    assert classify_source(None) == SOURCE_UNKNOWN
    assert classify_source("not-a-source") == SOURCE_UNKNOWN


# ---------------------------------------------------------------------------
# Pipeline propagation
# ---------------------------------------------------------------------------


def test_evidence_chain_inherits_the_signal_provenance():
    """An evidence link keeps the attribution of the observation it points at."""
    signal = _signal(SOURCE_LINUX_LOCAL, "host-1")
    chain = link_incident_signals(uuid4(), [signal])
    assert len(chain) == 1
    assert chain[0].source == SOURCE_LINUX_LOCAL
    assert chain[0].host_id == "host-1"


def test_dominant_provenance_requires_agreement():
    """A cluster that mixes sources stays unattributed."""
    signals = [
        _signal(SOURCE_LINUX_LOCAL, "host-1"),
        _signal(SOURCE_SIMULATION, "host-1"),
    ]
    source, host = dominant_provenance(signals)
    assert source is None
    assert host == "host-1"


def test_dominant_provenance_of_a_single_source():
    """A unanimous cluster reports its source."""
    signals = [
        _signal(SOURCE_LINUX_LOCAL, "host-1"),
        _signal(SOURCE_LINUX_LOCAL, "host-1"),
    ]
    source, host = dominant_provenance(signals)
    assert source == SOURCE_LINUX_LOCAL
    assert host == "host-1"


def test_dominant_provenance_of_signals_without_provenance():
    """Signals without provenance produce no attribution."""
    source, host = dominant_provenance([_signal(None, None)])
    assert source is None
    assert host is None


def test_normalizer_preserves_metric_provenance():
    """Normalization keeps the source and host of every observation."""
    from src.preprocessing.normalizer import group_metrics

    series = group_metrics([_metric(SOURCE_LINUX_LOCAL, "host-1")])
    assert len(series) == 1
    observation = series[0].observations[0]
    assert observation.source == SOURCE_LINUX_LOCAL
    assert observation.host_id == "host-1"


def test_deviation_carries_the_observation_provenance():
    """A metric deviation keeps the provenance of its observation."""
    from src.intelligence.deviation import DeviationDetector
    from src.intelligence.models import BaselineKey
    from src.preprocessing.normalizer import MetricObservation

    observation = MetricObservation(
        record_id=uuid4(),
        timestamp=START,
        value=10.0,
        source=SOURCE_LINUX_LOCAL,
        host_id="host-1",
    )
    key = BaselineKey(
        service="api-service",
        environment="production",
        metric_name="latency_ms",
    )
    deviation = DeviationDetector().detect(key, observation, None)
    assert deviation.source == SOURCE_LINUX_LOCAL
    assert deviation.host_id == "host-1"


def test_signal_builder_uses_the_record_provenance():
    """Signals built from telemetry carry the record's source."""
    from src.incidents.correlation import SignalBuilder

    signals = SignalBuilder().build(logs=[_log(SOURCE_LINUX_LOCAL, "host-1")])
    assert signals
    assert signals[0].source == SOURCE_LINUX_LOCAL
    assert signals[0].host_id == "host-1"


# ---------------------------------------------------------------------------
# Storage round trips
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_incident_provenance_round_trips(backend, tmp_path):
    """An incident's source and host survive a write/read cycle."""
    storage = (
        InMemoryStorage()
        if backend == "memory"
        else SQLiteStorage(tmp_path / "provenance.db")
    )
    incident = _incident(SOURCE_LINUX_LOCAL, "host-1")
    storage.save_incident(incident)

    stored = storage.get_incidents()
    assert len(stored) == 1
    assert stored[0].source == SOURCE_LINUX_LOCAL
    assert stored[0].host_id == "host-1"


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_evidence_provenance_round_trips(backend, tmp_path):
    """An evidence record's source and host survive a write/read cycle."""
    storage = (
        InMemoryStorage()
        if backend == "memory"
        else SQLiteStorage(tmp_path / "provenance.db")
    )
    evidence = _evidence(SOURCE_LINUX_LOCAL, "host-1")
    storage.save_evidence(evidence)

    stored = storage.get_evidence()
    assert len(stored) == 1
    assert stored[0].source == SOURCE_LINUX_LOCAL
    assert stored[0].host_id == "host-1"


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_incidents_can_be_filtered_by_source(backend, tmp_path):
    """A source filter never returns an incident from another source."""
    storage = (
        InMemoryStorage()
        if backend == "memory"
        else SQLiteStorage(tmp_path / "provenance.db")
    )
    storage.save_incident(_incident(SOURCE_LINUX_LOCAL, "host-1"))
    storage.save_incident(_incident(SOURCE_SIMULATION, "host-2"))
    storage.save_incident(_incident(None, "host-3"))

    live = storage.get_incidents(source=SOURCE_LINUX_LOCAL)
    assert len(live) == 1
    assert live[0].source == SOURCE_LINUX_LOCAL

    demo = storage.get_incidents(source=SOURCE_SIMULATION)
    assert len(demo) == 1

    unknown = storage.get_incidents(source=SOURCE_UNKNOWN)
    assert len(unknown) == 1
    assert unknown[0].source is None

    assert len(storage.get_incidents()) == 3


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_evidence_can_be_filtered_by_source(backend, tmp_path):
    """A source filter never returns evidence from another source."""
    storage = (
        InMemoryStorage()
        if backend == "memory"
        else SQLiteStorage(tmp_path / "provenance.db")
    )
    storage.save_evidence(_evidence(SOURCE_LINUX_LOCAL, "host-1"))
    storage.save_evidence(_evidence(SOURCE_SIMULATION, "host-2"))

    live = storage.get_evidence(source=SOURCE_LINUX_LOCAL)
    assert len(live) == 1
    assert live[0].source == SOURCE_LINUX_LOCAL


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_legacy_incidents_without_provenance_read_back_as_unknown(
    backend, tmp_path
):
    """An incident stored before provenance existed is unknown, not live."""
    storage = (
        InMemoryStorage()
        if backend == "memory"
        else SQLiteStorage(tmp_path / "provenance.db")
    )
    storage.save_incident(_incident(None, None))

    stored = storage.get_incidents(source=SOURCE_UNKNOWN)
    assert len(stored) == 1
    assert classify_source(stored[0].source) == SOURCE_UNKNOWN


def test_sqlite_migration_preserves_legacy_incidents(tmp_path):
    """Migrating a legacy database keeps its incidents and adds provenance."""
    import sqlite3

    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, "
        "name TEXT, applied_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE incidents (id TEXT PRIMARY KEY, title TEXT NOT NULL, "
        "start_time TEXT NOT NULL, end_time TEXT, severity TEXT NOT NULL, "
        "status TEXT NOT NULL, scenario TEXT, affected_services TEXT)"
    )
    incident_id = str(uuid4())
    conn.execute(
        "INSERT INTO incidents (id, title, start_time, end_time, severity, "
        "status, scenario, affected_services) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            incident_id,
            "legacy incident",
            "2026-01-01T00:00:00+00:00",
            None,
            "high",
            "open",
            None,
            "[]",
        ),
    )
    conn.commit()
    conn.close()

    storage = SQLiteStorage(db_path)
    incidents = storage.get_incidents()
    assert len(incidents) == 1
    assert incidents[0].title == "legacy incident"
    assert incidents[0].source is None
    # The migrated row is filterable as unknown provenance.
    assert len(storage.get_incidents(source=SOURCE_UNKNOWN)) == 1


def test_demonstration_incidents_carry_the_simulation_source(tmp_path, monkeypatch):
    """The demonstration pipeline attributes its incidents to the simulation.

    The incidents must be derived from the *same* tagged records that were
    persisted, otherwise every incident would be stored without provenance
    and would be indistinguishable from data of unknown origin.
    """
    monkeypatch.setenv(
        "RESON_SQLITE_PATH", str(tmp_path / "demonstration.db")
    )
    from app.services.backend import build_application, run_demonstration

    application = build_application("sqlite")
    run_demonstration(application, scenarios=DEMO_SCENARIOS)

    incidents = application.storage.get_incidents()
    assert incidents, "the demonstration is expected to detect an incident"
    for incident in incidents:
        assert incident.source == SOURCE_SIMULATION

    # The source filter separates the demonstration from every other source.
    assert len(application.storage.get_incidents(source=SOURCE_SIMULATION)) == len(
        incidents
    )
    assert application.storage.get_incidents(source=SOURCE_LINUX_LOCAL) == []


def test_demonstration_evidence_is_attributable(tmp_path, monkeypatch):
    """Every persisted evidence link keeps the simulation provenance."""
    monkeypatch.setenv(
        "RESON_SQLITE_PATH", str(tmp_path / "demonstration.db")
    )
    from app.services.backend import build_application, run_demonstration

    application = build_application("sqlite")
    run_demonstration(application, scenarios=DEMO_SCENARIOS)

    evidence = application.storage.get_evidence()
    assert evidence
    assert all(item.source == SOURCE_SIMULATION for item in evidence)
    assert (
        len(application.storage.get_evidence(source=SOURCE_SIMULATION))
        == len(evidence)
    )

