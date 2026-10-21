"""Tests for the standalone RESON collector agent.

The agent is exercised with in-memory storage and a controlled collector so
the tests are deterministic and offline. A separate live test runs one real
collection cycle against the running platform and a temporary SQLite file to
prove the agent works end to end.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from src.data.agent import (
    AgentConfig,
    CollectorAgent,
    map_event_type,
)
from src.data.collector import (
    BaseCollector,
    HostSample,
    ProcessSample,
)
from src.data.memory_storage import InMemoryStorage
from src.data.schemas import (
    Environment,
    EventType,
    Metric,
    SOURCE_LINUX_LOCAL,
)


class StubCollector(BaseCollector):
    """Deterministic collector used to drive the agent in tests."""

    def __init__(self, host_id="stub-host", source=SOURCE_LINUX_LOCAL):
        self.collect_calls = 0
        super().__init__(host_id=host_id, source=source)

    @staticmethod
    def _default_host_id() -> str:
        return "stub-host"

    @staticmethod
    def capabilities() -> dict[str, bool]:
        return {"cpu": True, "memory": True}

    def collect_host(self) -> HostSample:
        self.collect_calls += 1
        return HostSample(
            timestamp=datetime.now(timezone.utc),
            host_id=self._host_id,
            source=self._source,
            cpu_percent=25.0,
            memory_percent=35.0,
            memory_used_bytes=1000,
            memory_total_bytes=2000,
            ok=True,
        )

    def collect_processes(self) -> tuple[ProcessSample, ...]:
        return (
            ProcessSample(
                timestamp=datetime.now(timezone.utc),
                host_id=self._host_id,
                source=self._source,
                pid=7,
                name="stub",
                cpu_percent=2.0,
                memory_rss_bytes=512,
                status="running",
                ok=True,
            ),
        )


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_default_config_is_valid():
    config = AgentConfig().validate()
    assert config.storage_backend == "sqlite"
    assert config.interval_seconds >= 1.0
    assert config.retention_days == 30
    assert config.collect_events is False


def test_interval_below_the_minimum_is_rejected():
    with pytest.raises(ValueError) as excinfo:
        AgentConfig(interval_seconds=0.1).validate()
    assert "interval_seconds" in str(excinfo.value)


def test_retention_below_the_minimum_is_rejected():
    with pytest.raises(ValueError) as excinfo:
        AgentConfig(retention_days=0).validate()
    assert "retention_days" in str(excinfo.value)


def test_event_max_records_must_be_positive():
    with pytest.raises(ValueError):
        AgentConfig(event_max_records=0).validate()


# ---------------------------------------------------------------------------
# Collection and ingestion
# ---------------------------------------------------------------------------


def test_run_once_ingests_metrics_with_source_and_host_id():
    storage = InMemoryStorage()
    agent = CollectorAgent(
        config=AgentConfig(storage_backend="memory"),
        storage=storage,
        collector=StubCollector(),
    )
    count = agent.run_once()
    assert count > 0

    metrics = storage.get_metrics()
    assert metrics
    assert {m.source for m in metrics} == {SOURCE_LINUX_LOCAL}
    assert {m.host_id for m in metrics} == {"stub-host"}
    assert {m.metric_name for m in metrics} >= {
        "cpu_percent",
        "memory_percent",
        "process_cpu_percent",
    }


def test_health_reports_collection_progress():
    agent = CollectorAgent(
        config=AgentConfig(storage_backend="memory"),
        storage=InMemoryStorage(),
        collector=StubCollector(),
    )
    agent.run_once()
    health = agent.health()
    assert health.collection_count == 1
    assert health.error_count == 0
    assert health.last_error is None
    assert health.last_collection is not None
    assert health.records_ingested > 0
    assert health.host_id == "stub-host"
    assert health.source == SOURCE_LINUX_LOCAL


def test_collection_failure_is_recorded_and_reraised():
    class FailingCollector(StubCollector):
        def collect_host(self):
            raise RuntimeError("collector exploded")

    agent = CollectorAgent(
        config=AgentConfig(storage_backend="memory"),
        storage=InMemoryStorage(),
        collector=FailingCollector(),
    )
    with pytest.raises(RuntimeError):
        agent.run_once()

    health = agent.health()
    assert health.error_count == 1
    assert "collector exploded" in health.last_error


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_start_and_stop_control_the_collection_loop():
    collector = StubCollector()
    agent = CollectorAgent(
        config=AgentConfig(
            storage_backend="memory", interval_seconds=1.0
        ),
        storage=InMemoryStorage(),
        collector=collector,
    )
    agent.start()
    assert agent.is_running is True
    assert collector.is_running is True

    deadline = time.time() + 5.0
    while agent.health().records_ingested == 0 and time.time() < deadline:
        time.sleep(0.05)

    agent.stop()
    assert agent.is_running is False
    assert collector.is_running is False


def test_starting_twice_does_not_start_a_second_loop():
    collector = StubCollector()
    agent = CollectorAgent(
        config=AgentConfig(storage_backend="memory", interval_seconds=1.0),
        storage=InMemoryStorage(),
        collector=collector,
    )
    agent.start()
    thread = agent._thread
    agent.start()
    assert agent._thread is thread
    agent.stop()


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


def test_retention_prunes_records_older_than_the_window():
    storage = InMemoryStorage()
    old = datetime.now(timezone.utc) - timedelta(days=90)
    storage.save_metric(
        Metric(
            timestamp=old,
            service="old-host",
            environment=Environment.PRODUCTION,
            metric_name="cpu_percent",
            value=1.0,
            unit="percent",
            source=SOURCE_LINUX_LOCAL,
            host_id="old-host",
        )
    )
    assert len(storage.get_metrics(host_id="old-host")) == 1

    agent = CollectorAgent(
        config=AgentConfig(
            storage_backend="memory", retention_days=30
        ),
        storage=storage,
        collector=StubCollector(),
    )
    agent.run_once()

    # The 90-day-old record is gone; the fresh sample remains.
    assert storage.get_metrics(host_id="old-host") == []
    assert storage.get_metrics(host_id="stub-host")
    assert agent.health().retention["metrics"] == 1


def test_retention_can_be_disabled():
    storage = InMemoryStorage()
    old = datetime.now(timezone.utc) - timedelta(days=365)
    storage.save_metric(
        Metric(
            timestamp=old,
            service="old-host",
            environment=Environment.PRODUCTION,
            metric_name="cpu_percent",
            value=1.0,
            unit="percent",
            source=SOURCE_LINUX_LOCAL,
            host_id="old-host",
        )
    )
    agent = CollectorAgent(
        config=AgentConfig(storage_backend="memory", retention_days=None),
        storage=storage,
        collector=StubCollector(),
    )
    agent.run_once()
    assert len(storage.get_metrics(host_id="old-host")) == 1


# ---------------------------------------------------------------------------
# Event mapping
# ---------------------------------------------------------------------------


def test_event_ids_map_onto_reson_event_types():
    assert map_event_type(6008) == EventType.RESTART
    assert map_event_type(7031) == EventType.SERVICE_FAILURE
    assert map_event_type(7045) == EventType.CONFIG_CHANGE
    assert map_event_type(9999) == EventType.SERVICE_FAILURE
    assert map_event_type(None) == EventType.SERVICE_FAILURE


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def test_cli_parser_defaults():
    from src.data.agent import build_parser

    args = build_parser().parse_args([])
    assert args.backend == "sqlite"
    assert args.interval == 5.0
    assert args.retention_days == 30
    assert args.events is False
    assert args.once is False


def test_cli_once_runs_a_single_cycle(capsys, tmp_path, monkeypatch):
    from src.data.agent import main

    monkeypatch.setenv("RESON_SQLITE_PATH", str(tmp_path / "agent.db"))
    exit_code = main(["--backend", "sqlite", "--once"])
    assert exit_code == 0
    output = capsys.readouterr().out
    assert "ingested" in output


# ---------------------------------------------------------------------------
# Live end-to-end validation
# ---------------------------------------------------------------------------


def test_agent_collects_real_metrics_on_the_running_platform(tmp_path):
    """Live check: the agent ingests genuine metrics with real provenance."""
    from src.data.sqlite_storage import SQLiteStorage

    storage = SQLiteStorage(tmp_path / "live.db")
    agent = CollectorAgent(
        config=AgentConfig(storage_backend="memory", retention_days=None),
        storage=storage,
    )
    count = agent.run_once()
    assert count > 0

    metrics = storage.get_metrics()
    assert metrics
    sources = {m.source for m in metrics}
    assert sources <= {"windows_local", "linux_local", "macos_local"}
    assert all(m.host_id for m in metrics)
    assert agent.health().records_ingested == count
