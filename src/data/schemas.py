"""
RESON Data Contract v1.

This module defines the canonical data models used throughout RESON.
All telemetry, system entities, incidents, and evidence should conform
to these models before entering the rest of the system.
"""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class Environment(str, Enum):
    """Supported system environments."""

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class LogLevel(str, Enum):
    """Application log severity."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class EventType(str, Enum):
    """System-level events that can occur."""

    DEPLOYMENT = "deployment"
    RESTART = "restart"
    CONFIG_CHANGE = "config_change"
    SERVICE_FAILURE = "service_failure"
    DATABASE_PRESSURE = "database_pressure"
    SCALE_UP = "scale_up"
    SCALE_DOWN = "scale_down"
    NETWORK_DEGRADATION = "network_degradation"


class DeploymentStatus(str, Enum):
    """Deployment outcome."""

    SUCCESS = "success"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class IncidentSeverity(str, Enum):
    """Incident severity."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IncidentStatus(str, Enum):
    """Incident lifecycle state."""

    OPEN = "open"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"


class EvidenceType(str, Enum):
    """Types of evidence that can support an investigation."""

    METRIC = "metric"
    LOG = "log"
    EVENT = "event"
    DEPLOYMENT = "deployment"
    INCIDENT = "incident"


class RelationshipType(str, Enum):
    """Relationships between evidence entities."""

    TEMPORAL = "temporal"
    CORRELATION = "correlation"
    SERVICE_DEPENDENCY = "service_dependency"
    EVENT_SEQUENCE = "event_sequence"
    HISTORICAL_SIMILARITY = "historical_similarity"


# ---------------------------------------------------------------------------
# Shared base model
# ---------------------------------------------------------------------------


class RESONModel(BaseModel):
    """Base model used by all RESON data contracts."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        use_enum_values=True,
    )


class TelemetryBase(RESONModel):
    """Fields shared by telemetry records."""

    id: UUID = Field(default_factory=uuid4)
    timestamp: datetime
    service: str = Field(min_length=1)
    environment: Environment


# ---------------------------------------------------------------------------
# System entities
# ---------------------------------------------------------------------------


class Service(RESONModel):
    """A service participating in the monitored system."""

    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1)
    environment: Environment
    version: str = Field(min_length=1)
    description: str | None = None


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------


class Metric(TelemetryBase):
    """A numerical system measurement."""

    metric_name: str = Field(min_length=1)
    value: float
    unit: str = Field(min_length=1)


class Log(TelemetryBase):
    """An application or system log record."""

    level: LogLevel
    message: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Event(TelemetryBase):
    """A significant system event."""

    event_type: EventType
    description: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Deployment(TelemetryBase):
    """A service deployment event."""

    version: str = Field(min_length=1)
    status: DeploymentStatus
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------


class Incident(RESONModel):
    """A correlated period of abnormal system behavior."""

    id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=1)
    start_time: datetime
    end_time: datetime | None = None
    severity: IncidentSeverity
    status: IncidentStatus
    scenario: str | None = None
    affected_services: list[str] = Field(default_factory=list)

    @property
    def duration_seconds(self) -> float | None:
        """Return incident duration in seconds when it has ended."""

        if self.end_time is None:
            return None

        return (self.end_time - self.start_time).total_seconds()


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


class Evidence(RESONModel):
    """A relationship connecting two pieces of system evidence."""

    id: UUID = Field(default_factory=uuid4)

    source_id: UUID
    source_type: EvidenceType

    target_id: UUID
    target_type: EvidenceType

    relationship_type: RelationshipType

    timestamp: datetime

    strength: float = Field(ge=0.0, le=1.0)

    explanation: str = Field(min_length=1)
