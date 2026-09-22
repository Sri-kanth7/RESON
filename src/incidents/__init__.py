"""RESON incident intelligence (Phase 3).

Deterministic, explainable, traceable, offline-testable incident detection
and analysis built on top of the Phase 2 behavioral intelligence pipeline.

Public surface
--------------
- ``IncidentIntelligenceService`` — thin orchestrator (correlation, detection,
  lifecycle, timeline, historical similarity, persistence via ``Storage``).
- ``IncidentIntelligenceConfig``, ``Signal``, ``CorrelationCandidate``,
  ``IncidentSimilarity``, ``HistoricalIncidentMatch``, ``IncidentResearch``,
  ``IncidentIntelligenceResult`` — Phase 3 domain models.
- ``SignalBuilder`` / ``CorrelationEngine`` — evidence correlation.
- ``IncidentDetector`` / ``SeverityPolicy`` — incident detection and the
  deterministic severity policy.
- ``IncidentLifecycle`` — deterministic status transitions.
- ``TimelineBuilder`` — chronological, stable-ID-ordered timelines.
- ``IncidentMatcher`` — historical incident similarity and matching.
"""

from src.incidents.correlation import CorrelationEngine, SignalBuilder
from src.incidents.detector import IncidentDetector, SeverityPolicy
from src.incidents.lifecycle import IncidentLifecycle
from src.incidents.models import (
    CorrelationCandidate,
    HistoricalIncidentMatch,
    IncidentHistory,
    IncidentIntelligenceConfig,
    IncidentIntelligenceResult,
    IncidentResearch,
    IncidentSimilarity,
    Signal,
)
from src.incidents.service import IncidentIntelligenceService
from src.incidents.similarity import IncidentMatcher
from src.incidents.timeline import TimelineBuilder

__all__ = [
    "CorrelationCandidate",
    "CorrelationEngine",
    "HistoricalIncidentMatch",
    "IncidentDetector",
    "IncidentHistory",
    "IncidentIntelligenceConfig",
    "IncidentIntelligenceResult",
    "IncidentIntelligenceService",
    "IncidentLifecycle",
    "IncidentMatcher",
    "IncidentResearch",
    "IncidentSimilarity",
    "SeverityPolicy",
    "Signal",
    "SignalBuilder",
    "TimelineBuilder",
]