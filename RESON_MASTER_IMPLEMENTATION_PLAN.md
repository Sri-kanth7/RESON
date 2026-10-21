# RESON Master Implementation Plan

**Project:** RESON — System Observability & Incident Intelligence Platform
**Branch:** `main` | **HEAD:** `4ce1662 fix: harden SQLite concurrent writes` (unchanged — no commit was made)
**Working tree:** 27 modified tracked files + 34 untracked paths, all preserved
**Status:** Phases 0–3, 6–8 implemented and verified; Phase 1 completion
(1.6 local-time display), Phase 4 gap 4.5 (incident/evidence provenance) and
the Phase 5 Supabase-provenance defect were closed in the 2026-10-21 recovery
session. Phase 5 remote ingestion remains deliberately unimplemented.

> **Recovery note (2026-10-21).** This document was last edited by the
> previous session at 06:29 UTC. Its header and §7 still described the state
> as "Phase 1 next", and its §7.6 claimed that no test was skipped — both
> were already stale when the session was interrupted. The authoritative
> record of the recovery audit is **§0** (added below); the phase sections
> that follow are the pre-recovery plan and have been annotated where the
> recovery changed a status.

---

## 0. Recovery audit (2026-10-21)

### 0.1 Where the previous session stopped

Evidence: file modification times and the Git index.

| Evidence | Finding |
|---|---|
| `git status` at session start | 20 modified tracked files, 34 untracked paths |
| `.git/index` mtime | `2026-10-21 05:10:44` — written by the previous session, **not** during recovery |
| Last file writes | `tests/conftest.py` 06:34:58, `tests/test_config.py` 06:35:02, `tests/test_orchestrator.py` 06:35:10, `tests/test_storage_selection.py` 06:35:17, `tests/test_supabase_connection.py` 06:35:25, `tests/test_storage.py` 06:35:31, `docs/development.md` 06:37:37 |
| Last plan edit | `RESON_MASTER_IMPLEMENTATION_PLAN.md` 06:29 — **before** those six test files were edited |
| Last source edit | `src/intelligence/baseline.py` 06:32 |

**Conclusion.** The previous session stopped at ~06:37 UTC, immediately
after adding the `requires_supabase` skip marker to the six Supabase
live-integration test modules and updating `docs/development.md` to describe
it. It did **not** update the master plan afterwards, so the plan's §7.6
statement *"No assertion was weakened or skipped to obtain a green suite"*
no longer matches the code: those 19 tests are now **skipped with an
explicit reason** when `SUPABASE_URL`/`SUPABASE_SECRET_KEY` are absent.
Their assertions are unchanged and they run in full when the credentials
are present. The plan has been corrected in §7.6.

No work was lost. The session ended between two completed edits, not inside
a file: every changed file parses, imports, and passes the suite.

### 0.2 A Git reporting discrepancy (important)

The **first** `git status --porcelain` of the recovery session reported only
20 modified files and omitted the seven files listed in §0.1. A second
`git status` and `git diff HEAD --name-only` both reported **27**.

- `git ls-files -v` shows every entry as `H ` — no `assume-unchanged` and no
  `skip-worktree` bit is set.
- `git diff --cached` is empty; the index matches `HEAD` for every file.
- The index file itself was last written at 05:10:44, before the seven
  files were edited at 06:34–06:37.

So the index contained stale stat data for those seven paths and the first
`status` trusted it. **Recovery rule adopted: always cross-check `git status`
against `git diff HEAD --stat`.** The current state is verified consistent
(27 = 27, `comm` shows no difference).

### 0.3 Verified baseline at recovery

| Check | Command | Result |
|---|---|---|
| Full suite | `python -m pytest -q` | **512 passed, 19 failed** |
| Failing tests | — | All 19 are the Supabase live-integration tests, failing with `RuntimeError: SUPABASE_URL is not configured` — **missing credentials / external service**, not a product defect |
| Offline suite | `--ignore=tests/test_storage.py --ignore=tests/test_supabase_connection.py` | passes |

The 19 failures were the *pre-marker* state. Once the previous session's
skip marker is in place, the same 19 tests report as **skipped**.

### 0.4 Defects found during the recovery audit

| # | Defect | Severity | Status |
|---|---|---|---|
| D1 | `SupabaseStorage.get_metrics/get_logs/get_events/get_deployments` did **not** accept the `source`/`host_id` arguments that the `Storage` ABC's own generic helpers pass. Any dashboard read against the production backend raised `TypeError`. | **Product defect, production backend** | **Fixed** (Phase 5 task 5.5) |
| D2 | `SupabaseStorage` never wrote or returned `source`/`host_id`, so the production backend silently discarded provenance for every record type. | **Data-integrity defect** | **Fixed** (Phase 5 task 5.5) |
| D3 | `Incident` and `Evidence` carried no provenance, so an incident could not be attributed to the telemetry source it was detected from. (Plan task 4.5, previously deferred.) | Requirement gap | **Fixed** (Phase 4 task 4.5) |
| D4 | `run_demonstration` analysed a *second, untagged* copy of the dataset while persisting the tagged one — so every demonstration incident and evidence link was stored without provenance. | **Data-integrity defect** | **Fixed** (Phase 4 task 4.7) |
| D5 | The dashboard rendered **UTC only**; the Settings page stated "local timezone display is pending". (Plan task 1.6, previously partial.) | Requirement gap | **Fixed** (Phase 1 task 1.6) |
| D6 | The base SQLite schema created provenance indexes on columns that migration v4 adds. Against a **legacy** database this raised `sqlite3.OperationalError: no such column: source` — the migration could not run. | **Migration defect** | **Fixed** (Phase 4 task 4.5) |
| D7 | The master plan's header, §6 acceptance matrix and §7 status table described a pre-Phase-1 state while §4 described Phases 1–8 as done. | Documentation defect | **Fixed** (this document) |
| D8 | `RESON_DEPLOYMENT_READINESS_REPORT.md` reported "496 passed, 19 failed"; the actual count differed, and the report listed the `Incident`/`Evidence` provenance gap as open. | Documentation defect | **Fixed** (§8.4) |

### 0.5 Recovery checkpoint (recorded, no commit made)

- Branch `main`, HEAD `4ce1662`, upstream `origin/main` — **unchanged**.
- No commit, push, reset, clean, stash, or history rewrite was performed.
- No file was deleted or reverted. Every pre-existing change is preserved.
- No real user database was touched: all migration and storage tests use
  temporary files (`tmp_path`), and the live agent run used
  `/tmp/opencode/agent.db`.
- The only destructive-capable code path (migration v4) was exercised against
  throwaway databases and a hand-built legacy database.

### 0.6 Phase status at recovery

