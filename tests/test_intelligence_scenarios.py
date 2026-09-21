"""Simulator-driven validation of the Phase 2 intelligence pipeline.

The simulator is used only as a validation source. Assertions describe
behavioral expectations (deviation evidence, shift response, change point
formation) rather than exact record counts or scenario names inside logic.
"""

from datetime import datetime, timedelta, timezone

from src.data.simulator import SCENARIOS, SystemSimulator
from src.intelligence.service import BehaviorIntelligenceService

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
HISTORY_POINTS = 30
CURRENT_POINTS = 4


def build_history(simulator):
    """Build a normal-only historical window from the deterministic simulator."""
    batches = simulator.generate_history(
        start_time=START,
        points=HISTORY_POINTS,
        scenario="normal",
    )
    return [record for batch in batches for record in batch.records()]


def build_current(simulator, scenario):
    batches = simulator.generate_history(
        start_time=START + timedelta(days=1),
        points=CURRENT_POINTS,
        scenario=scenario,
    )
    return [record for batch in batches for record in batch.records()]


def records_split(records):
    metrics = [r for r in records if not hasattr(r, "level") and hasattr(r, "value")]
    logs = [r for r in records if hasattr(r, "level")]
    events = [r for r in records if hasattr(r, "event_type")]
    deployments = [r for r in records if hasattr(r, "version") and hasattr(r, "status")]
    return metrics, logs, events, deployments


def analyze_scenario(scenario):
    simulator = SystemSimulator(seed=42)
    service = BehaviorIntelligenceService()

    historical = build_history(simulator)
    current = build_current(simulator, scenario)

    historical_metrics, _, _, _ = records_split(historical)
    current_metrics, current_logs, current_events, current_deployments = records_split(
        current
    )

    baselines = service.build_baselines(historical_metrics)

    return service.analyze(
        baselines,
        current_metrics,
        logs=current_logs,
        events=current_events,
        deployments=current_deployments,
    )


def test_normal_scenario_stays_near_baseline():
    for scenario in ("normal", "deployment"):
        analysis = analyze_scenario(scenario)
        assert analysis.changed_services == []
        assert analysis.overall_score == 0.0

        for result in analysis.service_results:
            assert result.shift.score == 0.0


def test_abnormal_scenarios_produce_deviation_evidence():
    abnormal = [
        "latency_spike",
        "error_spike",
        "database_pressure",
        "deployment_regression",
    ]

    for scenario in abnormal:
        analysis = analyze_scenario(scenario)
        assert analysis.changed_services, f"expected changes for {scenario}"
        assert analysis.overall_score > 0.0

        for result in analysis.service_results:
            for behavior in result.fingerprint.metric_behaviors:
                assert behavior.observation_count == CURRENT_POINTS


def test_sustained_anomaly_forms_change_point():
    analysis = analyze_scenario("database_pressure")

    for result in analysis.service_results:
        if result.shift.change_detected:
            assert result.fingerprint.change_point_count >= 1
            assert result.fingerprint.behavior_state.value == "changing"


def test_isolated_anomaly_does_not_form_change_point():
    simulator = SystemSimulator(seed=42)
    service = BehaviorIntelligenceService()

    historical = build_history(simulator)
    historical_metrics, _, _, _ = records_split(historical)

    single = simulator.generate_batch(
        timestamp=START + timedelta(days=1),
        scenario="latency_spike",
    )
    current_metrics, current_logs, current_events, current_deployments = records_split(
        single.records()
    )

    analysis = service.analyze(
        service.build_baselines(historical_metrics),
        current_metrics,
        logs=current_logs,
        events=current_events,
        deployments=current_deployments,
    )

    for result in analysis.service_results:
        assert result.fingerprint.change_point_count == 0


def test_deployment_scenario_records_deployment_evidence():
    analysis = analyze_scenario("deployment")

    assert analysis.overall_score == 0.0

    payment = next(
        result for result in analysis.service_results if result.service == "payment-service"
    )
    assert payment.fingerprint.deployment_count == CURRENT_POINTS


def test_all_simulator_scenarios_are_covered():
    assert set(SCENARIOS) == {
        "normal",
        "latency_spike",
        "error_spike",
        "database_pressure",
        "deployment",
        "deployment_regression",
    }