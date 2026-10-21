"""Tests for UI-facing services (offline, deterministic, no Streamlit run)."""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone

from app import theme as th
from app.services import backend as svc
from src.common.config import get_config
from src.data.memory_storage import InMemoryStorage
from src.data.schemas import Environment, Metric
from src.data.supabase_client import get_supabase_client
from src.intelligence.baseline import BaselineReadiness


@pytest.fixture
def offline_env(monkeypatch, tmp_path):
    monkeypatch.delenv("RESON_STORAGE_BACKEND", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
    # Keep any sqlite-backed path inside a throwaway directory; tests must
    # never write to the user's real data directory.
    monkeypatch.setenv("RESON_SQLITE_PATH", str(tmp_path / "ui_services.db"))
    get_config.cache_clear()
    get_supabase_client.cache_clear()
    yield monkeypatch
    get_config.cache_clear()
    get_supabase_client.cache_clear()


def test_build_memory_application_without_credentials(offline_env):
    app = svc.build_application("memory")
    assert isinstance(app.storage, InMemoryStorage)
    assert app.ingestion._storage is app.storage


def test_build_invalid_backend_raises(offline_env):
    with pytest.raises(RuntimeError, match="Unsupported storage backend"):
        svc.build_application("postgres")


def test_storage_counts_empty():
    assert svc.storage_counts(InMemoryStorage()) == {
        "metrics": 0,
        "logs": 0,
        "events": 0,
        "deployments": 0,
        "incidents": 0,
        "evidence": 0,
        "services": 0,
    }


def test_run_demonstration_is_deterministic(offline_env):
    # Record UUIDs are random per build by design, so cross-build stability
    # is asserted on values and structure, not on full model dumps.
    first = svc.run_demonstration(svc.build_application("memory"))
    second = svc.run_demonstration(svc.build_application("memory"))

    assert first.simulated is True
    assert (first.batches, first.records) == (second.batches, second.records)
    assert (
        first.analysis.overall_score == second.analysis.overall_score
    )
    assert (
        first.analysis.changed_services == second.analysis.changed_services
    )
    assert len(first.incident_result.researches) == len(
        second.incident_result.researches
    )
    assert [
        str(getattr(r.incident.severity, "value", r.incident.severity))
        for r in first.incident_result.researches
    ] == [
        str(getattr(r.incident.severity, "value", r.incident.severity))
        for r in second.incident_result.researches
    ]
    assert first.analysis.changed_services


def test_run_demonstration_persists_to_storage(offline_env):
    app = svc.build_application("memory")
    demo = svc.run_demonstration(app)
    counts = svc.storage_counts(app.storage)
    assert counts["metrics"] > 0
    assert counts["incidents"] == len(demo.incident_result.researches)
    assert counts["evidence"] > 0


def test_incident_helpers_without_demo():
    assert svc.severity_explanation(None, "anything") is None
    assert svc.research_for(None, "anything") is None
    assert (
        svc.historical_matches_for(None, InMemoryStorage(), "anything") == []
    )


def test_ordered_incidents_newest_first(offline_env):
    app = svc.build_application("memory")
    demo = svc.run_demonstration(app)
    ordered = svc.ordered_incidents(app.storage)
    assert len(ordered) == len(demo.incident_result.researches)
    starts = [i.start_time for i in ordered]
    assert starts == sorted(starts, reverse=True)
    label = svc.incident_label(ordered[0])
    assert "·" in label and str(ordered[0].id)[:8] in label


def test_severity_explanation_available_in_session(offline_env):
    app = svc.build_application("memory")
    demo = svc.run_demonstration(app)
    research = demo.incident_result.researches[0]
    explanation = svc.severity_explanation(demo, research.incident.id)
    assert explanation
    assert svc.research_for(demo, research.incident.id) is research


def test_historical_matches_use_real_engine(offline_env):
    app = svc.build_application("memory")
    first = svc.run_demonstration(app)
    current = first.incident_result.researches[0]

    # With only the current incident stored, exclusion leaves no candidates.
    assert (
        svc.historical_matches_for(first, app.storage, current.incident.id)
        == []
    )

    # A second analysis window persists a distinct incident to match against.
    second = svc.run_demonstration(app)
    latest = second.incident_result.researches[0]
    matches = svc.historical_matches_for(
        second, app.storage, latest.incident.id, top_k=5
    )
    assert matches
    assert all(
        m.historical_incident_id != latest.incident.id for m in matches
    )
    scores = [m.similarity.overall_similarity for m in matches]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] > 0.0


