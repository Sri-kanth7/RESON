# RESON Deployment Readiness Report

**Repository:** `https://github.com/Sri-kanth7/RESON.git`
**Branch:** `main` | **HEAD:** `97361fa feat: complete RESON observability platform with telemetry provenance` (pushed to `origin/main`)
**Assessment date:** 2026-10-21

---

## 1. Summary

| Dimension | Status | Notes |
|---|---|---|
| Local installation | **Ready** | `pip install -r requirements.txt` then run the dashboard or the agent |
| Local continuous collection | **Ready** | The standalone agent collects real telemetry on Windows, Linux, and macOS |
| Linux platform validation | **Validated** | Real collection run on this environment |
| Windows platform validation | **Pending** | Implemented and unit-tested; no Windows machine was available |
| macOS platform validation | **Pending** | Implemented and unit-tested; no macOS machine was available |
| Public hosted deployment (demonstration) | **Deployment-ready** | Artifacts present; the owner must perform the deployment |
| Public hosted deployment (real monitoring) | **Not available** | Requires the authenticated remote-ingestion path, which is not implemented |
| Production security posture | **Not production-ready** | See §7 |

**A public URL has not been created.** Deployment requires the owner's
GitHub account and hosting account; the repository is prepared for it and the
exact steps are in `docs/deployment.md`. No URL is claimed here.

> **Updated 2026-10-21 (recovery session).** The provenance gaps listed in
> the previous revision of this report are now **closed**: incidents and
> evidence carry their telemetry source, and the Supabase backend stores,
> returns and filters provenance instead of discarding it. The test counts
> below were re-measured. See §7 for the remaining gaps.

---

## 2. What was verified, and how

| Check | Command | Result |
|---|---|---|
| Dependency install and consistency | `pip install -r requirements.txt`, `pip check` | Passes on Python 3.14.2 |
| Offline test suite | `pytest -q --ignore=tests/test_storage.py --ignore=tests/test_supabase_connection.py` | **583 passed, 5 skipped, 0 failed** |
| Full test suite | `pytest -q` | **583 passed, 19 skipped, 0 failed** — the 19 skips are live Supabase integration tests, skipped with an explicit reason when the credentials are absent (see §6) |
| App starts and serves | `streamlit run app/streamlit_app.py`, then `GET /_stcore/health` | **HTTP 200 / `ok`**; `GET /` → **HTTP 200** (re-verified 2026-10-21) |
| Every page renders | `AppTest` over all 8 pages × 4 monitoring modes | **No exceptions** |
| Every page renders in a non-UTC timezone | `AppTest` over all 8 pages with `America/New_York` selected | **No exceptions** |
| Real telemetry collection | `python -m src.data.agent --backend sqlite --once` | **97 real metric records ingested**, correct source and host id |
| Data survives a restart | SQLite file re-opened in a new process | Passes |
| Migrations are non-destructive | Legacy v1 database migrated in place to v4 | Passes, idempotent, data preserved |
| Concurrent access | 8 writer threads + 4 reader threads against one file | Passes, no lost records |
| Cross-platform imports | Import every platform collector module | Passes (each is imported lazily) |
| Incident provenance | Full demonstration pipeline | The incident and all 18,915 evidence links carry `source="simulation"`; a `linux_local` filter returns none |
| Storage provenance | All three backends | SQLite, in-memory and Supabase all store, return and filter `source`/`host_id` |
| Secret scan | Repository-wide scan for credential patterns | **No secrets found**; no `.env` present |
| Test isolation | Full suite run | Tests write only to `tmp_path`; the user data directory is left clean |

### Measured performance

From `python scripts/benchmark.py` on this machine (Linux container,
Python 3.14.2, SQLite 3.45.1, 22,000 metric rows):

| Measurement | Result |
|---|---|
| Collector overhead, one full cycle | 10.6 ms mean |
| SQLite ingestion | 32,086–44,175 rows/s |
| Dashboard-optimized reads (indexed) | 0.9–8.0 ms each |
| `latest_metric_values()` | 69 ms (321 ms before optimization) |
| Bounded time-window query | 53 ms |
| Retention pruning | 27,000 rows in 0.195 s |
| Telemetry volume with the default sampling policy | 15.8 records/cycle → 272,160 records/day at a 5 s interval (down from ~2.2M/day unfiltered) |

