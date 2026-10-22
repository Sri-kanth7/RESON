"""Performance and batching regressions for the demonstration pipeline.

The default demonstration timeline produces ~18,700 candidate signal pairs.
Writing the resulting evidence links one row per transaction turned a local
demonstration into tens of seconds (measured: 37 s and 102 s on this
machine before the fix, depending on the backend and the disk). The engine
now writes the links in one batched transaction.

These tests pin both properties: the demonstration stays fast, and the
batched write stores exactly the same records as the per-row write did.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.services.backend import build_application, run_demonstration
from src.data.memory_storage import InMemoryStorage
from src.data.sqlite_storage import SQLiteStorage

#: The demonstration must complete well inside this budget. The measured
#: time on the development machine is ~1 s, so the budget leaves an order of
#: magnitude of headroom for slower hardware.
DEMONSTRATION_BUDGET_SECONDS = 10.0


class _CountingStorage(InMemoryStorage):
    """In-memory storage that records how the evidence was written."""

    def __init__(self) -> None:
        super().__init__()
        self.single_saves = 0
        self.batch_saves = 0
        self.batched_items = 0

    def save_evidence(self, evidence):
        self.single_saves += 1
        return super().save_evidence(evidence)

    def save_evidence_batch(self, evidence):
        self.batch_saves += 1
        self.batched_items += len(evidence)
        return super().save_evidence_batch(evidence)


@pytest.fixture
def offline_env(monkeypatch, tmp_path):
    monkeypatch.delenv("RESON_STORAGE_BACKEND", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
    monkeypatch.setenv("RESON_SQLITE_PATH", str(tmp_path / "demo.db"))
    yield monkeypatch


def test_the_default_demonstration_completes_within_the_budget(offline_env):
    """The full default timeline finishes in seconds, not a minute."""
    application = build_application("memory")
    start = time.perf_counter()
    demo = run_demonstration(application)
    elapsed = time.perf_counter() - start

    assert demo.batches == 15
    assert demo.incident_result is not None
    assert demo.incident_result.researches, "the run must produce incidents"
    assert elapsed < DEMONSTRATION_BUDGET_SECONDS, (
        f"the demonstration took {elapsed:.1f}s, over the "
        f"{DEMONSTRATION_BUDGET_SECONDS:.0f}s budget"
    )


def test_the_default_demonstration_is_fast_on_the_persistent_backend(
    offline_env, tmp_path
):
    """The persistent backend writes the evidence chain in one batch."""
    storage = SQLiteStorage(tmp_path / "demo.sqlite")
    application = build_application("memory")
    application.storage = storage

    start = time.perf_counter()
    demo = run_demonstration(application)
    elapsed = time.perf_counter() - start

    assert demo.incident_result is not None
    assert elapsed < DEMONSTRATION_BUDGET_SECONDS, (
        f"the demonstration took {elapsed:.1f}s, over the "
        f"{DEMONSTRATION_BUDGET_SECONDS:.0f}s budget"
    )
    stored = storage.get_evidence(source="simulation")
    expected = len(demo.incident_result.correlation_evidence) + sum(
        len(research.timeline) for research in demo.incident_result.researches
    )
    assert len(stored) == expected


def test_the_evidence_chain_is_written_in_one_batch(offline_env):
    """The chain is stored through a single batched call, not row by row."""
    counting = _CountingStorage()
    application = build_application("memory")
    application.storage = counting

    demo = run_demonstration(application)

    assert demo.incident_result is not None
    assert counting.batch_saves == 1
    assert counting.single_saves == 0
    assert counting.batched_items == len(
        demo.incident_result.correlation_evidence
    ) + sum(
        len(research.timeline) for research in demo.incident_result.researches
    )


def test_the_batch_and_the_per_row_write_store_the_same_records(offline_env):
    """Batching changes the write path, never the stored result.

    Record identifiers are random per build by design, so the two runs are
    compared on their structure (how many links, of which relationship type,
    with which strengths) rather than on their identifiers.
    """
    batched = _CountingStorage()
    batched_application = build_application("memory")
    batched_application.storage = batched
    batched_demo = run_demonstration(batched_application)

    per_row = _PerRowStorage()
    per_row_application = build_application("memory")
    per_row_application.storage = per_row
    per_row_demo = run_demonstration(per_row_application)

    assert batched_demo.batches == per_row_demo.batches
    assert batched_demo.records == per_row_demo.records
    assert len(batched_demo.incident_result.researches) == len(
        per_row_demo.incident_result.researches
    )
    assert [str(item.incident.severity) for item in
            batched_demo.incident_result.researches] == [
        str(item.incident.severity)
        for item in per_row_demo.incident_result.researches
    ]

    def profile(storage):
        return sorted(
            (
                item.relationship_type,
                round(item.strength, 6),
                item.source,
                item.host_id,
            )
            for item in storage.get_evidence()
        )

    assert len(batched.get_evidence()) == len(per_row.get_evidence())
    assert profile(batched) == profile(per_row)
    assert per_row.single_saves > 0


class _PerRowStorage(InMemoryStorage):
    """Storage that deliberately writes evidence one row at a time."""

    def __init__(self) -> None:
        super().__init__()
        self.single_saves = 0

    def save_evidence_batch(self, evidence):
        stored = []
        for item in evidence:
            self.single_saves += 1
            stored.append(self.save_evidence(item))
        return stored


def test_saving_the_same_incident_twice_updates_it(offline_env, tmp_path):
    """Re-saving an incident replaces it instead of failing.

    The dashboard moves an incident from ``open`` to ``investigating`` and
    saves it again; a backend that rejected the second write would make the
    action impossible on a persistent store.
    """
    from src.data.schemas import (
        Incident,
        IncidentSeverity,
        IncidentStatus,
    )
    from datetime import datetime, timezone

    storage = SQLiteStorage(tmp_path / "incident.db")
    incident = Incident(
        title="api latency",
        start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        severity=IncidentSeverity.HIGH,
        status=IncidentStatus.OPEN,
    )
    storage.save_incident(incident)
    updated = incident.model_copy(update={"status": "investigating"})
    storage.save_incident(updated)

    stored = storage.get_incidents()
    assert len(stored) == 1
    assert stored[0].status == IncidentStatus.INVESTIGATING
