# RESON Architecture

This document describes how RESON actually works: the entry points, the data
flow from collection to rendering, the boundaries between components, and the
places where the design deliberately refuses to guess.

---

## 1. Entry points

| Entry point | Purpose |
|---|---|
| `app/streamlit_app.py` | Streamlit dashboard entry point. Also the deployment entry point for Streamlit Community Cloud and similar platforms. |
| `python -m src.data.agent` | Standalone collector agent. A normal OS process that continuously collects real telemetry and ingests it into the configured backend. Independent of the dashboard and of any browser session. |
| `src/orchestrator.py` → `create_application()` | Composition root used by both the dashboard and the agent. Builds configuration, storage, and ingestion. |

---

## 2. Data flow

```
   platform collector                ingestion                persistence
   (src/data/collector.py)      (src/data/ingestion.py)   (src/data/sqlite_storage.py
   + *_collector.py                  │                       / memory_storage.py
            │                        │                       / supabase_storage.py)
            │  HostSample            │                             │
            │  ProcessSample         │                             │
            │  DiskSample            │                             │
            │  NetworkSample         │                             │
            │  BatterySample         │                             │
            │  SensorSample          │                             │
            ▼                        ▼                             ▼
   collection_to_metrics() ──► Metric records ──► stored rows
   (source + host_id on           (service, environment,           │
    every record)                 metric_name, value,              │
                                   unit, timestamp)                │
                                                                   ▼
                                                     src/intelligence/  (baselines,
                                                                       deviations,
                                                                       change points,
                                                                       fingerprints,
                                                                       shift score)
                                                                   │
                                                                   ▼
                                                     src/incidents/    (correlation,
                                                                       detection, severity,
                                                                       lifecycle, timeline,
                                                                       evidence chain,
                                                                       historical similarity)
                                                                   │
                                                                   ▼
                                          app/services/backend.py  (UI adapters)
                                                                   │
                                                                   ▼
                                          app/pages/*.py  ──►  Streamlit rendering
```

Every arrow preserves the telemetry `source` and `host_id`. Provenance is
carried from the collector all the way to the rendered card.

---

## 3. Layers and boundaries

### 3.1 Collection (`src/data/collector.py`, `src/data/*_collector.py`)

* `Collector` — the abstract interface. `BaseCollector` implements the shared
  start/stop lifecycle, the background loop, and health reporting.
* Platform implementations: `WindowsTelemetryCollector`,
  `LinuxTelemetryCollector`, `MacOSTelemetryCollector`. Selected by
  `src/data/collector_factory.py`, which imports each platform module lazily
  so a missing or broken platform module cannot affect another operating
  system.
* Collection is strictly separated from ingestion, storage, and analysis. A
  collector produces structured samples and nothing else.
* No background loop starts on import. `start()` is explicit and idempotent:
  calling it on a running collector is a no-op, so a Streamlit rerun cannot
  create a duplicate loop.
* Unavailable data is reported as `None` / an empty tuple together with
  explicit capability metadata. Nothing is fabricated.

### 3.1a System-event collection (`src/data/system_event_log.py`, `src/data/*_event_log.py`, `src/data/linux_journal.py`, `src/data/macos_unified_log.py`)

* `EventLogReader` — the reader seam. Each platform implements it over its
  own event log: the Windows Event Log (`pywin32`, falling back to
  `wevtutil`), the systemd journal (`journalctl`), and the macOS unified log
  (`log show`).
* `SystemEventCollector` — the shared collector. It enforces the same
  guarantees on every platform: an explicit allowlist, a bounded time window,
  a maximum number of records, stable record identifiers, checkpoint
  de-duplication, and honest error reporting (a log that cannot be read is
  never presented as "no events").
* The platform readers are separate modules and are imported lazily, so one
  platform's tooling can never break another operating system.
* Collected events are ingested through the ordinary `TelemetryIngestionService`
  and therefore carry the same `source` and `host_id` provenance as every
  other record.

### 3.2 Telemetry model (`src/data/schemas.py`)

| Model | Purpose | Provenance fields |
|---|---|---|
| `Metric` | A numerical measurement | `source`, `host_id` |
| `Log` | An application/system log record | `source`, `host_id` |
| `Event` | A significant system event | `source`, `host_id` |
| `Deployment` | A service deployment | `source`, `host_id` |
| `Incident` | A correlated period of abnormal behavior | (derived; see note) |
| `Evidence` | A relationship between two entities | (derived) |