These figures describe **this** machine and dataset size. Re-run the script
on the target hardware before quoting them as expected performance.

---

## 3. Deployment artifacts present

| Artifact | Path | Purpose |
|---|---|---|
| Streamlit configuration | `.streamlit/config.toml` | Headless mode, usage stats off, dark theme, no secrets |
| Dependencies | `requirements.txt` | Version-bounded runtime dependencies |
| Procfile | `Procfile` | `streamlit run app/streamlit_app.py` |
| Entry point | `app/streamlit_app.py` | The single dashboard entry point |
| Collector agent | `src/data/agent.py` | Standalone collection process (`python -m src.data.agent`) |
| Performance harness | `scripts/benchmark.py` | Reproducible measurement script |
| Deployment guide | `docs/deployment.md` | Local, hosted, and clean-environment verification |
| Cross-platform guide | `docs/cross_platform.md` | Per-OS support matrix, permissions, limitations |
| Security guide | `docs/security_and_privacy.md` | Data minimization, secrets, retention, known gaps |
| Telemetry reference | `docs/telemetry_sources.md` | Source values and the full data catalog |
| Troubleshooting | `docs/troubleshooting.md` | Symptoms and fixes |
| Architecture | `docs/architecture.md` | Data flow, boundaries, lifecycle |
| Implementation plan | `RESON_MASTER_IMPLEMENTATION_PLAN.md` | Phase-by-phase status and evidence |
| This report | `RESON_DEPLOYMENT_READINESS_REPORT.md` | Deployment readiness assessment |

**Clean-deployment properties verified:** no absolute local paths, no
Windows-only imports at module scope, no dependency on a pre-existing
database file for startup, and no secret in any committed file.

---

## 4. Hosted deployment: the honest behaviour

A public Streamlit URL **cannot** collect CPU, memory, battery, process, or
event-log data from a visitor's computer. The executing code is on the
hosting server; the browser does not expose those APIs to a Streamlit app.

RESON implements two distinct, clearly separated experiences:

| Experience | Available today | Behaviour |
|---|---|---|
| **Hosted demonstration** | Yes | The deterministic simulator runs inside the hosted app. Every view is labeled `SIMULATED TELEMETRY` and the mode banner reads `MODE: DEMONSTRATION`. |
| **Hosted real monitoring** | **No** | The dashboard shows an explicit "no telemetry has been received from a remote collector" state. It never shows synthetic data as if it were live. |

This is enforced in code, not by convention:
`src/data/monitoring.py::resolve_mode` returns `available=False` with a
reason, and
`tests/test_streamlit_app.py::test_hosted_mode_does_not_claim_to_observe_the_visitor`
pins the behaviour.

### Making hosted real monitoring work

A separately running local collector must send telemetry to the hosted
instance over the authenticated ingestion path. The full requirement list —
agent authentication, per-host authorization, tenant isolation, transport
security, server-side validation, size and rate limits, replay protection,
schema versioning, secret rotation, safe errors, auditability — is in
`docs/deployment.md` §6 and in the implementation plan's Phase 5.

**That path is not implemented in this build.** It is the single largest
remaining functional gap.

---

## 5. Storage on a hosted platform

| Backend | Durable? | Shared? | Suitable for |
|---|---|---|---|
| `memory` | No (RAM only) | No | A public demonstration; per-session |
| `sqlite` | **No on a hosted platform** (ephemeral filesystem) | No | Local single-host monitoring only |
| `supabase` | Yes | Yes | Shared, multi-user monitoring (requires credentials) |

The dashboard states the backend and its durability in the sidebar, so a
hosted deployment cannot be mistaken for durable shared storage.

For a durable public deployment, configure Supabase credentials as platform
secrets **and** implement the ingestion path of §4.

---

## 6. Test results and failure classification

Full suite: **583 passed, 19 skipped, 0 failed**.

The 19 skips are live Supabase integration tests. They carry the
`requires_supabase` marker and are skipped with an explicit reason when the
credentials are absent; their assertions are unchanged and run in full when
the credentials are present. No assertion was weakened or deleted to obtain
a green suite.

