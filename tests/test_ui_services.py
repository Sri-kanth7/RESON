"""Tests for UI-facing services (offline, deterministic, no Streamlit run)."""

import pytest

from app import theme as th
from app.services import backend as svc
from src.common.config import get_config
from src.data.memory_storage import InMemoryStorage
from src.data.supabase_client import get_supabase_client


@pytest.fixture
def offline_env(monkeypatch):
    monkeypatch.delenv("RESON_STORAGE_BACKEND", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
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