Documented source values: `windows_local`, `linux_local`, `macos_local`,
`hosted_collector`, `simulation`, `imported`, `unknown`.
`classify_source()` maps `None` and any unrecognized value to `unknown`; a
record is never inferred to be real merely because it has a plausible
timestamp or value.

> Note: `Incident` and `Evidence` are derived records and carry their own
> `source` and `host_id`. The provenance flows
> `Metric/Log/Event/Deployment` → `MetricObservation` →
> `MetricDeviation`/`ChangePoint` → `Signal` → `Evidence` → `Incident`.
> An incident records the provenance of its supporting observations **only
> when they all agree**; a cluster that mixes sources is left unattributed
> rather than attributed to an arbitrary one. The dashboard filters the
> incident list by the source of the active operating mode, and shows the
> source on each incident and evidence link.

### 3.3 Ingestion (`src/data/ingestion.py`)

`TelemetryIngestionService` routes records to the `Storage` abstraction,
either one at a time or in type-specific batches. It performs no analysis.

### 3.4 Persistence

| Backend | Module | Purpose |
|---|---|---|
| SQLite | `src/data/sqlite_storage.py` | Persistent local storage. WAL journal mode, a busy timeout, a versioned `schema_migrations` table, and non-destructive migrations. |
| In-memory | `src/data/memory_storage.py` | Temporary, non-persistent. Used by tests and offline development. |
| Supabase | `src/data/supabase_storage.py` | Persistent cloud storage. Requires credentials. |

All three implement the same `Storage` contract with identical filter
semantics: inclusive time bounds, plain-string equality filters, and
timestamp-ordered results.

Source-aware filtering is available on every telemetry read:
`get_metrics`, `get_logs`, `get_events`, and `get_deployments` all accept
`source` and `host_id`.

### 3.5 Intelligence (`src/intelligence/`)

1. **Baseline** — mean, sample standard deviation, min/max, and interpolated
   percentiles per metric identity over an explicit window.
2. **Deviation** — z-score against a variable baseline, relative deviation
   against a constant baseline, absolute deviation against a zero-only
   baseline, and an explicit `INSUFFICIENT_REFERENCE` status when there is
   not enough history. Never an exception.
3. **Change point** — a metric that stays anomalous across consecutive
   batches, tolerating small gaps.
4. **Fingerprint** — a per-service, per-window summary with full traceability
   back to the source record ids.
5. **Behavior Shift Score** — a bounded `[0, 1]` magnitude of deviation
   evidence. It is not a probability and makes no causal claim.

### 3.6 Incident intelligence (`src/incidents/`)

1. **Projection** — evidence is normalized into `Signal` nodes.
2. **Correlation** — every signal pair is evaluated once; a pair is
   materialized as `Evidence` only when its strength clears the threshold
   **and** it carries at least one time-bounded factor. Static context alone
   never associates evidence that is not temporally close.
3. **Detection** — connected components over accepted edges, merged within
   the incident gap, emitted only for clusters containing a trigger signal.
   `SeverityPolicy` records why each severity was chosen.
4. **Lifecycle** — an incident stays open until positive recovery evidence
   arrives. Recovery is never inferred from silence.
5. **Timeline / evidence chain** — every entry traces back to the raw record
   ids that produced it.
6. **Historical similarity** — a deterministic structural comparison, never a
   probability.

### 3.7 Presentation (`app/`)

* `app/streamlit_app.py` — shell, navigation, backend selection, monitoring
  mode selection, collector controls.
* `app/services/backend.py` — UI-facing adapters. It contains no
  intelligence of its own; every analytical result comes from `src/`.
* `app/components/ui.py` — presentation helpers only.
* `app/theme.py` — the visual design system (CSS + formatting helpers).
* `app/pages/` — one module per page.

Pages are thin: they obtain already-computed values and render them. No page
runs an analysis, and no page invents a figure.

---

## 4. Operating modes

The dashboard's mode is explicit and is shown prominently on every page.

| Mode | Source in view | Meaning |
|---|---|---|
| Local monitoring | the platform's local source (`windows_local` / `linux_local` / `macos_local`) | A collector on this computer contributes real telemetry. |
| Demonstration | `simulation` | Clearly labeled synthetic data produced by the deterministic simulator. |
| Hosted monitoring | `hosted_collector` | Telemetry received from a separately running collector. Opening the page never observes the visitor's computer. |
| Imported history | `imported` | Historical records from an external source. Never presented as live. |

A mode whose source holds no data reports that fact and shows nothing in its
place. RESON never substitutes another mode's data.