| Phase | Status before recovery | Now |
|---|---|---|
| Phase 0 — Audit & plan | IMPLEMENTED | IMPLEMENTED |
| Phase 1 — Data integrity & source isolation | IMPLEMENTED, task 1.6 partial (UTC-only display) | **IMPLEMENTED / TESTED** — local-time display closed |
| Phase 2 — Real telemetry & collectors | IMPLEMENTED / TESTED, **Linux platform-validated** | unchanged |
| Phase 3 — Dashboard | IMPLEMENTED / TESTED (8 pages × 4 modes) | IMPLEMENTED / TESTED — display-timezone control added |
| Phase 4 — Incident intelligence | IMPLEMENTED, **gap 4.5 open** | **IMPLEMENTED / TESTED** — incidents and evidence carry provenance |
| Phase 5 — Multi-host & hosted | PARTIAL — remote ingestion deliberately not implemented; **Supabase provenance defect open** | PARTIAL — **Supabase provenance defect fixed**; remote ingestion still deliberately not implemented |
| Phase 6 — Cross-platform & deployment | DEPLOYMENT-READY (not deployed) | unchanged — local start re-verified |
| Phase 7 — Testing & performance | TESTED (512 pass / 19 env failures) | **TESTED** (583 pass / 19 skipped / 0 fail) |
| Phase 8 — Documentation | IMPLEMENTED, reports partly stale | IMPLEMENTED — reports refreshed |

---

## 1. Project Goals and Architecture

### Goals
- Monitor real computers; collect trustworthy telemetry; distinguish real data from demonstrations.
- Deterministic, rules-based incident intelligence (no mandatory LLM/paid API).
- Preserve historical data; present via a professional Streamlit dashboard.
- Work locally on Windows/Linux/macOS; architect for public Streamlit-compatible deployment with a separately running local collector for real hosted monitoring.
- Be honest about capabilities and limitations; never represent demo/imported data as live telemetry.

### Operating modes (must be shown prominently and consistently)
- **A — Local Monitoring:** collector on the user's own computer, real telemetry persisted locally.
- **B — Demonstration:** clearly labeled synthetic data; never silently mixed with live data.
- **C — Hosted Monitoring:** public dashboard displays authorized telemetry from a separately running local collector; server must not claim to observe a visitor's computer.
- **D — Imported History:** source-aware validation and analysis of imported records.

### Architecture (target)
```
Local collector/agent (psutil, cross-platform)  -->
  [platform collector] -> normalized observation -> validation -> ingestion -> persistence (SQLite local)
                                                                        -->
Telemetry transport (authenticated, future)  -->  ingestion/validation -->
  tenant + host authorization --> persistent storage --> incident processing --> dashboard (Streamlit)
```

### Core modules (current)
- `src/data/schemas.py` — data contracts. `source` + `host_id` on **Metric, Log, Event, Deployment, Incident and Evidence**; `classify_source()` maps a missing or unrecognised source to `unknown`.
- `src/data/storage.py` — Storage ABC. Provenance (`source`/`host_id`) is part of every `get_*` contract; `provenance_capability()` lets a backend report that it cannot store it.
- `src/data/sqlite_storage.py` — SQLite backend, WAL, versioned migrations (**SCHEMA_VERSION=4**), concurrent write lock + retry, UTC ISO-8601 timestamps, source/host indexes.
- `src/data/memory_storage.py`, `supabase_client.py`, `supabase_storage.py` — other backends. The Supabase backend probes the remote schema once per table and writes/reads/filters provenance when the columns exist.
- `src/data/ingestion.py` — TelemetryIngestionService.
- `src/data/collector.py` — common collector interface; `linux_collector.py`, `macos_collector.py`, `windows_collector.py`, `windows_event_log.py`; `collector_factory.py` resolves the platform.
- `src/data/agent.py` — standalone collector agent (`python -m src.data.agent`).
- `src/data/host_identity.py` — stable, non-secret host id.
- `src/data/monitoring.py` — source inventory, derived collector status, mode resolution, process/volume/interface inventories.
- `src/data/generator.py`, `simulator.py`, `dataset.py`, `dataset_service.py` — deterministic demo data, tagged `simulation`.
- `src/common/config.py` — RESONConfig; backends: supabase (default), sqlite, memory; `get_config()` cached.
- `src/common/timeutil.py` — the single UTC→display-timezone conversion point (naive = UTC, aware = converted, zone always named, DST applied per instant).
- `src/intelligence/` — baseline, deviation, change_detection, fingerprint, shift, historical_comparison. Deviations and change points carry observation provenance.
- `src/incidents/` — correlation, detector, lifecycle, evidence, reconstruction, service, similarity, timeline. Signals, evidence and incidents carry provenance.
- `src/orchestrator.py` — RESONApplication, create_application.
- `app/streamlit_app.py` — entry point, navigation, collector in session state, display-timezone control.
- `app/pages/{overview,system_resources,processes,system_events,incidents,behavior,history,settings}.py`, `app/services/backend.py`, `app/theme.py`, `app/components/ui.py`.

---

## 2. Current Repository and Git State

**Final state at the end of this work:**

- Branch: `main`, still up to date with `origin/main`.
- HEAD commit: **unchanged** at `4ce1662 fix: harden SQLite concurrent writes`.
  **No commit, push, reset, clean, stash, or history rewrite was performed.**
- Working tree: modified/new files listed in the final report; nothing staged.
- The 4 pre-existing untracked report files are **untouched** (original
  timestamps preserved): `IMPLEMENTATION_SUMMARY.md`,
  `RESON_AUDIT_REVIEW_REPORT.md`, `RESON_FINAL_IMPLEMENTATION_REPORT.md`,
  `RESON_IMPLEMENTATION_REPORT.md`.
- `.gitignore` excludes `*.sqlite3`, `reson.db`, `.reson/`, `.env`,
  `.venv`, `.pytest_cache/`, `__pycache__/`, and `.streamlit/secrets.toml`.
  `.streamlit/config.toml` is intentionally tracked (it holds no secrets).

**Note on concurrent work.** A **second recovery instance was running
concurrently** in this working tree during the 2026-10-21 session (see §0.1
for the file-timeline evidence). It implemented the same two slices —
local-timezone display and incident provenance — in the same files. The two
implementations were reconciled rather than overwritten:

* the local-timezone module `src/common/timeutil.py` and its test file
  `tests/test_timeutil.py` were kept, and the other instance's genuine bug
  fix was preserved (`__all__` listed a non-existent `format_zone`, which
  would raise `AttributeError` on `from src.common.timeutil import *`; the
  missing public helpers `chart_axis_title` and `resolve_choice` were added
  to `__all__`);
* the incident/evidence provenance work (schema, detector, evidence chain,
  correlation edges, SQLite migration v4, Supabase provenance, and the
  demonstration pipeline fix) was completed and verified in this instance;
