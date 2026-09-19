"""Deterministic telemetry generation for RESON."""

from dataclasses import dataclass
from datetime import datetime, timedelta
import random

from src.data.schemas import (
    Deployment,
    DeploymentStatus,
    Environment,
    Event,
    EventType,
    Log,
    LogLevel,
    Metric,
)


@dataclass(frozen=True)
class ServiceProfile:
    """Static characteristics of a simulated service."""

    name: str
    baseline_latency_ms: float
    baseline_error_rate: float
    baseline_request_rate: float
    baseline_cpu_percent: float
    baseline_db_connections: float


SERVICE_PROFILES = (
    ServiceProfile(
        name="api-service",
        baseline_latency_ms=120.0,
        baseline_error_rate=0.5,
        baseline_request_rate=180.0,
        baseline_cpu_percent=42.0,
        baseline_db_connections=35.0,
    ),
    ServiceProfile(
        name="payment-service",
        baseline_latency_ms=180.0,
        baseline_error_rate=0.8,
        baseline_request_rate=75.0,
        baseline_cpu_percent=48.0,
        baseline_db_connections=28.0,
    ),
    ServiceProfile(
        name="database-service",
        baseline_latency_ms=35.0,
        baseline_error_rate=0.2,
        baseline_request_rate=260.0,
        baseline_cpu_percent=52.0,
        baseline_db_connections=60.0,
    ),
)


@dataclass(frozen=True)
class SimulationPoint:
    """One timestamp in the simulated system timeline."""

    timestamp: datetime
    scenario: str


class TelemetryGenerator:
    """Generate deterministic telemetry from service profiles."""

    def __init__(self, seed: int = 42) -> None:
        self._random = random.Random(seed)

    def generate_metrics(
        self,
        point: SimulationPoint,
    ) -> list[Metric]:
        """Generate metrics for every simulated service."""

        metrics: list[Metric] = []

        for profile in SERVICE_PROFILES:
            values = self._metric_values(profile, point.scenario)

            for metric_name, value, unit in values:
                metrics.append(
                    Metric(
                        timestamp=point.timestamp,
                        service=profile.name,
                        environment=Environment.DEVELOPMENT,
                        metric_name=metric_name,
                        value=round(value, 3),
                        unit=unit,
                    )
                )

        return metrics

    def generate_logs(
        self,
        point: SimulationPoint,
    ) -> list[Log]:
        """Generate logs associated with the current scenario."""

        logs: list[Log] = []

        for profile in SERVICE_PROFILES:
            level, message = self._log_behavior(
                profile,
                point.scenario,
            )

            logs.append(
                Log(
                    timestamp=point.timestamp,
                    service=profile.name,
                    environment=Environment.DEVELOPMENT,
                    level=level,
                    message=message,
                    metadata={
                        "scenario": point.scenario,
                    },
                )
            )

        return logs

    def generate_events(
        self,
        point: SimulationPoint,
    ) -> list[Event]:
        """Generate significant events for the current scenario."""

        events: list[Event] = []

        if point.scenario == "database_pressure":
            events.append(
                Event(
                    timestamp=point.timestamp,
                    service="database-service",
                    environment=Environment.DEVELOPMENT,
                    event_type=EventType.DATABASE_PRESSURE,
                    description="Database connection pressure increased.",
                    metadata={
                        "scenario": point.scenario,
                    },
                )
            )

        elif point.scenario == "deployment_regression":
            events.append(
                Event(
                    timestamp=point.timestamp,
                    service="payment-service",
                    environment=Environment.DEVELOPMENT,
                    event_type=EventType.DEPLOYMENT,
                    description="Payment service deployment completed.",
                    metadata={
                        "scenario": point.scenario,
                        "version": "2.0.0",
                    },
                )
            )

        elif point.scenario == "error_spike":
            events.append(
                Event(
                    timestamp=point.timestamp,
                    service="api-service",
                    environment=Environment.DEVELOPMENT,
                    event_type=EventType.SERVICE_FAILURE,
                    description="API service experienced elevated failures.",
                    metadata={
                        "scenario": point.scenario,
                    },
                )
            )

        elif point.scenario == "latency_spike":
            events.append(
                Event(
                    timestamp=point.timestamp,
                    service="api-service",
                    environment=Environment.DEVELOPMENT,
                    event_type=EventType.NETWORK_DEGRADATION,
                    description="Network degradation affected request latency.",
                    metadata={
                        "scenario": point.scenario,
                    },
                )
            )

        return events

    def generate_deployments(
        self,
        point: SimulationPoint,
    ) -> list[Deployment]:
        """Generate deployment records for deployment scenarios."""

        if point.scenario != "deployment":
            return []

        return [
            Deployment(
                timestamp=point.timestamp,
                service="payment-service",
                environment=Environment.DEVELOPMENT,
                version="2.0.0",
                status=DeploymentStatus.SUCCESS,
                metadata={
                    "scenario": point.scenario,
                    "previous_version": "1.9.0",
                },
            )
        ]

    def _metric_values(
        self,
        profile: ServiceProfile,
        scenario: str,
    ) -> list[tuple[str, float, str]]:
        """Calculate scenario-specific metric values."""

        latency = profile.baseline_latency_ms
        error_rate = profile.baseline_error_rate
        request_rate = profile.baseline_request_rate
        cpu = profile.baseline_cpu_percent
        db_connections = profile.baseline_db_connections

        if scenario == "latency_spike":
            latency *= 2.8
            cpu *= 1.15

        elif scenario == "error_spike":
            error_rate *= 8.0
            latency *= 1.6

        elif scenario == "database_pressure":
            db_connections *= 1.9
            latency *= 1.8
            cpu *= 1.25

        elif scenario == "deployment_regression":
            if profile.name == "payment-service":
                latency *= 2.2
                error_rate *= 5.0
                cpu *= 1.2

        return [
            (
                "latency_ms",
                self._with_noise(latency),
                "ms",
            ),
            (
                "error_rate",
                self._with_noise(error_rate),
                "percent",
            ),
            (
                "request_rate",
                self._with_noise(request_rate),
                "requests_per_second",
            ),
            (
                "cpu_usage",
                self._with_noise(cpu),
                "percent",
            ),
            (
                "db_connections",
                self._with_noise(db_connections),
                "connections",
            ),
        ]

    def _log_behavior(
        self,
        profile: ServiceProfile,
        scenario: str,
    ) -> tuple[LogLevel, str]:
        """Generate a log level and message for a service."""

        if scenario == "database_pressure":
            return (
                LogLevel.WARNING,
                "Database connection pool pressure detected.",
            )

        if scenario == "error_spike":
            return (
                LogLevel.ERROR,
                "Elevated request failures detected.",
            )

        if scenario == "latency_spike":
            return (
                LogLevel.WARNING,
                "Request latency is above historical baseline.",
            )

        if scenario == "deployment_regression":
            if profile.name == "payment-service":
                return (
                    LogLevel.ERROR,
                    "Payment requests are experiencing elevated failures.",
                )

            return (
                LogLevel.INFO,
                "Service operating normally after deployment.",
            )

        return (
            LogLevel.INFO,
            "Service operating within expected behavior.",
        )

    def _with_noise(self, value: float) -> float:
        """Apply small deterministic noise to a baseline value."""

        noise = self._random.uniform(-0.05, 0.05)

        return value * (1.0 + noise)
