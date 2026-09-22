# RESON

Historical System Behavior Intelligence & Incident Analysis Platform.

RESON turns raw system telemetry (metrics, logs, events, deployments) into a
deterministic, explainable, and traceable picture of current system behavior
compared against its own recent history.

## Phases

Implemented phases:

- **Phase 1 — Foundational Data Platform**: canonical data contracts,
  deterministic telemetry simulator, Supabase storage layer, and bulk
  telemetry ingestion.
- **Phase 2 — Working Intelligence**: normalization, historical baselines,
  deviation detection, change-point detection, Behavior Fingerprints, and the
  Behavior Shift Score, orchestrated through the
  `BehaviorIntelligenceService`.
- **Phase 3 — Incident Intelligence**: deterministic evidence correlation,
  incident detection with an explainable severity policy, incident lifecycle,
  timeline/evidence-chain construction, and historical incident similarity —
  orchestrated through the `IncidentIntelligenceService` (the System Machine
  unit `IncidentResearch`).

Future phases (LLM-assisted incident analysis, Streamlit UI, etc.) are not yet
implemented and are not claimed here.

## Package Layout

```
src/
  data/
    schemas.py            Data contracts (Metric, Log, Event, Deployment, ...)
    generator.py          Deterministic telemetry generation
    simulator.py          Scenario timelines (normal, latency_spike, ...)
    supabase_client.py    Supabase client factory
    supabase_storage.py   Bulk storage / retrieval
    orchestrator.py       Ingestion and retrieval orchestration
  common/
    config.py             Environment-based configuration (.env)
  preprocessing/
    normalizer.py         Group / order / window raw telemetry
  intelligence/
    models.py             Domain models and IntelligenceConfig
    baseline.py           BaselineBuilder (historical statistics)
    deviation.py          DeviationDetector (z-score + fallback rules)
    change_detection.py   ChangeDetector (sustained anomaly runs)
    fingerprint.py        FingerprintBuilder (per-service behavior snapshot)
    shift.py              BehaviorShiftCalculator (bounded shift score)
    service.py            BehaviorIntelligenceService (pipeline orchestration)
  incidents/
    models.py             IncidentIntelligenceConfig and Phase 3 domain models
    correlation.py        SignalBuilder + CorrelationEngine
    detector.py           IncidentDetector + SeverityPolicy
    lifecycle.py          IncidentLifecycle
    timeline.py           TimelineBuilder
    evidence.py           Evidence-chain construction and validation
    similarity.py         IncidentMatcher (historical incident matching)
    service.py            IncidentIntelligenceService (pipeline orchestration)
```

## Quick Start

```bash
python -m pip install -r requirements.txt
python -m pytest -q
```

`IntelligenceConfig` carries the tunable thresholds used across Phase 2:

- `min_observations_per_baseline` (default 2) — history a baseline needs
- `z_score_threshold` (default 3.0) — z-score anomaly threshold
- `relative_deviation_threshold` (default 0.25) — fallback for constant baselines
- `consecutive_anomalous_batches` (default 2) — sustained-run requirement
- `max_z_magnitude` (default 4.0) — clamp for shift peak magnitude
- `max_gap_seconds` (default 60.0) — tolerated gap within a sustained run

Phase 2 does **not** depend on Supabase, Streamlit, network access, or
`get_config()`; it is fully deterministic and offline-testable.

## Incident Intelligence Pipeline

Phase 3 (`src/incidents/`) turns Phase 2 evidence (deviations, change points,
Behavior Shift Score) and telemetry (logs, events, deployments) into incidents
with a full evidence trail. It shares the same design constraints as Phase 2:
deterministic (content-derived UUIDs for incident/evidence ids), explainable,
reusing the existing `Incident`/`Evidence` contracts, and offline-testable.
It never claims causality — edges are "correlated with" / "temporally
associated with" only.

`IncidentIntelligenceConfig` carries the Phase 3 thresholds:

- `correlation_window_seconds` (default 600.0) — temporal proximity window
- `deployment_window_seconds` (default 300.0) — deployment impact window
- `minimum_correlation_strength` (default 0.3) — accepted-edge threshold
- `incident_gap_seconds` (default 120.0) — adjacent-cluster merge tolerance
- `severity_shift_threshold` (default 0.5) — behavior shift severity bound

Pipeline:

1. **Projection** — `SignalBuilder` normalizes evidence into `Signal` nodes:
   anomalous deviations and change points are triggers, ERROR/CRITICAL logs and
   escalation events are triggers, failed/rolled-back deployments are triggers;
   successful deployments and non-escalation events are context. Normal
   deviations stay out of the incident corpus (they become lifecycle recovery
   evidence).
2. **Correlation** — `CorrelationEngine` evaluates every signal pair once and
   computes a bounded strength from documented factors: temporal proximity
   (0.30), same service (0.25), same environment (0.15), metric overlap (0.15),
   deployment proximity (0.10), event link (0.05). A pair is materialized as
   `Evidence` (`EVENT_SEQUENCE` / `CORRELATION` / `TEMPORAL`) only when its
   strength is at or above `minimum_correlation_strength` **and** it carries at
   least one time-bounded factor (positive temporal proximity, deployment
   proximity within its window, or event link). Static context alone (shared
   service/environment/metric key) never associates evidence that is not
   temporally close, so far-apart alarms never merge into a day-long incident.
3. **Detection** — `IncidentDetector` builds connected components over accepted
   edges, merges adjacent components within `incident_gap_seconds`, and emits an
   incident only for clusters containing a trigger signal. `SeverityPolicy`
   escalates from LOW by verifiable evidence: behavior shift magnitude, number
   of affected services, sustained change-point evidence, and critical evidence
   (ERROR/CRITICAL logs, escalation events, failed deployments, or deployment
   events with same-service anomalous metrics). Every decision is recorded in a
   `severity_explanation`.