* no file was deleted, reverted, or reset to resolve the overlap, and the
  working tree was re-verified as a whole (`583 passed, 19 skipped`).

### Original state (before any work)

- Recent commits: `1706d87` SQLite storage, `197d3f9` Windows telemetry,
  `5d61193` Streamlit dashboard, plus Phase 2/3 intelligence commits.
- No `.streamlit/config.toml`, no `Procfile`, no `pyproject.toml`, no
  `setup.py`, no `src/common/timeutil.py`, no platform collectors beyond
  Windows, and no `scripts/` directory.

---

## 3. Baseline Test Results

Command: `python -m pytest -q` (Python 3.14.2, deps installed: streamlit, pydantic, psutil, supabase, altair, pandas).

Result: **379 passed, 19 failed**.

All 19 failures are **environmental / missing configuration**, NOT product-code failures:
- `tests/test_config.py::test_config_loads`
- `tests/test_config.py::test_config_is_cached`
- `tests/test_orchestrator.py::test_create_application`
- `tests/test_storage.py` × 12 (service/metric/log/event/deployment/incident/evidence persistence + historical query)
- `tests/test_storage_selection.py` × 2
- `tests/test_supabase_connection.py::test_supabase_connection`

Cause: `RuntimeError: SUPABASE_URL is not configured` — the default storage backend is `supabase`, which requires `SUPABASE_URL` and `SUPABASE_SECRET_KEY`. No `.env` present, no network to Supabase. Classification per §12.6: **external network dependency / missing secret/configuration**. Offline suite works: `--ignore=tests/test_storage.py --ignore=tests/test_supabase_connection.py`.

No product-code failures in the baseline. The existing test suite is a solid regression foundation.

---

## 4. Phase-by-Phase Tasks

### Phase 0 — Repository audit & implementation plan (THIS DOCUMENT)
- [x] Inspect repo, git state, architecture, config, schemas, ingestion, intelligence, UI, deployment, tests.
- [x] Run baseline tests; record exact outcomes (379 pass / 19 env failures).
- [x] Create `RESON_MASTER_IMPLEMENTATION_PLAN.md`.
- **Status:** IMPLEMENTED (this plan) / Next: Phase 1.

### Phase 1 — Data integrity, source isolation & time correctness
**Goal:** Every telemetry record has reliable provenance; demo and live data never silently mix; UTC persistence + correct local display; safe migrations.

Tasks:
1. `1.1` [DONE] Add `source` + `host_id` to `Log`, `Event`, `Deployment` schemas (backward-compatible, optional, default `None` → `unknown` classification at read/query time). Document source values (`windows_local`, `linux_local`, `macos_local`, `hosted_collector`, `simulation`, `imported`, `unknown`) + `classify_source()` helper.
2. `1.2` [DONE] Storage migration: add `source`/`host_id` columns to `logs`/`events`/`deployments` tables via versioned, repeatable migration (SCHEMA_VERSION 1→2), `CREATE TABLE IF NOT EXISTS` for new tables, indexes; never drop/recreate tables. Tested against temp/disposable copies; migration is idempotent.
3. `1.3` [DONE] Source-aware query support in `SQLiteStorage` and `InMemoryStorage`: `source`/`host_id` filters on `get_metrics`/`get_logs`/`get_events`/`get_deployments`. Indexes on `(source, host_id)`.
4. `1.4` [DONE at storage layer] `run_demonstration` / `HistoricalDatasetService.build_and_persist` now tag every persisted record `source=simulation`; source-aware reads separate demo from live. Demo never overwrites/clears/relabels real telemetry. Headline-metric UI labeling (combined demo+live) is a Phase 3 dashboard task.
5. `1.5` [DONE] Legacy NULL source read back as `None` → `classify_source()` → `unknown`; never inferred real.
6. `1.6` [DONE] Timestamp correctness: UTC persistence via `_iso`/`_parse` (naive treated as UTC, stored as UTC, read back aware). `src/common/timeutil.py` provides the single display-conversion point: naive input is UTC, aware input is converted (never relabelled), the zone is always named, DST is applied per instant, and chart axes are labelled with the zone. Naive round-trip test added. The dashboard renders in a selectable display timezone (default UTC) and names it on every timestamp.
7. `1.7` [DONE] Query/ingestion performance: bounded time windows in the UI, composite `(source, timestamp)` indexes, a database-side `get_latest_metrics` / `count_records` / `list_sources` / `list_hosts` / `latest_observation` path, and a collector sampling policy that bounds telemetry volume. Measured in `scripts/benchmark.py` — see §8b.
8. Tests: source isolation, legacy unknown sources, timezone boundaries, migrations, concurrent SQLite access — added and passing.
- **Status:** IMPLEMENTED / TESTED

### Phase 2 — Real system telemetry & collector architecture
**Goal:** Modular, cross-platform collection separated from Streamlit; standalone local agent; privacy-conscious.

Tasks:
1. `2.1` [DONE] Common collector interface in `src/data/collector.py`: `Collector` ABC, `BaseCollector` (shared start/stop/background loop/health), `CollectorState`, `CollectorHealth`, `HostSample`, `ProcessSample`, `DiskSample`, `NetworkSample`, `BatterySample`, `SensorSample`, `CollectionResult`, `ObservationSession`, `observations_to_metrics()`.
2. `2.2` [DONE] Windows collector refactored onto `BaseCollector` (backward compatible: `HostMeasurement`/`ProcessMeasurement`/`observations_to_metrics`/`CollectorState`/`ObservationSession` still importable from `src.data.windows_collector`). Added disk, network, battery, sensors (best effort, honest unavailable states). Windows Event Log collector added in `src/data/windows_event_log.py`: channel + event-id allowlist, bounded window and record limit, stable record ids, deduplication with checkpoint file, permission-aware errors, `pywin32` with `wevtutil` fallback.
3. `2.3` [DONE — PLATFORM_VALIDATED on Linux] `src/data/linux_collector.py`: CPU (incl. physical/logical core counts, frequency, load average), memory, swap, disk (pseudo-filesystem and file-bind-mount filtering), network counters, processes, battery (absent reported honestly), sensors (hwmon temperatures + fans).
4. `2.4` [IMPLEMENTED — validation pending] `src/data/macos_collector.py`: CPU, memory, swap, disk, network, processes, battery; sensors reported as unsupported (macOS hides temperatures from unprivileged processes) and never fabricated.
5. `2.5` [DONE] `src/data/agent.py` — standalone collector agent (`python -m src.data.agent`), a real OS process independent of the Streamlit page lifecycle. Supports `--backend`, `--interval`, `--retention-days`, `--events`, `--once`. Duplicate-loop prevention; explicit start/stop; health reporting; errors surfaced, never swallowed.
6. `2.6` [DONE] Sampling bounds: `MIN_INTERVAL_SECONDS = 1.0`; retention via `Storage.purge_before()` (SQLite + InMemory implemented; default no-op elsewhere); in-memory dedup set bounded; event collection bounded by window and record limit.
7. `2.8` [DONE] `src/data/host_identity.py` — stable, non-secret host id (hashed machine identifier, `RESON_HOST_ID` override), never a username/path/IP. `src/data/collector_factory.py` — platform resolution with lazy per-platform imports.
8. `2.9` [DONE] App now uses the platform factory (no longer hardcodes Windows), so a Linux/macOS host is no longer mislabelled as `windows_local`.
9. Tests: `tests/test_collector.py` (23), `tests/test_platform_collectors.py` (18), `tests/test_windows_event_log.py` (20), `tests/test_agent.py` (15) — 76 new tests, all passing.
- **Status:** TESTED (Linux platform-validated; Windows/macOS implementation complete, platform validation pending hardware)