| Test group | Classification | Now |
|---|---|---|
| `tests/test_storage.py` (12) | Live Supabase integration | Skipped without credentials |
| `tests/test_supabase_connection.py` (1) | Live Supabase integration | Skipped without credentials |
| `tests/test_config.py` (2 Supabase cases) | Needs Supabase credentials | Skipped without credentials |
| `tests/test_orchestrator.py` (1) | Needs Supabase credentials | Skipped without credentials |
| `tests/test_storage_selection.py` (2) | Needs Supabase credentials | Skipped without credentials |

The baseline before this work was **379 passed, 19 failed** with exactly the
same 19 tests. Those failures were always a missing secret, never a product
defect.

---

## 7. Security and privacy posture

**Implemented and verified:**

* Data minimization by default; no credentials, command-line arguments,
  environment values, packet payloads, or browser history are collected.
* Stable, non-secret, hashed host identity — never a username, path, or IP.
* Parameterized SQL throughout.
* Secrets never reach the frontend, session state, or logs.
* Source-aware queries prevent cross-source leakage in the UI.
* **Every record type carries its provenance** — metric, log, event,
  deployment, incident and evidence — in all three storage backends. A
  record that cannot be attributed is reported as `unknown`, never as live.
* Retention pruning bounds local growth; incidents are deliberately retained.
* Status indicators are derived from real collector health, never a session
  flag.
* Timestamps are stored in UTC and rendered in a named display timezone, so
  a local time on screen is always an accurate local time.

**Not implemented — blocking for a multi-tenant public deployment:**

| Gap | Consequence |
|---|---|
| No dashboard authentication or authorization | A public deployment exposes whatever its backend holds |
| No tenant isolation | One tenant could read another's telemetry |
| No remote ingestion | Hosted real monitoring is unavailable |
| No encryption at rest | The local SQLite file is unencrypted (OS-level disk encryption is the mitigation) |
| A Supabase project created before the provenance columns | Records written to it cannot be attributed; the backend detects this, reports it in the sidebar and Settings page, and refuses to filter by a column it does not have |

---

## 8. What the owner must do

1. **Review the committed work.** Commit `97361fa` is on `origin/main`
   (`feat: complete RESON observability platform with telemetry provenance`,
   73 files). Nothing was reset, cleaned, stashed, or rewritten.
2. **Choose a hosting target.** Streamlit Community Cloud is the path of
   least resistance; the artifacts are already in place.
3. **Set secrets** in the platform's secret store — only if using Supabase.
4. **Deploy** and verify:
   * the app builds and starts,
   * `/_stcore/health` returns `ok`,
   * the demonstration mode runs and is labeled,
   * the hosted mode correctly reports "unavailable".
5. **Decide the data story for the public instance.** With no remote
   ingestion, the public instance is a labeled demonstration. That is
   honest and useful, but it is not "live monitoring of your machine" —
   the README and the dashboard both say so.
6. **Run the collector on a real machine** (Windows and/or macOS) to convert
   those platforms from *implemented* to *validated*, following
   `docs/cross_platform.md` §6.
7. **Add the provenance columns to an existing Supabase project** if one
   already exists — `ALTER TABLE metrics ADD COLUMN source TEXT; ALTER TABLE
   metrics ADD COLUMN host_id TEXT;` and the equivalent for `logs`, `events`,
   `deployments`, `incidents` and `evidence`. Until then the backend reports
   that it cannot attribute records, and the dashboard shows those records
   as `unknown` rather than as live telemetry.

---

## 9. Honest summary of readiness

| Claim | Supported? |
|---|---|
| "RESON collects real telemetry from the computer it runs on" | **Yes** — validated on Linux, implemented on Windows/macOS |
| "RESON distinguishes demo data from live data" | **Yes** — source tagging, source-aware queries, mode banner, tested |
| "RESON never labels stale data as live" | **Yes** — derived status, tested |
| "RESON detects meaningful incidents with evidence" | **Yes** — deterministic, explainable pipeline with an evidence chain; every incident and evidence link carries the source it was detected from |
| "RESON shows my local time correctly" | **Yes** — stored in UTC, rendered in a named display timezone with DST applied per instant, tested across zones and DST boundaries |
| "RESON is deployed at a public URL" | **No** — not deployed; the owner performs this step |
| "A public RESON URL monitors your computer" | **No** — and RESON states this explicitly rather than implying otherwise |
| "RESON is production-secure for multi-tenant hosting" | **No** — see §7 |
