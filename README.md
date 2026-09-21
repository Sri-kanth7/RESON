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

Future phases (correlation, LLM-assisted incident analysis, etc.) are not yet
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