### Phase 3 — Professional observability dashboard
**Goal:** Polished UI; honest status indicators; source-aware queries; real data only.

Tasks:
1. `3.1` [DONE] Design system extended: mode-banner and empty-state styling added to `app/theme.py`; consistent cards, tables, chips, and charts retained.
2. `3.2` [DONE] Eight pages: Overview, **System Resources** (new), **Processes** (new), **System Events** (new), Incidents, Behavior Analytics, Historical Explorer, **Settings & Data Sources** (new).
3. `3.3` [DONE] Mode selector in the sidebar (Local / Demonstration / Hosted / Imported) with a prominent `MODE:` banner on every page; every card has a defined data source and a correct empty/unavailable state.
4. `3.4` [DONE] Status derived from `derive_status()` — running, stopped, degraded, no recent telemetry, no data, error, permission denied, historical-only, demonstration. A running collector without a recent sample is **never** reported as live. No UI session flag is used as proof.
5. `3.5` [DONE] Filters, time ranges, host filters, refresh timestamps, pagination; the Overview trend is bounded to 24 h; the System Resources page offers 15 m–7 d + all-history windows and reports sampling gaps explicitly.
6. `3.6` [DONE] Source-aware queries everywhere: counts, series, processes, events, volumes, interfaces, incidents, evidence, and exports are all source-filtered.
7. `3.7` [DONE] Storage line in the sidebar now states durability ("durable" / "not durable") so a hosted SQLite file is never mistaken for durable shared storage.
- **Status:** IMPLEMENTED / TESTED (15 Streamlit `AppTest` tests; all 8 pages × 4 modes render without exception)

### Phase 4 — Deterministic incident intelligence
**Goal:** Interpretable, testable rules; evidence-backed incidents; coherent lifecycle.

Tasks:
1. `4.1` [EXISTING — retained] Rolling baselines, z-score / relative / absolute deviation rules, change-point detection over sustained anomalous runs, minimum-data requirements, and a bounded Behavior Shift Score. Deterministic and offline-testable. The existing Phase 2 implementation already satisfies the requirement.
2. `4.2` [EXISTING — retained] Correlation over host/service/environment/metric/deployment/event factors, with an explicit time-bounded factor requirement so far-apart alarms never merge. Temporal correlation is never presented as causation.
3. `4.3` [EXISTING — retained] Lifecycle: open → investigating → resolved, with reopening on later anomalous evidence and recovery only from positive evidence. Deduplication via content-derived ids.
4. `4.4` [EXISTING — retained] Every incident carries a severity rationale, a timeline, a full evidence chain traceable to raw record ids, and a historical-similarity factor matrix. No root-cause claim is made.
5. `4.5` [DONE - recovery 2026-10-21] Historical reconstruction exists (`src/incidents/reconstruction.py`). **Closed:** `Incident` and `Evidence` now carry `source`/`host_id`. The chain is complete and verified end to end: `Metric/Log/Event/Deployment` -> `MetricObservation` -> `MetricDeviation`/`ChangePoint` -> `Signal` -> `Evidence` -> `Incident`. An incident records the provenance of its supporting observations **only when they agree**; a cluster that mixes sources is left unattributed rather than attributed to an arbitrary one (`dominant_provenance`). The dashboard shows the incident's source and filters the incident list by the active mode's source.
6. `4.7` [DONE - recovery 2026-10-21] The demonstration pipeline analysed a *second, untagged* copy of the dataset while persisting the tagged one, so every demonstration incident and evidence link was stored without provenance. `HistoricalDatasetService.build_and_persist` now accepts the dataset it should persist, tags it, and returns the tagged records; `run_demonstration` analyses exactly those objects. Verified: the demonstration's incident and all 18,915 evidence links carry `source="simulation"`.
6. `4.6` [EXISTING — retained] Synthetic scenario evaluation exists (`tests/test_intelligence_scenarios.py`, `tests/test_incident_detector.py`, `tests/test_lifecycle.py`). Results are labelled as test outcomes, never as production performance.
- **Status:** IMPLEMENTED / TESTED (no open gap)

### Phase 5 — Multi-host & hosted architecture
**Goal:** Hosted dashboard monitors multiple authorized machines without confusing the dashboard server with those machines.

Tasks:
1. `5.1` [DONE] Stable, non-secret host identity in `src/data/host_identity.py` — SHA-256 of the platform machine identifier, truncated; `RESON_HOST_ID` override. Never a username, home path, or IP address. Every observation carries `host_id` and `source`.
2. `5.2` [NOT IMPLEMENTED — deliberately] Remote ingestion is **not** enabled. The full requirement list (agent authentication, per-host authorization, tenant isolation, secure transport, server-side validation, size/rate limits, replay protection, schema versioning, secret rotation, safe errors, auditability) is specified in `docs/deployment.md` §6. Until implemented and tested, the hosted mode reports "unavailable" — enforced by `resolve_mode()` and pinned by `test_hosted_mode_does_not_claim_to_observe_the_visitor`.
3. `5.3` [DONE for the current scope] No collector secret ever reaches the frontend, session state, logs, or the repository. No unauthenticated telemetry endpoint exists. The dashboard is single-tenant by construction.
4. `5.4` [PARTIAL] SQLite is the local single-host store; Supabase is the managed option for shared use. Hosted-filesystem durability is documented and surfaced in the UI. Concurrent-write safety is tested (8 writer threads + 4 reader threads against one file).
5. `5.5` [DONE - recovery 2026-10-21] **The Supabase backend lost provenance and crashed on dashboard reads.** Its `get_metrics`/`get_logs`/`get_events`/`get_deployments` did not accept the `source`/`host_id` arguments that the `Storage` ABC's own generic helpers pass, so every dashboard read against the production backend raised `TypeError`; and it never wrote or returned provenance at all. The backend now stores, returns and filters provenance, and **probes the remote schema once per table**: when a project table predates the provenance columns, the limitation is reported through `provenance_capability()` and surfaced in the sidebar and Settings page instead of being hidden. A source filter against such a table is an **error**, never a silently ignored argument that would mix live and synthetic data. The provenance columns are now part of the `Storage` ABC contract, so a future backend cannot repeat the defect.
- **Status:** PARTIAL — host identity and provenance foundations done (SQLite, in-memory and Supabase); authenticated remote ingestion NOT implemented (the single largest remaining functional gap)

