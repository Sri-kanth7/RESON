"""Shared helpers for RESON Phase 2 intelligence tests.

These helpers construct deterministic telemetry so intelligence tests can run
without Supabase, without a configuration file, and without the simulator.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from src.data.schemas import (
    Deployment,
    DeploymentStatus,
    Event,
    EventType,
    Log,
    LogLevel,
    Metric,
)

DEFAULT_START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _supabase_configured() -> bool:
    """Return whether Supabase credentials are present in the environment."""
    return bool(
        os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SECRET_KEY")
    )


#: Marker for live integration tests that need a reachable Supabase project.
#:
#: The test body and its assertions are unchanged; the marker only skips the
#: test when the external service it integrates with is not configured. The
#: skip reason is explicit so the skip is never silent.
requires_supabase = pytest.mark.skipif(
    not _supabase_configured(),
    reason=(
        "Live integration test: requires SUPABASE_URL and "
        "SUPABASE_SECRET_KEY. The offline suite covers the same code paths "
        "using the sqlite and memory backends."
    ),
)


def make_metric(
    timestamp: datetime,
    value: float,
    service: str = "api-service",
    environment: str = "development",
    metric_name: str = "latency_ms",
    unit: str = "ms",
) -> Metric:
    """Build a deterministic metric record."""
    return Metric(
        timestamp=timestamp,
        service=service,
        environment=environment,
        metric_name=metric_name,
        value=value,
        unit=unit,
    )


def make_series(
    values: list[float],
    service: str = "api-service",
    environment: str = "development",
    metric_name: str = "latency_ms",
    start: datetime = DEFAULT_START,
    interval_seconds: int = 10,
) -> list[Metric]:
    """Build a list of metric records at regular intervals."""
    return [
        make_metric(
            timestamp=start + timedelta(seconds=index * interval_seconds),
            value=value,
            service=service,
            environment=environment,
            metric_name=metric_name,
        )
        for index, value in enumerate(values)
    ]


def make_log(
    timestamp: datetime,
    level: LogLevel = LogLevel.INFO,
    service: str = "api-service",
    environment: str = "development",
    message: str = "Request completed",
) -> Log:
    """Build a deterministic log record."""
    return Log(
        timestamp=timestamp,
        service=service,
        environment=environment,
        level=level,
        message=message,
    )


def make_event(
    timestamp: datetime,
    event_type: EventType = EventType.RESTART,
    service: str = "api-service",
    environment: str = "development",
    description: str = "Service restarted",
) -> Event:
    """Build a deterministic event record."""
    return Event(
        timestamp=timestamp,
        service=service,
        environment=environment,
        event_type=event_type,
        description=description,
    )


def make_deployment(
    timestamp: datetime,
    service: str = "api-service",
    environment: str = "development",
    version: str = "2.0.0",
    status: DeploymentStatus = DeploymentStatus.SUCCESS,
) -> Deployment:
    """Build a deterministic deployment record."""
    return Deployment(
        timestamp=timestamp,
        service=service,
        environment=environment,
        version=version,
        status=status,
    )