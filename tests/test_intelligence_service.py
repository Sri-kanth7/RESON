"""Tests for the BehaviorIntelligenceService end-to-end pipeline."""

from datetime import timedelta

from tests.conftest import (
    DEFAULT_START,
    make_deployment,
    make_event,
    make_log,
    make_series,
)

from src.intelligence.service import BehaviorIntelligenceService


def build_service():
    return BehaviorIntelligenceService()


def history_for(service):
    return (
        make_series([10.0, 20.0, 30.0], service=service, metric_name="latency_ms")
        + make_series([10.0, 20.0, 30.0], service=service, metric_name="cpu_usage")
    )


def current_for(service, latency_values):
    return (
        make_series(latency_values, service=service, metric_name="latency_ms")
        + make_series([10.0, 20.0, 30.0, 30.0], service=service, metric_name="cpu_usage")
    )


def test_end_to_end_historical_baseline_plus_current_window():
    service_ = BehaviorIntelligenceService()

    baselines = service_.build_baselines(
        history_for("api-service") + history_for("payment-service")
    )

    analysis = service_.analyze(
        baselines,
        current_for("api-service", [20.0, 90.0, 90.0, 90.0])
        + current_for("payment-service", [10.0, 20.0, 30.0, 20.0]),
        logs=[
            make_log(DEFAULT_START, level="ERROR", service="api-service"),
            make_log(DEFAULT_START, level="INFO", service="payment-service"),
        ],
        events=[make_event(DEFAULT_START, service="api-service")],
        deployments=[make_deployment(DEFAULT_START, service="api-service")],
    )

    assert [result.service for result in analysis.service_results] == [
        "api-service",
        "payment-service",
    ]
    assert analysis.changed_services == ["api-service"]
    assert analysis.overall_score > 0.0

    api = analysis.service_results[0]
    payment = analysis.service_results[1]

    assert api.shift.score > 0.0
    assert payment.shift.score == 0.0

    assert api.fingerprint.log_summary == {"ERROR": 1}
    assert api.fingerprint.event_summary == {"restart": 1}
    assert api.fingerprint.deployment_count == 1
    assert api.fingerprint.change_point_count == 1
    assert api.fingerprint.behavior_state.value == "changing"

    assert payment.fingerprint.deployment_count == 0


def test_analysis_windows_are_recorded():
    service_ = build_service()

    baselines = service_.build_baselines(history_for("api-service"))
    current = current_for("api-service", [20.0, 90.0, 90.0, 90.0])

    analysis = service_.analyze(baselines, current)

    assert analysis.historical_window_start == DEFAULT_START
    assert analysis.historical_window_end == DEFAULT_START + timedelta(seconds=20)
    assert analysis.analysis_window_start == DEFAULT_START
    assert analysis.analysis_window_end == DEFAULT_START + timedelta(
        seconds=30, microseconds=1
    )


def test_deviations_and_shift_are_distinguishable_between_services():
    service_ = build_service()

    baselines = service_.build_baselines(
        history_for("api-service") + history_for("payment-service")
    )

    analysis = service_.analyze(
        baselines,
        current_for("api-service", [20.0, 90.0, 90.0, 90.0])
        + current_for("payment-service", [10.0, 20.0, 30.0, 20.0]),
    )

    by_service = {result.service: result for result in analysis.service_results}

    assert by_service["api-service"].fingerprint.behavior_state.value == "changing"
    assert by_service["payment-service"].fingerprint.behavior_state.value == "normal"
    assert by_service["api-service"].shift.score > by_service["payment-service"].shift.score


def test_traceability_back_to_source_telemetry():
    service_ = build_service()

    current = current_for("api-service", [20.0, 90.0, 90.0, 90.0])
    baselines = service_.build_baselines(history_for("api-service"))

    analysis = service_.analyze(baselines, current)

    api = analysis.service_results[0]
    metric_ids = {metric.id for metric in current}

    assert api.fingerprint.source_record_ids
    assert set(api.fingerprint.source_record_ids) <= metric_ids
    assert analysis.changed_services == ["api-service"]


def test_multiple_metrics_contribute_to_shift():
    service_ = build_service()

    baselines = service_.build_baselines(history_for("api-service"))
    current = (
        make_series([10.0, 20.0, 30.0], metric_name="latency_ms")
        + make_series([90.0, 90.0, 90.0, 90.0], metric_name="cpu_usage")
    )

    analysis = service_.analyze(baselines, current)

    api = analysis.service_results[0]
    contributions = api.shift.contributions
    metric_names = {contribution.key.metric_name for contribution in contributions}

    assert metric_names == {"latency_ms", "cpu_usage"}
    assert api.shift.score > 0.0


def test_analyze_history_is_equivalent_to_build_then_analyze():
    service_ = build_service()

    historical = history_for("api-service")
    current = current_for("api-service", [20.0, 90.0, 90.0, 90.0])

    combined = service_.analyze_history(
        historical_metrics=historical,
        metrics=current,
        historical_window=(DEFAULT_START, DEFAULT_START + timedelta(seconds=30)),
    )

    manual_baselines = service_.build_baselines(historical)
    manual = service_.analyze(manual_baselines, current)

    assert combined.overall_score == manual.overall_score
    assert combined.changed_services == manual.changed_services
    assert (
        combined.service_results[0].shift.score
        == manual.service_results[0].shift.score
    )


def test_empty_current_window_returns_empty_analysis():
    service_ = build_service()

    baselines = service_.build_baselines(history_for("api-service"))

    analysis = service_.analyze(baselines, [])

    assert analysis.service_results == []
    assert analysis.overall_score == 0.0
    assert analysis.changed_services == []


def test_sustained_deviation_creates_change_point_evidence():
    service_ = build_service()

    baselines = service_.build_baselines(history_for("api-service"))
    analysis = service_.analyze(
        baselines,
        current_for("api-service", [90.0, 90.0, 90.0, 90.0]),
    )

    api = analysis.service_results[0]
    assert api.fingerprint.change_point_count == 1
    assert api.shift.change_detected is True
    assert api.fingerprint.behavior_state.value == "changing"

    behavior = next(
        b for b in api.fingerprint.metric_behaviors if b.key.metric_name == "latency_ms"
    )
    assert behavior.anomaly_count == 4
    assert behavior.peak_abs_z == 7.0


def test_analysis_is_deterministic():
    service_ = build_service()

    historical = history_for("api-service") + history_for("payment-service")
    current = (
        current_for("api-service", [20.0, 90.0, 90.0, 90.0])
        + current_for("payment-service", [10.0, 20.0, 30.0, 20.0])
    )

    first = service_.analyze(service_.build_baselines(historical), current)
    second = service_.analyze(service_.build_baselines(historical), current)

    assert first.model_dump() == second.model_dump()