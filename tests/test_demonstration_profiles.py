"""Tests for the shared demonstration pipeline and its timeline profiles.

The demonstration pipeline lives in :mod:`src.data.demonstration` so the
dashboard and the standalone agent run exactly the same code. The profiles
select how much simulated history is analysed; both must be complete runs of
the same pipeline.
"""

from __future__ import annotations

import pytest

from src.data.demonstration import (
    FULL_SCENARIOS,
    QUICK_SCENARIOS,
    Demonstration,
    resolve_scenarios,
    run_demonstration,
    timeline_label,
)
from src.data.schemas import SOURCE_SIMULATION
from src.orchestrator import create_application


@pytest.fixture
def memory_app(monkeypatch, tmp_path):
    monkeypatch.delenv("RESON_STORAGE_BACKEND", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
    monkeypatch.setenv("RESON_SQLITE_PATH", str(tmp_path / "demo.db"))
    return create_application("memory")


def test_the_full_profile_uses_the_default_timeline():
    assert resolve_scenarios(profile="full") is None
    assert timeline_label(None) == "full"


def test_the_quick_profile_uses_the_short_timeline():
    assert resolve_scenarios(profile="quick") == QUICK_SCENARIOS
    assert timeline_label(QUICK_SCENARIOS) == "quick"


def test_an_explicit_scenario_list_wins():
    assert resolve_scenarios(("normal", "error_spike")) == (
        "normal",
        "error_spike",
    )
    assert resolve_scenarios(("normal", "error_spike"), profile="quick") == (
        "normal",
        "error_spike",
    )


def test_an_empty_scenario_list_is_rejected():
    with pytest.raises(ValueError):
        resolve_scenarios(())


def test_an_unknown_profile_is_rejected():
    with pytest.raises(ValueError):
        resolve_scenarios(profile="enormous")


def test_a_custom_timeline_is_labelled_as_custom():
    assert timeline_label(("normal", "error_spike")) == "custom"


def test_the_quick_timeline_produces_incidents(memory_app):
    demo = run_demonstration(
        memory_app, scenarios=resolve_scenarios(profile="quick")
    )
    assert isinstance(demo, Demonstration)
    assert demo.timeline == "quick"
    assert demo.batches == len(QUICK_SCENARIOS)
    assert demo.records > 0
    assert demo.incident_result is not None
    assert demo.incident_result.researches, "the quick run must find incidents"
    for research in demo.incident_result.researches:
        assert research.incident.source == SOURCE_SIMULATION


def test_the_full_timeline_produces_incidents(memory_app):
    demo = run_demonstration(
        memory_app, scenarios=resolve_scenarios(profile="full")
    )
    assert demo.timeline == "full"
    assert demo.batches == len(FULL_SCENARIOS)
    assert demo.incident_result is not None
    assert demo.incident_result.researches


def test_the_quick_timeline_is_smaller_than_the_full_one(memory_app):
    quick = run_demonstration(
        memory_app, scenarios=resolve_scenarios(profile="quick")
    )
    full = run_demonstration(
        memory_app, scenarios=resolve_scenarios(profile="full")
    )
    assert quick.records < full.records
    assert len(quick.incident_result.researches) >= 1
