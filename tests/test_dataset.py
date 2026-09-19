from datetime import datetime, timezone

import pytest

from src.data.dataset import HistoricalDatasetBuilder
from src.data.simulator import SystemSimulator


def test_default_historical_dataset_is_deterministic():
    start_time = datetime(2026, 1, 1, tzinfo=timezone.utc)

    builder_a = HistoricalDatasetBuilder(
        simulator=SystemSimulator(seed=42)
    )
    builder_b = HistoricalDatasetBuilder(
        simulator=SystemSimulator(seed=42)
    )

    dataset_a = builder_a.build(start_time)
    dataset_b = builder_b.build(start_time)

    assert dataset_a.scenarios == dataset_b.scenarios
    assert dataset_a.record_count == dataset_b.record_count

    for batch_a, batch_b in zip(dataset_a.batches, dataset_b.batches):
        assert batch_a.timestamp == batch_b.timestamp
        assert batch_a.scenario == batch_b.scenario


def test_default_dataset_contains_expected_scenarios():
    start_time = datetime(2026, 1, 1, tzinfo=timezone.utc)

    dataset = HistoricalDatasetBuilder(
        simulator=SystemSimulator(seed=42)
    ).build(start_time)

    assert dataset.batch_count == 15
    assert dataset.scenarios == [
        "normal",
        "normal",
        "normal",
        "deployment",
        "deployment_regression",
        "deployment_regression",
        "normal",
        "normal",
        "database_pressure",
        "database_pressure",
        "normal",
        "error_spike",
        "normal",
        "latency_spike",
        "normal",
    ]


def test_dataset_records_are_chronological():
    start_time = datetime(2026, 1, 1, tzinfo=timezone.utc)

    dataset = HistoricalDatasetBuilder().build(start_time)

    timestamps = [record.timestamp for record in dataset.records]

    assert timestamps == sorted(timestamps)


def test_custom_scenario_sequence_is_supported():
    start_time = datetime(2026, 1, 1, tzinfo=timezone.utc)

    scenarios = (
        "normal",
        "database_pressure",
        "normal",
    )

    dataset = HistoricalDatasetBuilder().build(
        start_time=start_time,
        scenarios=scenarios,
    )

    assert dataset.scenarios == list(scenarios)
    assert dataset.batch_count == 3


def test_empty_scenario_sequence_is_rejected():
    start_time = datetime(2026, 1, 1, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="At least one scenario"):
        HistoricalDatasetBuilder().build(
            start_time=start_time,
            scenarios=[],
        )