4. **Lifecycle** — `IncidentLifecycle` keeps open incidents OPEN with
   `end_time=None` until positive recovery evidence: a SUCCESS deployment for
   an affected service strictly after the incident end, a ROLLED_BACK
   deployment at or after the incident end (the rollback is the mitigation
   milestone), or a NORMAL deviation for an affected metric key after its last
   anomalous one. A deployment never resolves an incident on its own — it must
   be positive evidence, and a later anomalous signal keeps the incident open.
   Recovery is never inferred from silence.
5. **Timeline / evidence chain** — `TimelineBuilder` orders the
   Incident → Signal `Evidence` by `(timestamp, source_id, target_id)`; each
   entry stays traceable to the raw record ids that produced the signal.
6. **Historical similarity** — `IncidentMatcher` scores current incidents
   against `IncidentHistory` candidates using documented `[0,1]` factors
   (service overlap, metric similarity, event-sequence LCS, deployment
   relationship, temporal similarity, scenario match), excludes the current
   incident, preserves historical ids and timelines, and ranks deterministically.
   Absent evidence is an explicit penalty (scored 0.0 with its full weight), so 1.0
   is reachable only when every factor is available and identical; two
   deployment/scenario-free incidents of identical structure cap at 0.80. The
   penalty affects the magnitude, not the deterministic ranking. Similarity is a
   structural comparison, not a probability.

### Orchestration

`IncidentIntelligenceService` composes the pipeline:

- `analyze(...)` — run correlation, detection, lifecycle, and timeline for one
  window, returning an `IncidentIntelligenceResult` of `IncidentResearch`
  units (the System Machine row: incident, signals, timeline, severity
  explanation, historical matches).
- `derive_evidence(metrics, baselines, window_start=None, window_end=None, intelligence_config=None)` — bridge that reruns the unmodified
  Phase 2 `DeviationDetector`/`ChangeDetector` to materialize the deviation
  and change-point streams `BehaviorAnalysis` keeps internal. For results
  consistent with a `BehaviorAnalysis`, the same baselines, thresholds
  (`intelligence_config`), and analysis window must be used; metrics are
  windowed with `slice_series` (half-open `[start, end)`) exactly like Phase 2.
- `find_historical_matches` / `with_history` — rank and attach historical
  matches to a research unit.
- `persist(result)` / `historical_candidates(...)` — persist incidents and
  evidence through the `Storage` abstraction only (never Supabase directly);
  `historical_candidates` reloads incidents plus their timeline evidence.

Phase 3 does **not** depend on Supabase, Streamlit, network access, an LLM, or
`get_config()`; it is fully deterministic and offline-testable.

## Preprocessing and Normalization

- `group_metrics(metrics)` groups metric records by
  `(service, environment, metric_name)`, orders each series by timestamp (then
  record id for stability), and never mutates inputs.
- `slice_series(series, start, end)` returns the observations in the half-open
  window `[start, end)`.

## Behavior Intelligence Pipeline

1. **Historical baseline** — `BaselineBuilder.build_baselines` computes mean,
   sample standard deviation, min/max, and interpolated percentiles
   (10th/25th/50th/75th/90th) per metric key. Below
   `min_observations_per_baseline` no baseline is produced.
2. **Deviation detection** — `DeviationDetector` flags each observation by
   comparing current values against the historical baseline:
   - variable baseline → z-score against `z_score_threshold`
   - constant baseline → relative deviation against
     `relative_deviation_threshold`
   - zero-only baseline → absolute deviation
   - missing / insufficient baseline → `INSUFFICIENT_REFERENCE` status
     (never an exception)
3. **Change-point detection** — `ChangeDetector` groups deviations by
   timestamp and flags metrics that remain anomalous across at least
   `consecutive_anomalous_batches` timestamps (allowing small gaps bounded by
   `max_gap_seconds`).
4. **Behavior Fingerprint** — `FingerprintBuilder` produces a per-service,
   per-window snapshot: per-metric behavior counts/statistics, peak absolute
   z-score, log/event summaries, deployment and change-point counts, and the
   source record ids that produced the signal (full traceability).
5. **Behavior Shift Score** — `BehaviorShiftCalculator` returns a value in
   `[0, 1]`: the mean per-metric contribution, where each contribution is
   `anomaly_rate × peak_magnitude` (anomaly rate = share of anomalous
   observations; peak magnitude = bounded maximum deviation, clamped at
   `max_z_magnitude`).

The Behavior Shift Score is an **explainable, bounded measure of deviation
evidence**. It is not a probability and makes no attribution claim about
causes.

### Orchestration

`BehaviorIntelligenceService` composes the pipeline:

- `build_baselines(metrics, window_start, window_end)` — build history baselines.
- `analyze(baselines, metrics, logs, events, deployments, window_start,
  window_end)` — analyze a current window, returning a `BehaviorAnalysis`
  with one `ServiceBehavior` per affected service, `overall_score`, and
  `changed_services`.
- `analyze_history(...)` — convenience wrapper that builds history baselines
  then analyzes a current window.

When `window_end` is omitted on `analyze`, the window closes at the final
observed timestamp (plus one microsecond), keeping the last observation
included.

## Simulator-Driven Validation

`tests/test_intelligence_scenarios.py` uses `SystemSimulator` (a fixed seed)
as a *validation source*, not as logic inside the intelligence modules:

- `normal` / `deployment` timelines stay near baseline → score `0.0`,
  `changed_services` empty.
- `latency_spike`, `error_spike`, `database_pressure`,
  `deployment_regression` produce deviation evidence and a positive shift.
- Sustained anomalies form change points; an isolated spike does not.