### Phase 6 — Cross-platform operation & public Streamlit deployment
**Goal:** Local support for Windows/Linux/macOS with honest validation status; public hosted dashboard ready.

Tasks:
1. `6.1` [DONE] `docs/cross_platform.md`: per-OS Python versions, install steps, permissions, start/stop, capability table, known limitations, and a "how to validate a platform yourself" procedure. Support is **not** claimed for Windows/macOS — both are marked *implemented, platform validation pending*.
2. `6.2` [DONE] Deployment artifacts: `.streamlit/config.toml`, `requirements.txt` (now explicitly listing `altair` and `pandas`, which the pages import), `Procfile`, and the existing `app/streamlit_app.py` entry point. Clean start verified: no absolute local paths, no Windows-only module-scope imports, no dependency on a pre-existing database file.
3. `6.3` [DONE] The public URL behaves honestly: hosted demonstration is explicitly labeled; hosted real monitoring reports "unavailable"; there is no silent fallback from a failed real collector to synthetic telemetry.
4. `6.4` [DONE] Deployment constraints addressed: ephemeral hosted filesystem, cold starts, per-session memory storage, resource limits, secret configuration, no dashboard authentication (documented as a gap), retention and deletion. The sidebar states the backend and its durability.
5. `6.5` [DONE] Clean-environment verification procedure written in `docs/deployment.md` §4 and executed locally.
- **Status:** DEPLOYMENT-READY (not deployed — the owner performs the deployment)

### Phase 7 — Testing, reliability & performance
**Goal:** Comprehensive test strategy; measured performance; honest failure classification.

Tasks:
1. `7.1`–`7.3` [DONE] Unit, integration, and regression tests added for: source classification, source-aware queries, legacy NULL sources, timezone normalization and DST boundaries, schema migrations (v1→v4, idempotent, non-destructive), concurrent SQLite access, rollback, collector lifecycle, sampling policy, Windows event allowlist/dedup/checkpoint, agent retention, mode resolution, incident/evidence provenance, and dashboard rendering. See §8.
2. `7.4` [PARTIAL] The Windows- and macOS-specific logic is exercised with mocked `psutil` and passes on any platform, but no real Windows or macOS run occurred in this environment. CI was not available here.
3. `7.5` [DONE] `scripts/benchmark.py` measures collector overhead, SQLite ingestion throughput, query latency (bounded and unbounded), the dashboard-optimized indexed reads, telemetry volume under the sampling policy, and retention pruning. See §8b for the actual numbers. No benchmark is invented.
4. `7.6` [DONE - corrected 2026-10-21] The 19 pre-existing failures are classified as *missing secret / external network dependency*, not product defects. The 19 live Supabase integration tests now carry a `requires_supabase` marker: they are **skipped with an explicit reason** when `SUPABASE_URL`/`SUPABASE_SECRET_KEY` are absent, and run in full when the credentials are present. Their assertions are unchanged — no assertion was weakened or deleted. **Correction to the earlier wording in this document:** the earlier text said "No assertion was weakened or skipped to obtain a green suite". The *assertions* were indeed not weakened, but the 19 tests **are** now skipped when credentials are absent; that is what turns 19 failures into 19 skips. The offline suite passes completely.
- **Status:** TESTED

### Phase 8 — Documentation & handoff
**Goal:** Docs match actual code; no unimplemented features documented as available.

Tasks:
1. `8.1` [DONE] `README.md` rewritten: what RESON does, supported OS + **verified** capabilities, architecture and data flow, local install, starting/stopping the collector, launching the dashboard, demo vs local monitoring, storage configuration, running tests, hosted deployment, **why a hosted URL needs a separate collector**, and a limitations table.
2. `8.2` [DONE] `RESON_MASTER_IMPLEMENTATION_PLAN.md` — this document, updated after each slice.
3. `8.3` [DONE] `RESON_DEPLOYMENT_READINESS_REPORT.md` — readiness assessment with verified/not-verified separation.
4. `8.4` [DONE] `docs/architecture.md`, `docs/deployment.md`, `docs/cross_platform.md`, `docs/security_and_privacy.md`, `docs/telemetry_sources.md`, `docs/troubleshooting.md` created; the pre-existing `docs/development.md` retained and still accurate.
- **Status:** IMPLEMENTED

---

## 5. Dependencies Between Tasks

- Phase 1 (provenance + source-aware queries) is foundational: Phases 2–6 all depend on source/host_id being present on all record types and on source-aware reads.
- Phase 2 (collector architecture) depends on Phase 1 schemas (source/host_id on records) and storage writes.
- Phase 3 (dashboard) depends on Phase 1 source-aware queries and Phase 2 collector health.
- Phase 4 (intelligence) depends on Phase 1 source preservation through aggregation/incident generation.
- Phase 5 (hosted) depends on Phase 1 host_id/source, Phase 2 collector, Phase 3 dashboard.
- Phase 6 (deployment) depends on Phases 1–5 being coherent; docs in Phase 8 reference all.
- Phase 7 (testing) runs throughout; regression tests for Phase 1 must pass before Phase 2.
- Phase 8 docs written last (but updated incrementally).

---

## 6. Acceptance Criteria (Acceptance Matrix)

See §16 of the directive.

