"""Phase 2 domain models for RESON behavioral intelligence."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class BehaviorState(str, Enum):
    """Deterministic behavior classification for an analysis window."""

    NORMAL = "normal"
    DEVIATED = "deviated"
    CHANGING = "changing"


class DeviationStatus(str, Enum):
    """Classification of one observation relative to its historical baseline."""

    NORMAL = "normal"
    ANOMALOUS = "anomalous"
    ZERO_BASELINE = "zero_baseline"
    CONSTANT_BASELINE = "constant_baseline"
    INSUFFICIENT_REFERENCE = "insufficient_reference"


class IntelligenceModel(BaseModel):
    """Immutable, strict base model for intelligence data contracts."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class BaselineKey(IntelligenceModel):
    """Identity of a metric baseline."""

    service: str
    environment: str
    metric_name: str


class BaselineStatistics(IntelligenceModel):
    """Statistics describing a historical baseline."""

    count: int
    mean: float | None = None
    std: float | None = None
    min: float | None = None
    max: float | None = None
    p25: float | None = None
    p50: float | None = None
    p75: float | None = None


class HistoricalBaseline(IntelligenceModel):
    """A historical baseline for one metric identity over an explicit window."""

    key: BaselineKey
    start_time: datetime
    end_time: datetime
    statistics: BaselineStatistics
    source_record_ids: list[UUID] = Field(default_factory=list)
    explanation: str


class MetricDeviation(IntelligenceModel):
    """Deviation of one observation relative to its historical baseline."""

    record_id: UUID
    timestamp: datetime
    key: BaselineKey
    value: float
    baseline_mean: float | None = None
    absolute_deviation: float | None = None
    relative_deviation: float | None = None
    z_score: float | None = None
    status: DeviationStatus

    @property
    def is_anomalous(self) -> bool:
        """Return whether this deviation exceeds the configured threshold."""
        return self.status == DeviationStatus.ANOMALOUS


class ChangePoint(IntelligenceModel):
    """A sustained period of anomalous behavior across consecutive batches."""

    start_time: datetime
    end_time: datetime
    consecutive_batches: int
    affected_metric_keys: list[BaselineKey] = Field(default_factory=list)
    source_record_ids: list[UUID] = Field(default_factory=list)
    explanation: str


class MetricBehavior(IntelligenceModel):
    """Behavioral summary for a single metric within a window."""

    key: BaselineKey
    observation_count: int
    mean: float | None = None
    std: float | None = None
    min: float | None = None
    max: float | None = None
    anomaly_count: int = 0
    peak_abs_z: float | None = None


class BehaviorFingerprint(IntelligenceModel):
    """Deterministic structured summary of system behavior for a window."""

    start_time: datetime
    end_time: datetime
    services: list[str]
    environment: str | None = None
    metric_behaviors: list[MetricBehavior] = Field(default_factory=list)
    log_summary: dict[str, int] = Field(default_factory=dict)
    event_summary: dict[str, int] = Field(default_factory=dict)
    deployment_count: int = 0
    change_point_count: int = 0
    behavior_state: BehaviorState
    source_record_ids: list[UUID] = Field(default_factory=list)


class MetricContribution(IntelligenceModel):
    """Per-metric contribution to the Behavior Shift Score."""

    key: BaselineKey
    anomaly_rate: float
    peak_magnitude: float
    contribution: float


class BehaviorShift(IntelligenceModel):
    """Transparent, bounded behavioral deviation magnitude for a window."""

    score: float
    contributions: list[MetricContribution] = Field(default_factory=list)
    change_detected: bool = False
    explanation: str


class ServiceBehavior(IntelligenceModel):
    """Behavioral analysis for one service."""

    service: str
    fingerprint: BehaviorFingerprint
    shift: BehaviorShift


class BehaviorAnalysis(IntelligenceModel):
    """Complete Phase 2 analysis comparing a window against history."""

    historical_window_start: datetime | None = None
    historical_window_end: datetime | None = None
    analysis_window_start: datetime | None = None
    analysis_window_end: datetime | None = None
    service_results: list[ServiceBehavior] = Field(default_factory=list)
    overall_score: float = 0.0
    changed_services: list[str] = Field(default_factory=list)


class IntelligenceConfig(IntelligenceModel):
    """Immutable, independently constructable configuration for Phase 2."""

    min_observations_per_baseline: int = Field(default=2, ge=1)
    z_score_threshold: float = Field(default=3.0, gt=0.0)
    relative_deviation_threshold: float = Field(default=0.25, gt=0.0)
    consecutive_anomalous_batches: int = Field(default=2, ge=1)
    max_z_magnitude: float = Field(default=4.0, gt=0.0)
    max_gap_seconds: float = Field(default=60.0, gt=0.0)