def test_mark_investigating_persists(offline_env):
    from datetime import datetime, timezone

    from src.data.schemas import (
        Incident,
        IncidentSeverity,
        IncidentStatus,
    )

    app = svc.build_application("memory")
    incident = Incident(
        title="open incident",
        start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end_time=None,
        severity=IncidentSeverity.HIGH,
        status=IncidentStatus.OPEN,
        affected_services=["api-service"],
    )
    app.storage.save_incident(incident)
    updated = svc.mark_investigating(app.storage, incident)
    assert (
        str(getattr(updated.status, "value", updated.status))
        == "investigating"
    )
    reloaded = app.storage.get_incidents()[0]
    assert (
        str(getattr(reloaded.status, "value", reloaded.status))
        == "investigating"
    )


def test_metric_keys_and_series(offline_env):
    app = svc.build_application("memory")
    demo = svc.run_demonstration(app)
    keys = svc.metric_keys(app.storage)
    assert keys
    assert all(k.service for k in keys)
    points = svc.series_for_key(app.storage, keys[0])
    assert points
    assert [t for t, _ in points] == sorted(t for t, _ in points)
    assert demo.baselines


def test_theme_helpers():
    assert "CRITICAL" in th.severity_chip("critical")
    assert "chip-critical" in th.severity_chip("CRITICAL")
    assert "INVESTIGATING" in th.status_chip("investigating")
    assert len(th.short_id("1234567890abcdef")) == 8
    bar = th.factor_bar("service_overlap", 0.5)
    assert "service_overlap" in bar and "0.500" in bar
    assert "width:150.0%" not in th.factor_bar("x", 1.5)


def test_accent_class():
    assert th.accent_class("critical") == "reson-accent-critical"
    assert th.accent_class("HIGH") == "reson-accent-high"
    assert th.accent_class("low") == "reson-accent-low"
    assert th.accent_class("unknown") == ""


# ---------------------------------------------------------------------------
# Storage health and observation wiring
# ---------------------------------------------------------------------------


def test_storage_health_reports_operational_and_counts(offline_env):
    app = svc.build_application("memory")
    health = svc.storage_health(app.storage)

    assert health["operational"] is True
    assert health["error"] is None
    assert health["counts"]["metrics"] == 0


def test_storage_health_distinguishes_mode_from_health(offline_env):
    app = svc.build_application("memory")
    health = svc.storage_health(app.storage)

    # Memory mode is operational with no Supabase contact.
    assert health["operational"] is True


def test_observation_publish_ingests_real_measurements(offline_env):
    from src.data.windows_collector import HostMeasurement, ProcessMeasurement

    app = svc.build_application("memory")
    publish = svc.observation_publish(app.ingestion)

    now = datetime(2026, 1, 15, 9, 30, 0, tzinfo=timezone.utc)
    host = HostMeasurement(
        timestamp=now,
        host_id="test-host",
        source="windows_local",
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
            source="windows_local",
            pid=101,
            name="svchost.exe",
            cpu_percent=5.5,
            memory_rss_bytes=42_000_000,
            status="running",
            ok=True,
        ),
    )

    publish(host, processes)

    host_metrics = app.storage.get_metrics(service="test-host")
    process_metrics = app.storage.get_metrics(service="test-host:101")
    assert len(host_metrics) == 4  # cpu, memory %, used, total
    assert len(process_metrics) == 2  # process cpu, process rss
    assert all(m.source == "windows_local" for m in [*host_metrics, *process_metrics])
    assert all(m.host_id == "test-host" for m in [*host_metrics, *process_metrics])