| Area | Acceptance requirement | Status | Evidence |
|---|---|---|---|
| Repository integrity | Existing user changes preserved; no unauthorized Git ops | IMPLEMENTED | The 4 pre-existing untracked reports are untouched; no commit/push/reset/clean performed; `git status` shows only intended changes |
| Persistent plan | Every phase has status, evidence, blockers, next steps | IMPLEMENTED | This document — all 9 phases carry a status, evidence, and next action |
| Data provenance | Every new observation has reliable source | IMPLEMENTED | `source` + `host_id` on **every** record type (Metric, Log, Event, Deployment, Incident, Evidence); `classify_source()`; every collector stamps source + host id; **all three storage backends** store, return and filter provenance |
| Legacy data | Unknown sources remain explicitly unknown unless proven | IMPLEMENTED | `classify_source(None) == "unknown"`; `source="unknown"` matches NULL-source rows; `test_legacy_record_without_source_is_preserved_as_none` |
| Demo isolation | Synthetic data cannot silently contaminate live dashboards/incidents | IMPLEMENTED | Demo tagged `simulation`; all reads source-filtered; the demo button switches the dashboard to Demonstration mode; `test_demo_records_are_tagged_simulation_and_never_live` |
| Time correctness | UTC persistence + correct local display tested | IMPLEMENTED | UTC persistence + naive→UTC normalization tested; `src/common/timeutil.py` converts for display and names the zone on every timestamp; DST applied per instant; 34 timezone tests; every page renders local time with the zone named in the top bar and on each timestamp |
| SQLite safety | Migrations, concurrent access, rollback tested | TESTED | Schema v1→v4, idempotent, non-destructive; 8 writers + 4 readers; rollback test; migration v4 verified against a hand-built legacy database |
| Incident provenance | An incident is attributable to the telemetry source it came from | IMPLEMENTED | `source`/`host_id` on `Incident` and `Evidence`; full chain Metric→Signal→Evidence→Incident; `dominant_provenance` refuses to attribute a mixed cluster; the incident list is source-filtered by the active mode |
| Windows telemetry | Real CPU/memory/disk/network/process validated | PARTIAL | Collector implemented with disk/network/battery/sensors; Linux validated on real hardware; **Windows platform validation pending** |
| Battery/sensors | Real readings when supported; honest unavailable otherwise | IMPLEMENTED | Battery reports `present=False` when absent; macOS sensors reported unsupported; never fabricated |
| Windows events | Bounded, filtered, deduped, safe errors | TESTED | Channel + event-id allowlist, bounded window and record limit, stable ids, checkpoint dedup, permission errors surfaced — 20 tests |
| Linux support | Capabilities + validation status documented | PLATFORM_VALIDATED | Real collection run on this Linux machine; `docs/cross_platform.md` |
| macOS support | Capabilities + validation status documented | IMPLEMENTED | `docs/cross_platform.md`; implementation + tests complete, **platform validation pending** |
| Collector lifecycle | Start/stop/restart/failure/stale-data tested | TESTED | `tests/test_collector.py` (29) + `tests/test_agent.py` (15): start/stop, duplicate prevention, error state, publish failure, health |
| Incident intelligence | Interpretable, evidence-backed, tested | IMPLEMENTED | Existing deterministic pipeline retained and re-verified; severity rationale + evidence chain; incidents and evidence now carry provenance |
| Dashboard | Real source-aware queries + truthful status | IMPLEMENTED | 8 pages, all source-filtered; status derived from real collector health; 15 AppTest tests |
| Local persistence | History survives restarts | TESTED | SQLite file re-read across instances; `test_persistence_across_reopen` |
| Hosted dashboard | Clean hosted startup + browser access verified (when deployed) | DEPLOYMENT-READY | Artifacts present; local start verified (`/_stcore/health` → 200); **public deployment not performed** (owner action) |
| Remote ingestion | Auth/authz/validation/tenant isolation tested before enabling | NOT_STARTED | Deliberately not implemented; the hosted mode reports "unavailable" rather than faking it |
| Multi-host isolation | Tenant cannot access another tenant's data | BLOCKED | Depends on remote ingestion; blocked behind Phase 5 |
| Privacy | Sensitive collection/transmission minimized + documented | IMPLEMENTED | `docs/security_and_privacy.md`; no cmdline/env/payload/credential collection; transmission off by default |
| Performance | Measurements exist; no invented benchmarks | TESTED | `scripts/benchmark.py` — see §8b |
| Tests | Failures accurately classified | TESTED | 583 pass / 0 fail / 19 skipped (live Supabase integration, explicit reason) |
| Documentation | Matches implementation | IMPLEMENTED | README + 6 new docs; limitations listed explicitly; unimplemented features documented as unavailable |
| Git state | No unauthorized commit/push/reset/clean/history rewrite | IMPLEMENTED | No commit, push, reset, clean, stash, or history rewrite performed |

---

## 7. Implementation Status

| Phase | Status | Evidence |
|---|---|---|
| Phase 0 — Audit & plan | IMPLEMENTED | This document; baseline tests run (379 pass / 19 env failures) |
| Phase 1 — Data integrity & source isolation | IMPLEMENTED / TESTED | `source`+`host_id` on all record types; `classify_source()`; source-aware queries in all three backends; schema v1→v4 non-destructive idempotent migrations; demo tagged `simulation`; UTC storage + local-time display (DST-correct) tested |
| Phase 2 — Real telemetry & collector | TESTED (Linux platform-validated) | `src/data/collector.py` common interface; Linux/Windows/macOS collectors; `windows_event_log.py` (allowlist, bounded, dedup, checkpoint); standalone `src/data/agent.py`; `host_identity.py`; `collector_factory.py`; sampling policy; 76 new tests |
| Phase 3 — Dashboard | IMPLEMENTED / TESTED | 8 pages (4 new); monitoring-mode selector + banner; derived status; source-aware everywhere; display-timezone control; 18 AppTest tests; all pages × 4 modes render clean |
| Phase 4 — Incident intelligence | IMPLEMENTED / TESTED | Existing deterministic pipeline retained and re-verified; **the incident-provenance gap (4.5) is closed** — incidents and evidence carry `source`/`host_id` end to end |
| Phase 5 — Multi-host & hosted | PARTIAL | Stable hashed host identity + provenance done in all three backends; **remote ingestion deliberately not implemented** — hosted mode reports "unavailable" |
| Phase 6 — Cross-platform & deployment | DEPLOYMENT-READY | `.streamlit/config.toml`, `requirements.txt`, `Procfile`; `docs/cross_platform.md`; `docs/deployment.md`; local start verified; **not deployed** (owner action) |
| Phase 7 — Testing & performance | TESTED | 583 pass / 19 skipped (classified environmental) / 0 failed; `scripts/benchmark.py` with measured numbers (§8b) |
| Phase 8 — Documentation | IMPLEMENTED | README rewritten; `docs/architecture.md`, `docs/deployment.md`, `docs/cross_platform.md`, `docs/security_and_privacy.md`, `docs/telemetry_sources.md`, `docs/troubleshooting.md`; `RESON_DEPLOYMENT_READINESS_REPORT.md` |

---

## 8. Actual Test Evidence

### 8a. Test results

| Run | Result |
|---|---|
| Baseline `python -m pytest -q` (before any change) | **379 passed, 19 failed** (all 19: missing `SUPABASE_URL`) |
| Recovery baseline `python -m pytest -q` (2026-10-21, before the recovery work) | **512 passed, 19 failed** (the same 19: missing `SUPABASE_URL`) |
| Final `python -m pytest -q` | **583 passed, 19 skipped, 0 failed** |
| Offline suite (`--ignore=tests/test_storage.py --ignore=tests/test_supabase_connection.py`) | **583 passed, 5 skipped, 0 failed** |

