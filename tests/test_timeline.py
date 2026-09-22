"""Tests for deterministic incident timeline construction."""

from uuid import uuid4

from src.data.schemas import Evidence, EvidenceType
from src.incidents.evidence import link_incident_signals, order_timeline
from src.incidents.models import Signal
from src.incidents.timeline import TimelineBuilder
from tests.incident_test_helpers import KEY, make_deviation
from tests.incident_test_helpers import ts


def incident_id():
    return uuid4()


def make_signal(seconds, seed):
    deviation = make_deviation(seconds, seed=seed)
    return Signal(
        source_id=deviation.record_id,
        source_type=EvidenceType.METRIC,
        timestamp=deviation.timestamp,
        service=KEY.service,
        environment=KEY.environment,
        description="metric evidence",
        strength=1.0,
        trigger=True,
        related_keys=[KEY],
        source_record_ids=[deviation.record_id],
    )


def test_timeline_is_chronologically_ordered():
    builder = TimelineBuilder()
    signals = [
        make_signal(40, "t-4"),
        make_signal(10, "t-1"),
        make_signal(30, "t-3"),
    ]
    timeline = builder.build(incident_id(), signals)
    timestamps = [item.timestamp for item in timeline]
    assert timestamps == sorted(timestamps)


def test_timestamps_ticks_break_by_stable_id():
    builder = TimelineBuilder()
    signals = [
        make_signal(10, seed="tie-1"),
        make_signal(10, seed="tie-2"),
    ]
    timeline = builder.build(incident_id(), signals)
    # Both entries share a timestamp; ordering must be fully stable (insertion
    # order reversed here would otherwise produce a different result).
    reversed_signals = [signals[1], signals[0]]
    first = builder.build(incident_id(), reversed_signals)
    assert [item.target_id for item in timeline] == [
        item.target_id for item in first
    ]
    source_ids = [str(item.target_id) for item in timeline]
    assert source_ids == sorted(source_ids)


def test_timeline_has_no_duplicate_entries():
    builder = TimelineBuilder()
    signals = [
        make_signal(10, seed="nd-1"),
        make_signal(20, seed="nd-2"),
    ]
    timeline = builder.build(incident_id(), signals)
    target_ids = [item.target_id for item in timeline]
    assert len(target_ids) == len(set(target_ids))
    assert len(timeline) == 2


def test_timeline_entries_stay_traceable_to_source():
    builder = TimelineBuilder()
    incident = incident_id()
    signals = [make_signal(10, seed="tr-1")]
    timeline = builder.build(incident, signals)
    item = timeline[0]
    assert item.target_id == signals[0].source_id
    assert item.target_type == EvidenceType.METRIC.value
    assert item.source_type == EvidenceType.INCIDENT.value
    assert str(incident) in item.explanation
    assert item.strength == 1.0


def test_order_timeline_is_deterministic_across_runs():
    first = order_timeline(
        [
            make_signal_entry(30),
            make_signal_entry(10),
        ]
    )
    second = order_timeline(
        [
            make_signal_entry(10),
            make_signal_entry(30),
        ]
    )
    assert [item.target_id for item in first] == [
        item.target_id for item in second
    ]


def make_signal_entry(seconds):
    signal = make_signal(seconds, seed=f"oe-{seconds}")
    return link_incident_signals(uuid4(), [signal])[0]


def test_chain_links_every_signal_once():
    incident = incident_id()
    signals = [
        make_signal(10, "ch-1"),
        make_signal(20, "ch-2"),
        make_signal(30, "ch-3"),
    ]
    chain = link_incident_signals(incident, signals)
    assert len(chain) == 3
    assert all(item.source_id == incident for item in chain)