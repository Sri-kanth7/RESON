"""Tests for telemetry normalization (grouping, ordering, window slicing)."""

from datetime import datetime, timedelta, timezone

from tests.conftest import make_metric, make_series

from src.preprocessing.normalizer import (
    MetricSeries,
    group_metrics,
    slice_series,
)

START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_group_metrics_separates_services_and_metrics():
    metrics = [
        make_metric(START, 100.0, service="api-service", metric_name="latency_ms"),
        make_metric(START, 50.0, service="db-service", metric_name="latency_ms"),
        make_metric(START, 42.0, service="api-service", metric_name="cpu_usage"),
    ]

    series = group_metrics(metrics)

    assert [(s.service, s.metric_name) for s in series] == [
        ("api-service", "cpu_usage"),
        ("api-service", "latency_ms"),
        ("db-service", "latency_ms"),
    ]


def test_group_metrics_separates_environments():
    metrics = [
        make_metric(START, 10.0, environment="development"),
        make_metric(START, 20.0, environment="production"),
    ]

    series = group_metrics(metrics)

    assert [s.environment for s in series] == ["development", "production"]
    assert [s.count for s in series] == [1, 1]


def test_group_metrics_orders_by_timestamp():
    payload = [
        make_metric(START + timedelta(seconds=30), 30.0),
        make_metric(START, 10.0),
        make_metric(START + timedelta(seconds=10), 20.0),
    ]

    series = group_metrics(payload)

    assert len(series) == 1
    assert [o.value for o in series[0].observations] == [10.0, 20.0, 30.0]
    assert [o.timestamp for o in series[0].observations] == [
        START,
        START + timedelta(seconds=10),
        START + timedelta(seconds=30),
    ]


def test_group_metrics_preserves_record_identity():
    metric = make_metric(START, 10.0)

    series = group_metrics([metric])

    assert series[0].observations[0].record_id == metric.id


def test_group_metrics_does_not_mutate_source_records():
    metrics = make_series([1.0, 2.0, 3.0])

    group_metrics(metrics)

    assert [m.value for m in metrics] == [1.0, 2.0, 3.0]
    assert all(m.timestamp == t for m, t in zip(metrics, [START, START + timedelta(seconds=10), START + timedelta(seconds=20)]))


def test_group_metrics_deterministic_order():
    metrics = make_series([1.0, 2.0, 3.0]) + make_series(
        [5.0], metric_name="cpu_usage"
    )

    first = group_metrics(metrics)
    second = group_metrics(list(reversed(metrics)))

    assert [(s.service, s.metric_name) for s in first] == [
        (s.service, s.metric_name) for s in second
    ]
    assert [o.value for s in first for o in s.observations] == [
        o.value for s in second for o in s.observations
    ]


def test_window_slicing_restricts_to_closed_open_interval():
    metrics = make_series([1.0, 2.0, 3.0])
    full = group_metrics(metrics)[0]

    sliced = slice_series(
        full,
        window_start=START + timedelta(seconds=10),
        window_end=START + timedelta(seconds=30),
    )

    assert sliced.count == 2
    assert [o.value for o in sliced.observations] == [2.0, 3.0]
    assert isinstance(sliced, MetricSeries)


def test_window_slicing_without_boundaries_keeps_all_observations():
    metrics = make_series([1.0, 2.0, 3.0])
    full = group_metrics(metrics)[0]

    sliced = slice_series(full)

    assert sliced.count == 3
    assert [o.value for o in sliced.observations] == [1.0, 2.0, 3.0]


def test_window_slicing_excludes_records_outside_interval():
    metrics = make_series([1.0, 2.0], interval_seconds=120)

    full = group_metrics(metrics)[0]

    sliced = slice_series(
        full,
        window_start=START,
        window_end=START + timedelta(seconds=60),
    )

    assert sliced.count == 1
    assert sliced.observations[0].value == 1.0


def test_group_metrics_empty_input_returns_empty():
    assert group_metrics([]) == []