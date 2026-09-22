"""Tests for Phase 3 domain models and deterministic identity helpers."""

from uuid import UUID

import pytest
from pydantic import ValidationError

from src.data.schemas import EvidenceType
from src.incidents.models import (
    IncidentIntelligenceConfig,
    Signal,
    content_id,
)
from tests.incident_test_helpers import KEY, T0, make_deviation


def test_content_id_is_deterministic():
    first = content_id("chan", UUID("11111111-1111-4111-8111-111111111111"))
    second = content_id("chan", UUID("11111111-1111-4111-8111-111111111111"))
    assert first == second
    assert isinstance(first, UUID)


def test_content_id_changes_with_content():
    a = content_id("chan", UUID("11111111-1111-4111-8111-111111111111"))
    b = content_id("chan", UUID("22222222-2222-4222-8222-222222222222"))
    assert a != b


def test_content_id_mapping_keys_are_sorted():
    left = content_id("p", {"z": 1, "a": 2})
    right = content_id("p", {"a": 2, "z": 1})
    assert left == right


def test_models_are_immutable():
    signal = Signal(
        source_id=UUID("11111111-1111-4111-8111-111111111111"),
        source_type=EvidenceType.METRIC,
        timestamp=T0,
        service="api-service",
        environment="development",
        description="metric evidence",
        strength=1.0,
    )
    with pytest.raises(ValidationError):
        signal.strength = 0.5


def test_signal_rejects_extra_fields():
    with pytest.raises(ValidationError):
        Signal(
            source_id=UUID("11111111-1111-4111-8111-111111111111"),
            source_type=EvidenceType.METRIC,
            timestamp=T0,
            service="api-service",
            environment="development",
            description="metric evidence",
            strength=1.0,
            unexpected=True,
        )


def test_incident_config_defaults_are_bounded_and_sensible():
    config = IncidentIntelligenceConfig()
    assert config.correlation_window_seconds == 600.0
    assert config.deployment_window_seconds == 300.0
    assert 0.0 <= config.minimum_correlation_strength <= 1.0
    assert config.incident_gap_seconds > 0.0


def test_signal_from_deviation_carries_traceability():
    deviation = make_deviation(10, seed="trace-1")
    signal = Signal(
        source_id=deviation.record_id,
        source_type=EvidenceType.METRIC,
        timestamp=deviation.timestamp,
        service=KEY.service,
        environment=KEY.environment,
        description="metric evidence",
        strength=1.0,
        trigger=True,
        related_keys=[KEY],
        source_record_ids=[deviation.record_id],
    )
    assert signal.source_id == deviation.record_id
    assert signal.source_record_ids == [deviation.record_id]
    assert signal.trigger is True