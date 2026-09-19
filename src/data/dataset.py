"""Historical telemetry dataset builder for RESON."""

from dataclasses import dataclass
from datetime import datetime

from src.data.simulator import SimulationBatch, SystemSimulator


@dataclass(frozen=True)
class HistoricalDataset:
    """A deterministic collection of simulated historical telemetry."""

    batches: list[SimulationBatch]

    @property
    def records(self):
        """Return all telemetry records in chronological order."""
        records = []

        for batch in self.batches:
            records.extend(batch.records())

        return records

    @property
    def batch_count(self) -> int:
        """Return the number of simulation batches."""
        return len(self.batches)

    @property
    def record_count(self) -> int:
        """Return the total number of telemetry records."""
        return len(self.records)

    @property
    def scenarios(self) -> list[str]:
        """Return the scenario associated with each batch."""
        return [batch.scenario for batch in self.batches]


class HistoricalDatasetBuilder:
    """Build deterministic historical telemetry datasets."""

    DEFAULT_SCENARIOS = (
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
    )

    def __init__(
        self,
        simulator: SystemSimulator | None = None,
    ) -> None:
        self._simulator = simulator or SystemSimulator()

    def build(
        self,
        start_time: datetime,
        scenarios: tuple[str, ...] | list[str] | None = None,
    ) -> HistoricalDataset:
        """Build a deterministic historical dataset."""
        selected_scenarios = (
            tuple(scenarios)
            if scenarios is not None
            else self.DEFAULT_SCENARIOS
        )

        if not selected_scenarios:
            raise ValueError("At least one scenario is required.")

        batches = self._simulator.generate_scenario_timeline(
            start_time=start_time,
            scenarios=selected_scenarios,
        )

        return HistoricalDataset(batches=batches)
