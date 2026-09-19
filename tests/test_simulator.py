from datetime import datetime, timezone

from src.data.simulator import SystemSimulator


def test_simulator_generates_expected_telemetry():
    simulator = SystemSimulator(seed=42)

    batch = simulator.generate_batch(
        timestamp=datetime(
            2026,
            1,
            1,
            tzinfo=timezone.utc,
        ),
        scenario="normal",
    )

    assert len(batch.metrics) == 15
    assert len(batch.logs) == 3
    assert len(batch.events) == 0
    assert len(batch.deployments) == 0


def test_database_pressure_generates_event():
    simulator = SystemSimulator(seed=42)

    batch = simulator.generate_batch(
        timestamp=datetime(
            2026,
            1,
            1,
            tzinfo=timezone.utc,
        ),
        scenario="database_pressure",
    )

    assert len(batch.metrics) == 15
    assert len(batch.logs) == 3
    assert len(batch.events) == 1
    assert batch.events[0].event_type == "database_pressure"


def test_deployment_regression_represents_post_deployment_behavior():
    simulator = SystemSimulator(seed=42)

    batch = simulator.generate_batch(
        timestamp=datetime(
            2026,
            1,
            1,
            tzinfo=timezone.utc,
        ),
        scenario="deployment_regression",
    )

    assert len(batch.metrics) == 15
    assert len(batch.logs) == 3
    assert len(batch.events) == 1

    # The deployment happened earlier in the timeline.
    # This scenario represents the resulting system behavior.
    assert len(batch.deployments) == 0

    payment_latency = next(
        metric.value
        for metric in batch.metrics
        if (
            metric.service == "payment-service"
            and metric.metric_name == "latency_ms"
        )
    )

    payment_error_rate = next(
        metric.value
        for metric in batch.metrics
        if (
            metric.service == "payment-service"
            and metric.metric_name == "error_rate"
        )
    )

    assert payment_latency > 300
    assert payment_error_rate > 3.0


def test_simulation_is_deterministic():
    timestamp = datetime(
        2026,
        1,
        1,
        tzinfo=timezone.utc,
    )

    simulator_a = SystemSimulator(seed=42)
    simulator_b = SystemSimulator(seed=42)

    batch_a = simulator_a.generate_batch(
        timestamp=timestamp,
        scenario="latency_spike",
    )

    batch_b = simulator_b.generate_batch(
        timestamp=timestamp,
        scenario="latency_spike",
    )

    values_a = [
        metric.value
        for metric in batch_a.metrics
    ]

    values_b = [
        metric.value
        for metric in batch_b.metrics
    ]

    assert values_a == values_b


def test_scenario_timeline_preserves_temporal_order():
    simulator = SystemSimulator(
        seed=42,
        interval_seconds=10,
    )

    start_time = datetime(
        2026,
        1,
        1,
        12,
        0,
        tzinfo=timezone.utc,
    )

    scenarios = [
        "normal",
        "normal",
        "deployment",
        "deployment_regression",
        "deployment_regression",
        "normal",
    ]

    timeline = simulator.generate_scenario_timeline(
        start_time=start_time,
        scenarios=scenarios,
    )

    assert len(timeline) == len(scenarios)

    for index, batch in enumerate(timeline):
        assert batch.scenario == scenarios[index]

    assert timeline[0].timestamp == start_time
    assert timeline[1].timestamp > timeline[0].timestamp
    assert timeline[2].timestamp > timeline[1].timestamp


def test_deployment_precedes_regression_telemetry():
    simulator = SystemSimulator(
        seed=42,
        interval_seconds=10,
    )

    start_time = datetime(
        2026,
        1,
        1,
        12,
        0,
        tzinfo=timezone.utc,
    )

    timeline = simulator.generate_scenario_timeline(
        start_time=start_time,
        scenarios=[
            "normal",
            "deployment",
            "deployment_regression",
            "deployment_regression",
        ],
    )

    deployment_batch = timeline[1]
    regression_batch = timeline[2]

    assert len(deployment_batch.deployments) == 1
    assert deployment_batch.deployments[0].version == "2.0.0"

    assert len(regression_batch.deployments) == 0

    assert regression_batch.timestamp > deployment_batch.timestamp

    payment_metrics = [
        metric
        for metric in regression_batch.metrics
        if metric.service == "payment-service"
    ]

    assert any(
        metric.metric_name == "latency_ms"
        and metric.value > 300
        for metric in payment_metrics
    )


def test_deployment_itself_uses_normal_system_metrics():
    simulator = SystemSimulator(seed=42)

    timestamp = datetime(
        2026,
        1,
        1,
        12,
        0,
        tzinfo=timezone.utc,
    )

    batch = simulator.generate_batch(
        timestamp=timestamp,
        scenario="deployment",
    )

    assert len(batch.deployments) == 1

    payment_latency = next(
        metric.value
        for metric in batch.metrics
        if (
            metric.service == "payment-service"
            and metric.metric_name == "latency_ms"
        )
    )

    # Deployment timestamp itself should still look normal.
    assert payment_latency < 250
