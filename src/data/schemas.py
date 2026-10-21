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
# Source attribution
# ---------------------------------------------------------------------------

SOURCE_WINDOWS_LOCAL = "windows_local"
"""Real host telemetry collected on a Windows computer."""

SOURCE_LINUX_LOCAL = "linux_local"
"""Real host telemetry collected on a Linux computer."""

SOURCE_MACOS_LOCAL = "macos_local"
"""Real host telemetry collected on a macOS computer."""

SOURCE_HOSTED_COLLECTOR = "hosted_collector"
"""Telemetry sent to a hosted dashboard by a separately running local collector."""

SOURCE_SIMULATION = "simulation"
"""Synthetic / generated demonstration telemetry."""

SOURCE_IMPORTED = "imported"
"""Historical data imported from an external source."""

SOURCE_UNKNOWN = "unknown"
"""Provenance cannot be established (for example legacy records with no source)."""


#: Every source value RESON understands. Used for validation and documentation.
SOURCE_VALUES = (
    SOURCE_WINDOWS_LOCAL,
    SOURCE_LINUX_LOCAL,
    SOURCE_MACOS_LOCAL,
    SOURCE_HOSTED_COLLECTOR,
    SOURCE_SIMULATION,
    SOURCE_IMPORTED,
    SOURCE_UNKNOWN,
)


def classify_source(source: str | None) -> str:
    """Return the reliable source classification for a telemetry record.

    ``None`` and any unrecognized value are classified as
    :data:`SOURCE_UNKNOWN`. A record is never inferred to be real merely
    because it carries a plausible timestamp or value.
    """
    if source is None:
        return SOURCE_UNKNOWN
    if source in SOURCE_VALUES:
        return source
    return SOURCE_UNKNOWN


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
    """A numerical system measurement.

    ``source`` identifies the telemetry source (for example ``"windows_local"``
    for real host telemetry collected by :mod:`src.data.windows_collector`, or
    unset for simulated/fixture telemetry) so real measurements remain
    distinguishable from simulated data throughout the pipeline and storage.
    ``host_id`` is the stable reference of the computer the observation was
    collected on. Both are optional and default to ``None`` so existing
    simulated and historical records validate unchanged.
    """

    metric_name: str = Field(min_length=1)
    value: float
    unit: str = Field(min_length=1)
    source: str | None = None
    host_id: str | None = None


class Log(TelemetryBase):
    """An application or system log record.

    ``source`` and ``host_id`` identify the telemetry provenance and the
    computer the record was collected on. Both default to ``None`` so existing
    simulated and historical records validate unchanged; a ``None`` source is
    classified as :data:`SOURCE_UNKNOWN` at read/query time.
    """

    level: LogLevel
    message: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    source: str | None = None
    host_id: str | None = None


class Event(TelemetryBase):
    """A significant system event.

    ``source`` and ``host_id`` identify the telemetry provenance and the
    computer the record was collected on. Both default to ``None`` so existing
    simulated and historical records validate unchanged; a ``None`` source is
    classified as :data:`SOURCE_UNKNOWN` at read/query time.
    """

    event_type: EventType
    description: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    source: str | None = None
    host_id: str | None = None


class Deployment(TelemetryBase):
    """A service deployment event.

    ``source`` and ``host_id`` identify the telemetry provenance and the
    computer the record was collected on. Both default to ``None`` so existing
    simulated and historical records validate unchanged; a ``None`` source is
    classified as :data:`SOURCE_UNKNOWN` at read/query time.
    """

    version: str = Field(min_length=1)
    status: DeploymentStatus
    metadata: dict[str, Any] = Field(default_factory=dict)
    source: str | None = None
    host_id: str | None = None


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------


class Incident(RESONModel):
    """A correlated period of abnormal system behavior.

    ``source`` records the telemetry source the incident was detected from
    and ``host_id`` the machine it was detected on, so an incident can be
    traced back to the provenance of the observations behind it. Both
    default to ``None`` (classified as :data:`SOURCE_UNKNOWN` at read time)
    for incidents created before provenance was recorded.
    """

    id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=1)
    start_time: datetime
    end_time: datetime | None = None
    severity: IncidentSeverity
    status: IncidentStatus
    scenario: str | None = None
    affected_services: list[str] = Field(default_factory=list)
    source: str | None = None
    host_id: str | None = None

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
    """A relationship connecting two pieces of system evidence.

    ``source`` records the telemetry source the supporting observation came
    from, so an evidence link keeps the provenance of the record it points
    at. Both default to ``None`` (classified as :data:`SOURCE_UNKNOWN`).
    """

    id: UUID = Field(default_factory=uuid4)

    source_id: UUID
    source_type: EvidenceType

    target_id: UUID
    target_type: EvidenceType

    relationship_type: RelationshipType

    timestamp: datetime

    strength: float = Field(ge=0.0, le=1.0)

    explanation: str = Field(min_length=1)

    source: str | None = None
    host_id: str | None = None