---

## 5. Collector lifecycle

```
        ┌──────────┐  start()   ┌──────────┐  cycle   ┌──────────┐
        │ STOPPED  │──────────▶ │ RUNNING  │────────▶ │ RUNNING  │
        └──────────┘            └──────────┘          └──────────┘
              ▲                     │                        │
              │        stop()       │        failure        │
              └─────────────────────┴────────────────────────┘
                                    ▼
                              ┌──────────┐
                              │  ERROR   │
                              └──────────┘
```

* `start()` primes the counters once and discards the meaningless first
  reading, then runs the loop in a daemon thread.
* The loop publishes each completed cycle to the caller's callback. A failing
  callback sets the error state instead of killing the collector silently.
* `stop()` clears the stop event, joins the worker thread, and closes the
  observation session.
* `health()` reports the running flag, the state, the last error, the last
  successful sample, sample/error counts, and the platform capability map.
  The dashboard derives its status from this — never from a UI session flag.

---

## 6. Configuration and secrets

`src/common/config.py` reads configuration from the environment, loading a
local `.env` when present.

| Variable | Purpose |
|---|---|
| `RESON_STORAGE_BACKEND` | `supabase` (default when credentials exist), `sqlite`, `memory` |
| `RESON_SQLITE_PATH` | Path to the local SQLite file |
| `RESON_HOST_ID` | Override the derived stable host identifier |
| `SUPABASE_URL`, `SUPABASE_SECRET_KEY` | Required only for the Supabase backend |
| `RESON_STORAGE_BACKEND`, `RESON_ENVIRONMENT`, `RESON_DEFAULT_LOOKBACK_HOURS` | Other application settings |

Secrets are never written to the frontend, never logged, and never committed.
`.env` and `.streamlit/secrets.toml` are both git-ignored.

---

## 7. Background execution

| Task | Where it runs | Lifetime |
|---|---|---|
| Dashboard rendering | Streamlit's script runner | Per session |
| In-dashboard collector | `BaseCollector` daemon thread inside the Streamlit process | Until stopped or the process exits |
| Standalone agent | Its own OS process | Until SIGINT/SIGTERM |

The agent is the recommended way to collect continuously, because it does not
depend on a browser session remaining open.

---

## 8. Tests and fixtures

* `tests/conftest.py` — deterministic record builders.
* `tests/test_collector.py` — common collector lifecycle, platform factory,
  host identity.
* `tests/test_platform_collectors.py` — Linux/macOS collection logic plus a
  live smoke test for the running platform.
* `tests/test_windows_event_log.py` — allowlist filtering, bounding,
  deduplication, checkpointing, and error handling.
* `tests/test_platform_event_logs.py` — the Linux journal and macOS unified
  log readers: allowlists, bounding, deduplication, checkpointing, parsing,
  error handling, and the per-platform reader selection.
* `tests/test_correlation_equivalence.py` — a recorded digest of every
  decision the correlation engine makes, so a performance change can never
  change an outcome.
* `tests/test_demonstration_performance.py` — the demonstration's wall clock
  and the equivalence of the batched and per-row evidence writes.
* `tests/test_agent.py` — agent configuration, ingestion, lifecycle, retention.
* `tests/test_monitoring.py` — source inventory, status derivation, mode
  resolution.
* `tests/test_sqlite_storage.py` — schema, migrations, source-aware queries,
  concurrency, rollback.
* `tests/test_streamlit_app.py` — the dashboard shell via Streamlit's
  `AppTest` harness.

`tests/test_storage.py` and `tests/test_supabase_connection.py` are live
integration tests that require Supabase credentials and network access.

---

## 9. Deployment configuration

* `.streamlit/config.toml` — headless mode, no usage statistics, dark theme.
* `requirements.txt` — runtime dependencies.
* `Procfile` — `streamlit run app/streamlit_app.py` for platforms that use it.

See `docs/deployment.md` for the full deployment procedure and
`RESON_DEPLOYMENT_READINESS_REPORT.md` for the current readiness state.

---

## 10. Platform compatibility

| Platform | Status | Notes |
|---|---|---|
| Linux | Implemented and validated | See `docs/cross_platform.md` |
| Windows | Implemented; platform validation pending | No Windows machine was available in the development environment |
| macOS | Implemented; platform validation pending | No macOS machine was available in the development environment |

`psutil` provides the cross-platform core, so the same code paths run
everywhere; the platform-specific parts are the collectors themselves.