The 19 baseline failures were **missing secret / external network
dependency**, not product defects. They are now handled correctly: the live
Supabase integration tests carry a `requires_supabase` marker and are
**skipped with an explicit reason** when the credentials are absent. Their
assertions are unchanged and execute in full when the credentials are present
— no assertion was weakened, deleted, or hidden to obtain a green suite.

| Test group | Classification | Now |
|---|---|---|
| `tests/test_storage.py` (12) | Live Supabase integration | Skipped without credentials |
| `tests/test_supabase_connection.py` (1) | Live Supabase integration | Skipped without credentials |
| `tests/test_config.py` (2 Supabase cases) | Needs Supabase credentials | Skipped without credentials |
| `tests/test_orchestrator.py` (1) | Needs Supabase credentials | Skipped without credentials |
| `tests/test_storage_selection.py` (2) | Needs Supabase credentials | Skipped without credentials |

**No product-code test failure exists at any point**, before or after the
implementation.

### 8b. New tests added

| File | Tests | Covers |
|---|---|---|
| `tests/test_collector.py` | 29 | Common collector lifecycle, duplicate prevention, health, publish arity, sampling policy, platform factory, host identity |
| `tests/test_platform_collectors.py` | 18 | Linux/macOS collection logic (mocked `psutil`) + live smoke test for the running platform |
| `tests/test_windows_event_log.py` | 20 | Allowlist, bounding, deduplication, checkpoint, permission errors, XML parsing |
| `tests/test_agent.py` | 15 | Agent config validation, ingestion, lifecycle, retention, CLI |
| `tests/test_monitoring.py` | 29 | Source inventory, status derivation, mode resolution, process inventory, series |
| `tests/test_timeutil.py` | 34 | UTC storage semantics, timezone resolution, DST per instant, display formatting, chart time conversion, dashboard-context formatting |
| `tests/test_incident_provenance.py` | 22 | Incident/evidence provenance round trip and source isolation (memory + SQLite), pipeline propagation, legacy migration, demonstration attribution |
| `tests/test_supabase_provenance.py` | 10 | Supabase provenance write/read/filter, `count_records` regression, schema-probe honesty, filter-fails-loudly |
| `tests/test_schemas.py` | +6 | Source values, `classify_source`, record provenance fields |
| `tests/test_sqlite_storage.py` | +14 | Migrations, source-aware queries, unknown-source matching, dashboard-optimized reads, schema-version recording |
| `tests/test_streamlit_app.py` | +7 | All pages, all modes, mode banner, hosted-mode honesty, display-timezone control |

### 8c. Performance — measured, not invented

Produced by `python scripts/benchmark.py` on this machine (Linux container,
Python 3.14.2, SQLite 3.45.1, 22,000 metric rows in the test table).

| Measurement | Result |
|---|---|
| Collector overhead, one full cycle | **10.6 ms** mean (9.9 ms best) |
| SQLite ingestion | **32,086 rows/s** (1k batch), **44,175 rows/s** (10k batch) |
| `get_metrics()` — full scan | 228 ms |
| `get_metrics()` — bounded window | **53 ms** |
| `source_inventory()` | **8.0 ms** (was 312 ms before the indexed path) |
| `latest_metric_values()` | **69 ms** (was 321 ms) |
| `count_records()` | **3.5 ms** |
| `list_sources()` | **3.6 ms** |
| `list_hosts()` | **4.7 ms** |
| `latest_observation()` | **0.9 ms** |
| `process_inventory()` | 234 ms (full scan; bounded by a time window in the UI) |
| Retention pruning | 27,000 rows in **0.195 s** |
| Telemetry volume, unfiltered | ~129 records/cycle |
| Telemetry volume, with the default sampling policy | **15.8 records/cycle → 272,160 records/day at a 5 s interval** (down from ~2.2M/day unfiltered) |

The remaining slow paths are genuine full scans; the UI bounds them with an
explicit time window, and the overview uses the indexed reads.

---

## 8d. Runtime verification

| Check | Result |
|---|---|
| `streamlit run app/streamlit_app.py` | starts; `GET /_stcore/health` → **HTTP 200 / `ok`**; `GET /` → **HTTP 200** (re-verified 2026-10-21, port 8599) |
| All 8 pages rendered via `AppTest` | **no exceptions** |
| All 4 monitoring modes rendered via `AppTest` | **no exceptions** |
| All 8 pages rendered with a **non-UTC** display timezone | **no exceptions** (`tests/test_streamlit_app.py::test_pages_render_in_a_non_utc_display_timezone`) |
| `python -m src.data.agent --backend sqlite --once` | **97 real metric records ingested** with `source=linux_local` and the stable hashed host id (re-verified 2026-10-21) |
| Demonstration pipeline provenance | the incident and all 18,915 evidence links carry `source="simulation"`; `get_incidents(source="linux_local")` returns `[]` |
| Secret scan across the repository | **no secrets found**; no `.env` present |
| Test isolation | tests write only to `tmp_path`; the user data directory is left clean |

---

## 9. Platform-Validation Evidence

| Platform | Python version | Validated? | Evidence | Notes |
|---|---|---|---|---|
| Linux (dev) | 3.14.2 | **Yes (Linux collector)** | `tests/test_platform_collectors.py` (18 passed, incl. live smoke test); `python -m src.data.agent --once` ingested 80 real metric records with `source=linux_local`; real CPU/mem/swap/disk/network/process/battery/sensor values observed | This is genuine platform validation of the Linux collector. |
| Windows | — | Implementation complete, **validation pending** | `src/data/windows_collector.py` (refactored, disk/network/battery/sensors added), `src/data/windows_event_log.py` (20 offline tests passing) | No Windows machine available in this environment. Collector logic is cross-platform `psutil` and is exercised by the shared test suite, but no real Windows run has occurred. |
| macOS | — | Implementation complete, **validation pending** | `src/data/macos_collector.py` (18 shared platform tests passing) | No macOS machine available in this environment. |

Per §6.4/§11.1: OS support is not claimed until validated on that OS. Windows and macOS are documented as *implemented, platform validation pending*.

## 9a. Live Collection Evidence (Linux, this environment)

```
$ python -m src.data.agent --backend sqlite --once
RESON collector agent starting: backend=sqlite host=reson-14336f25bdc8c777 \
    source=linux_local interval=5.0s
Collected and ingested 80 metric record(s).

metrics rows: 80
  ('cpu_percent', 82.6, 'percent', 'linux_local', 'reson-14336f25bdc8c777')
  ('memory_percent', 53.6, 'percent', 'linux_local', 'reson-14336f25bdc8c777')
  ('memory_used_bytes', 4467703808.0, 'bytes', 'linux_local', 'reson-14336f25bdc8c777')
  ('memory_total_bytes', 8328073216.0, 'bytes', 'linux_local', 'reson-14336f25bdc8c777')
  ('process_cpu_percent', 0.0, 'percent', 'linux_local', 'reson-14336f25bdc8c777')
  ('process_memory_rss_bytes', 585728.0, 'bytes', 'linux_local', 'reson-14336f25bdc8c777')
distinct sources: [('linux_local',)]
```

