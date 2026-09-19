"""Synthetic system simulator for RESON."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from src.data.generator import (
    SimulationPoint,
    TelemetryGenerator,
)
from src.data.schemas import Deployment, Event, Log, Metric


SCENARIOS = (
    "normal",
    "latency_spike",
    "error_spike",
    "database_pressure",
    "deployment",
    "deployment_regression",
)


@dataclass
class SimulationBatch:
    """Telemetry generated for one simulation point."""

    timestamp: datetime
    scenario: str
    metrics: list[Metric]
    logs: list[Log]
    events: list[Event]
    deployments: list[Deployment]

    def records(self) -> list[Metric | Log | Event | Deployment]:
        """Return all telemetry records in this batch."""

        return [
            *self.metrics,
            *self.logs,
            *self.events,
            *self.deployments,
        ]


class SystemSimulator:
    """Generate deterministic historical system behavior."""

    def __init__(
        self,
        seed: int = 42,
        interval_seconds: int = 10,
    ) -> None:
        self._generator = TelemetryGenerator(seed=seed)
        self._interval_seconds = interval_seconds

    def generate_batch(
        self,
        timestamp: datetime,
        scenario: str = "normal",
    ) -> SimulationBatch:
        """Generate telemetry for one timestamp."""

        if scenario not in SCENARIOS:
            raise ValueError(
                f"Unknown simulation scenario: {scenario}"
            )

        # A deployment is an event in the timeline.
        # Abnormal system behavior begins after it.
        telemetry_scenario = scenario

        if scenario == "deployment":
            telemetry_scenario = "normal"

        point = SimulationPoint(
            timestamp=timestamp,
            scenario=telemetry_scenario,
        )

        return SimulationBatch(
            timestamp=timestamp,
            scenario=scenario,
            metrics=self._generator.generate_metrics(point),
            logs=self._generator.generate_logs(point),
            events=self._generate_events(
                timestamp=timestamp,
                scenario=scenario,
            ),
            deployments=self._generator.generate_deployments(
                SimulationPoint(
                    timestamp=timestamp,
                    scenario=scenario,
                )
            ),
        )

    def generate_history(
        self,
        start_time: datetime,
        points: int,
        scenario: str = "normal",
    ) -> list[SimulationBatch]:
        """Generate a deterministic sequence using one scenario."""

        if points < 1:
            raise ValueError("points must be at least 1")

        return [
            self.generate_batch(
                timestamp=(
                    start_time
                    + timedelta(
                        seconds=index * self._interval_seconds
                    )
                ),
                scenario=scenario,
            )
            for index in range(points)
        ]

    def generate_scenario_timeline(
        self,
        start_time: datetime,
        scenarios: list[str],
    ) -> list[SimulationBatch]:
        """Generate telemetry for an explicit scenario timeline."""

        if not scenarios:
            raise ValueError(
                "scenarios must contain at least one scenario"
            )

        return [
            self.generate_batch(
                timestamp=(
                    start_time
                    + timedelta(
                        seconds=index * self._interval_seconds
                    )
                ),
                scenario=scenario,
            )
            for index, scenario in enumerate(scenarios)
        ]

    def _generate_events(
        self,
        timestamp: datetime,
        scenario: str,
    ) -> list[Event]:
        """Generate scenario-specific system events."""

        if scenario == "deployment":
            return [
                Event(
                    timestamp=timestamp,
                    service="payment-service",
                    environment="development",
                    event_type="deployment",
                    description=(
                        "Payment service deployment "
                        "completed successfully."
                    ),
                    metadata={
                        "version": "2.0.0",
                    },
                )
            ]

        # Reuse the generator's existing event behavior
        point = SimulationPoint(
            timestamp=timestamp,
            scenario=scenario,
        )

        return self._generator.generate_events(point)