def test_observation_publish_omits_unavailable_measurements(offline_env):
    from src.data.windows_collector import HostMeasurement

    app = svc.build_application("memory")
    publish = svc.observation_publish(app.ingestion)

    now = datetime(2026, 1, 15, 9, 30, 0, tzinfo=timezone.utc)
    host = HostMeasurement(
        timestamp=now,
        host_id="test-host",
        source="windows_local",
        cpu_percent=None,
        memory_percent=None,
        memory_used_bytes=None,
        memory_total_bytes=None,
        ok=False,
        error="wmi down",
    )

    publish(host, ())

    assert app.storage.get_metrics(service="test-host") == []


def test_host_readiness_from_stored_observations(offline_env):
    app = svc.build_application("memory")
    host_id = "readiness-host"

    now = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)
    for index, value in enumerate([40.0, 41.0, 42.0]):
        app.storage.save_metric(
            Metric(
                timestamp=now + timedelta(minutes=index * 5),
                service=host_id,
                environment=Environment.PRODUCTION,
                metric_name="cpu_percent",
                value=value,
                unit="percent",
                source="windows_local",
                host_id=host_id,
            )
        )

    readiness = svc.host_readiness(app.storage, host_id=host_id)
    assert len(readiness) == 1
    key = next(iter(readiness))
    assert readiness[key] == BaselineReadiness.READY


def test_host_readiness_insufficient(offline_env):
    app = svc.build_application("memory")
    host_id = "readiness-host"
    now = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)
    app.storage.save_metric(
        Metric(
            timestamp=now,
            service=host_id,
            environment=Environment.PRODUCTION,
            metric_name="cpu_percent",
            value=40.0,
            unit="percent",
            source="windows_local",
            host_id=host_id,
        )
    )
    readiness = svc.host_readiness(app.storage, host_id=host_id)
    assert next(iter(readiness.values())) == BaselineReadiness.INSUFFICIENT_DATA


def test_compare_today_vs_prior_day_no_history(offline_env):
    app = svc.build_application("memory")
    host_id = "compare-host"
    reference = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)

    result = svc.compare_today_vs_prior_day(
        app.storage, host_id=host_id, source="windows_local",
        reference_time=reference,
    )

    assert result.sufficient is False
    assert result.analysis is None
    assert any("no historical" in reason.lower() for reason in result.reasons)


def test_compare_today_vs_prior_day_with_history(offline_env):
    app = svc.build_application("memory")
    host_id = "compare-host"
    reference = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)
    yesterday = reference - timedelta(days=1)

    for index, value in enumerate([40.0, 41.0, 42.0, 41.0, 40.0]):
        app.storage.save_metric(
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
    app.storage.save_metric(
        Metric(
            timestamp=reference,
            service=host_id,
            environment=Environment.PRODUCTION,
            metric_name="cpu_percent",
            value=90.0,
            unit="percent",
            source="windows_local",
            host_id=host_id,
        )
    )

    result = svc.compare_today_vs_prior_day(
        app.storage, host_id=host_id, source="windows_local",
        reference_time=reference,
    )

    assert result.sufficient is True
    assert any(d.is_anomalous for d in result.deviations)


def test_sparkline_renders_real_values_only():
    empty = th.sparkline([])
    assert empty == ""
    single = th.sparkline([5.0])
    assert single.count("<polyline") == 1
    series = th.sparkline([1.0, 2.0, 3.0])
    assert "polyline" in series and "svg" in series


def test_components_render_without_backend():
    from unittest import mock

    from app.components import ui

    with mock.patch("streamlit.markdown") as markdown, mock.patch(
        "streamlit.container"
    ) as container, mock.patch("streamlit.dataframe"):
        ui.page_header("k", "t", "d")
        ui.metric_card("label", "1", sub="s", badge="b", spark=[1, 2, 3])
        ui.panel_header("title", "subtitle")
        ui.registry_card("critical", "abc12345", "title", "open", "meta")
        ui.mesh_row("svc", "detail", "NOMINAL", "chip-low")
        ui.stream_item("00:00:00", "svc", "message", "chip-info")
        ui.flow([("n1", "d1", "id1", "corr"), ("n2", "d2", "id2", "")])
    assert markdown.call_count >= 8