Every stored record carries a real source and the stable hashed host identity.

---

## 10. Security & Privacy Risks

### Implemented and verified

- **Provenance is explicit.** Every telemetry record carries `source` and
  `host_id`. Legacy records with a NULL source are classified `unknown` and
  are reachable through the `unknown` filter, never inferred to be real.
- **Host identity is non-secret.** A SHA-256 hash of the platform machine
  identifier, truncated. Never a username, home path, or IP address.
- **Data minimization.** No passwords, tokens, environment-variable values,
  process command-line arguments, browser history, packet payloads, file
  contents, or user identities are collected. Process data is PID, name, CPU,
  RSS, and status only.
- **Event collection is narrow and opt-in.** An explicit channel and
  event-id allowlist; `Security` is excluded; bounded window and record
  limit; stable ids; checkpoint deduplication.
- **No transmission.** The collector writes only to the locally configured
  backend. Remote ingestion does not exist in this build.
- **SQL is parameterized.** No user value is interpolated into SQL; the one
  interpolated fragment is an internal constant list of known source values.
- **Secrets stay out of source control.** `.env`, `.streamlit/secrets.toml`,
  `*.sqlite3`, and `reson.db` are git-ignored. A repository-wide secret scan
  found nothing.
- **Secrets never reach the frontend.** No collector secret is read into
  Streamlit session state or rendered.

### Open risks (documented, not hidden)

| Risk | Consequence | Mitigation in place |
|---|---|---|
| No dashboard authentication | A public deployment exposes whatever its backend holds | Documented; use a private deployment or an authenticating proxy |
| No tenant isolation | Would be a cross-tenant leak in a multi-user hosted deployment | Blocked behind remote ingestion, which is not enabled |
| No encryption at rest | The local SQLite file is readable by anyone with file access | OS-level disk encryption; the file lives in the user's own data directory |
| Hosted filesystem is ephemeral | A hosted SQLite file is not durable | The sidebar states durability; the deployment guide recommends a managed database |
| A Supabase project created before the provenance columns | Records written to such a table cannot be attributed and are reported as `unknown` | The backend probes the schema once per table and reports the limitation in the sidebar and Settings page; a source filter against such a table fails loudly instead of mixing sources |
| WAL + busy timeout is not a concurrency model for many writers | Under heavy concurrent write load, SQLite would need a managed database | The agent writes from one thread; the concurrency test covers 8 writers + 4 readers |

---

## 11. Blockers & Deferred Work

| Item | Classification | Why | Mitigation |
|---|---|---|---|
| **No Windows/macOS hardware** | Environmental | The development environment is Linux; no Windows or macOS machine was available | Implementations are complete and unit-tested with mocked `psutil`; `docs/cross_platform.md` §6 gives the exact validation procedure; both platforms are documented as *implemented, platform validation pending* — **support is not claimed** |
| **No Supabase credentials / network** | Environmental | 19 live integration tests need a reachable Supabase project | Marked `requires_supabase`; skipped with an explicit reason; assertions unchanged and run in full when the credentials are present. The offline suite passes with 0 failures. |
| **Remote ingestion not implemented** | Deferred (deliberate) | Enabling it before the security requirements are met would expose an endpoint that accepts arbitrary telemetry | The hosted mode reports "unavailable"; the full requirement list is in `docs/deployment.md` §6. **This is the single largest remaining functional gap.** |
| **Dashboard authentication / tenant isolation** | Deferred (blocked) | Depends on remote ingestion | Documented as a gap in `docs/security_and_privacy.md` §9 and the readiness report |
| **`Incident`/`Evidence` source column** | **Closed 2026-10-21** | Was deferred as a schema + migration + detector change | Implemented: schema fields, pipeline propagation, SQLite migration v4, storage filters, UI display; verified by `tests/test_incident_provenance.py` (22 tests) |
| **Local-timezone display** | Closed 2026-10-21 | Was deferred | `src/common/timeutil.py` + dashboard integration now render stored UTC in a selectable, always-named display zone. Verified by 34 tests and by an AppTest run of all 8 pages in a non-UTC zone |
| **Supabase backend provenance** | **Closed 2026-10-21** | Was an open defect (see §0.4 D1/D2) | The production backend now stores, returns and filters provenance; the remote schema is probed once per table and any limitation is reported, never hidden |
| **macOS / Linux system-event collection** | Deferred | The Windows event-log collector is Windows-specific | Documented as unsupported in `docs/cross_platform.md`; the collector interface already supports a per-platform event reader |
| **CI configuration** | Deferred | No CI provider was available in this environment | The offline suite is self-contained and runs anywhere with `pytest` |

---

## 12. Exact Next Action

**Owner action required — the implementation is complete for every phase that
can be completed without Windows/macOS hardware, a Supabase project, or
hosting credentials.**

In priority order:

1. **Review the diff and merge.** Nothing has been committed or pushed. The
   working tree contains the whole implementation; the 4 pre-existing
   untracked report files are untouched.
2. **Validate Windows and/or macOS on real hardware** — follow
   `docs/cross_platform.md` §6. That converts those platforms from
   *implemented, validation pending* to *validated* and is the only thing
   standing between the current state and full cross-platform support.
3. **Deploy the hosted dashboard** — follow `docs/deployment.md` §2–§3. The
   artifacts are in place. Verify the demonstration mode is labeled and that
   hosted real monitoring correctly reports "unavailable" until remote
   ingestion exists.
4. **Decide on remote ingestion.** It is the only unimplemented functional
   capability. `docs/deployment.md` §6 lists the eleven requirements that must
   be met and tested before it may be enabled.
5. **Optional follow-ups**, in order of value: add macOS unified-log and Linux
   journald event readers; add CI for the offline suite; add the provenance
   columns to an existing Supabase project (`ALTER TABLE metrics ADD COLUMN
   source TEXT;` and the equivalent for `logs`, `events`, `deployments`,
   `incidents`, `evidence`) so a hosted deployment can attribute records;
   bound the correlation engine's pairwise work — the full default
   demonstration timeline (15 batches) produces ~18,900 candidate pairs and
   takes ~53 s, while a 5-batch timeline takes ~0.06 s.

On resuming work: reread this plan, re-verify the Git state
(`git status`, `git diff HEAD --stat`, `git log --oneline -1` — **not**
`git status` alone, see §0.2), run `python -m pytest -q` to
re-establish the test baseline, and continue from the first incomplete item
above.
