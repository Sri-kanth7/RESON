# RESON — Next Tasks

**Originally written:** 2026-10-21 (end of the recovery + continuation session)
**Revised:** 2026-10-22 (after the continual-improvement session)
**Branch:** `main` · **HEAD at time of writing:** `c10bb22 docs: record the commit, push and recovery checkpoint in the reports`, plus the uncommitted 2026-10-22 work
**Verified state:** `639 passed, 19 skipped, 0 failed` (`python -m pytest -q`)

This file is the hand-off for the next working session. It is deliberately
separate from `RESON_MASTER_IMPLEMENTATION_PLAN.md` (the authoritative
project record) — that document holds the phase-by-phase status and the
evidence; this one holds only *what to do next*.

> **What changed on 2026-10-22.** Every *technical* item of §2 below was
> completed in that session; the full evidence is in
> `RESON_SESSION_2026-10-22_REPORT.md` and in the master plan's §0.7.
> §2 is kept, with each item marked, because it is the acceptance record for
> that work. The *owner* items of §1 could not be done here: they need
> hosting credentials, a Supabase project, or Windows/macOS hardware.

---

## 0. Read this first — the state you are picking up

* The implementation of Phases 0–4, 6–8 is **complete and verified**.
  Phases 1–3, 6–8 are implemented, tested and (for Linux) platform-validated.
* **Phase 5 remote ingestion is deliberately NOT implemented.** It is the
  single largest remaining functional gap, and it must stay disabled until
  the security requirements in `docs/deployment.md` §6 are met *and tested*.
* Two defects that broke the **production** (Supabase) backend were found and
  fixed on 2026-10-21 — see the master plan §0.4. Do not assume the Supabase
  path is well-tested: it has never run against a real Supabase project.
* Two further defects were found and fixed on 2026-10-22 — see the master
  plan §0.7 (D9, D10).
* The 2026-10-22 changes are **in the working tree and not committed**:
  `git status` shows the modified and new files, `git diff HEAD --stat`
  cross-checks them.

### The three checks to run before you change anything

```bash
cd /path/to/RESON
git status                      # expect: the 2026-10-22 changes, uncommitted
git diff HEAD --stat            # cross-check the file list
git log --oneline -3
python -m pytest -q             # expect: 639 passed, 19 skipped, 0 failed
```

> **Warning — `git status` alone once under-reported the working tree.**
> During the recovery session the first `git status` omitted 7 modified
> files that `git diff HEAD --stat` reported correctly. Always cross-check
> with `git diff HEAD --stat` before concluding the tree is clean. This is
> documented in `RESON_MASTER_IMPLEMENTATION_PLAN.md` §0.2.

---

## 1. Owner actions (highest value — no code required)

*None of these can be done from a development container; all of them are
still open.*

### 1.1 Deploy the hosted dashboard — *not done, no public URL exists*

**Status:** deployment artifacts are ready and the app was verified to start
locally (`GET /_stcore/health` → HTTP 200 `ok`, `GET /` → HTTP 200), but
**nothing has been deployed and no public URL exists.** RESON does not claim
one.

Steps: `docs/deployment.md` §3.1–§3.3.

Verify after deploying:
* `/_stcore/health` returns `ok`;
* the demonstration mode renders and every view is labelled `SIMULATED
  TELEMETRY` with a `MODE: DEMONSTRATION` banner;
* the hosted mode reports *"no telemetry has been received from a remote
  collector"* — it must **not** fall back to synthetic data;
* the sidebar shows the backend and its durability (`memory` → *not durable*).

### 1.2 Add the provenance columns to an existing Supabase project

**Why:** a Supabase project created before RESON added provenance has no
`source`/`host_id` columns. Records written to it cannot be attributed and
are reported as `unknown` — never as live telemetry. The backend detects
this, reports it in the sidebar and on the Settings page, and refuses to
filter by a column it does not have.

The exact `ALTER TABLE` statements are in `docs/deployment.md` §3.4.

### 1.3 Validate Windows and/or macOS on real hardware

**Why:** both platforms are *implemented and unit-tested* but have **never
run on real hardware**. RESON does not claim OS support until it does. This
now includes the system-event readers: the Windows Event Log, the systemd
journal and the macOS unified log.

Procedure: `docs/cross_platform.md` §6. This is the only thing standing
between the current state and full cross-platform support.

After a successful run, update:
* `RESON_MASTER_IMPLEMENTATION_PLAN.md` §9 (platform-validation evidence),
* `RESON_DEPLOYMENT_READINESS_REPORT.md` §1 and §9,
* `docs/cross_platform.md` (support matrix),
* `README.md` (the limitations table).

### 1.4 Run the Supabase path against a real project — *unverified*

**Why:** the Supabase backend was **never executed against a real Supabase
project**. Its provenance behaviour is covered by
`tests/test_supabase_provenance.py` against a fake PostgREST client
(10 tests), which proves the *query construction* and the *policy*, but not
the wire behaviour.

**Acceptance:** with real credentials, the 19 currently-skipped tests pass;
a metric round-trip preserves `source`/`host_id`; a source-filtered query
returns only that source; and the schema probe correctly detects a table
without the provenance columns.

### 1.5 Decide on remote ingestion — *design decision, not a task to rush*

Read `docs/deployment.md` §6 first. It lists the eleven requirements that
must be met and tested **before** the path may be enabled. Enabling it
without them would expose an endpoint that accepts arbitrary telemetry.

---

## 2. Technical next tasks — **COMPLETED 2026-10-22**

Each item below was implemented, verified and documented. The evidence is in
`RESON_SESSION_2026-10-22_REPORT.md`; the acceptance record is kept here.

### 2.1 Bound the correlation engine's pairwise work — **DONE**

**Problem (measured, not estimated).** The full default demonstration
timeline (15 batches) produced ~18,900 candidate signal pairs and took
**~37 s (in-memory) / ~102 s (SQLite)** end to end. A 5-batch timeline took
~0.06 s.

**Root cause found.** The pair arithmetic was not the bottleneck: the engine
evaluated 18,721 pairs in ~0.9 s. The time went into **writing the 18,721
accepted evidence links**, one row per transaction, each opening and
committing its own SQLite connection.

**What was done.**
* `Storage.save_evidence_batch()` was added to the storage abstraction, with
  an efficient `executemany` implementation for SQLite, a single-request
  implementation for Supabase and a bulk implementation for the in-memory
  backend. `IncidentIntelligenceService.persist()` now writes the whole
  evidence chain in one call.
* The correlation engine prepares every per-signal value once (service,
  environment, metric-key set, identifier string, timestamp) instead of
  recomputing it for each of the ~18,700 pairs it appears in.
* `scripts/benchmark.py` gained section 7, which reports the pair count and
  the wall clock for the default timeline.

**Acceptance — met.**
* the full default demonstration completes in **under 10 s** — measured
  **0.83 s (in-memory) / 1.03 s (SQLite)**;
* the result is **identical** — `tests/test_correlation_equivalence.py` pins
  every decision (all six factors, the strength, the accept/reject decision,
  the explanation and the content-derived identifier) in a recorded digest,
  for the default configuration *and* for a custom one. The digest was taken
  from the engine before the optimisation and is unchanged after it;
* `scripts/benchmark.py` reports the pair count and the wall-clock time for
  the default timeline — **18,721 pairs, 194 signals, 15 batches**.

### 2.2 Add CI for the offline suite — **DONE**

`.github/workflows/ci.yml` runs
`python -m pytest -q --ignore=tests/test_storage.py --ignore=tests/test_supabase_connection.py`
on Python 3.11 and 3.14 with no Supabase credentials, byte-compiles every
module, and additionally runs the two ignored modules to verify that the 19
live integration tests **skip cleanly** with an explicit reason.

The workflow file has never been executed by a CI provider from this
environment; the exact commands it runs were executed locally and pass.

### 2.3 Run the Supabase path against a real project — **NOT POSSIBLE HERE**

This is owner item §1.4. It needs credentials and a reachable project.

### 2.4 Add macOS unified-log and Linux journald event readers — **DONE**

**Where:** `src/data/system_event_log.py` holds the shared contract — an
explicit allowlist, a bounded time window, a maximum number of records,
stable record identifiers, checkpoint de-duplication, and permission-aware
error reporting ("could not read" is never presented as "no events"). The
platform readers are `src/data/linux_journal.py` (`journalctl`) and
`src/data/macos_unified_log.py` (`log show`); the Windows reader keeps its
existing implementation and now shares the same collector. The agent selects
the reader per platform through `create_event_collector()`.

**Acceptance — met.** Same guarantees as the Windows reader, a platform test
suite that runs on any OS (29 tests in `tests/test_platform_event_logs.py`,
plus 3 in `tests/test_agent.py`), and a platform validation note: the real
`journalctl` binary was invoked on this machine (the container has no
journal entries, so no record was read and no error was reported).

### 2.5 Add a `--scenarios` option to the standalone agent's demonstration path — **DONE**

**Acceptance — met.** The demonstration pipeline moved to
`src/data/demonstration.py` so the CLI and the dashboard run the same code.
The sidebar offers **"Full timeline (15 batches)"** and **"Quick timeline
(5 batches)"** next to the demonstration button, the quick run is labelled
*"Quick simulated analysis ready"*, and it still produces an incident. The
command line gained `--demonstrate`, `--quick` and `--scenarios`.

### 2.6 Audit the remaining `NotImplemented` / `Deferred` markers — **DONE**

The markers that had become false (Linux/macOS system-event collection) were
corrected in `README.md`, `docs/cross_platform.md` and
`docs/security_and_privacy.md`. Every remaining marker is still true: remote
ingestion, dashboard authentication and tenant isolation.

---

## 3. Verification commands for the next session

```bash
# Full suite (19 live Supabase tests skip without credentials)
python -m pytest -q

# Offline suite only
python -m pytest -q --ignore=tests/test_storage.py --ignore=tests/test_supabase_connection.py

# The application actually starts and serves
streamlit run app/streamlit_app.py --server.headless true --server.port 8501 &
curl -s http://127.0.0.1:8501/_stcore/health     # expect: ok

# Real telemetry on this machine (Linux)
RESON_SQLITE_PATH=/tmp/reson-check.db python -m src.data.agent --backend sqlite --once

# Real system events on this machine (Linux)
RESON_SQLITE_PATH=/tmp/reson-check.db python -m src.data.agent --backend sqlite --once --events

# The demonstration, full and quick
python -m src.data.agent --backend memory --demonstrate
python -m src.data.agent --backend memory --demonstrate --quick

# Measurements (never invent a benchmark)
python scripts/benchmark.py

# The working tree is what you think it is
git status && git diff HEAD --stat
```

---

## 4. Things that must **not** be done casually

* **Do not enable remote ingestion** before the eleven requirements in
  `docs/deployment.md` §6 are implemented *and tested*. The hosted mode
  currently reports "unavailable", and that is the correct, honest
  behaviour.
* **Do not weaken or skip an assertion** to make the suite green. The 19
  Supabase tests are skipped with an *explicit reason* when the credentials
  are absent; their assertions are unchanged and run in full when the
  credentials are present.
* **Do not fabricate telemetry.** A capability the platform does not
  expose is reported as unsupported, not estimated.
* **Do not represent an unattributable record as live.** `source = None`
  reads back as `unknown`, and it must stay that way.
* **Do not run destructive tests against the user's real database.** Use
  `tmp_path` fixtures or a disposable copy; migration v4 was exercised
  against throwaway and hand-built legacy databases for exactly this reason.
* **Do not "optimize" the correlation engine past its recorded digest.**
  `tests/test_correlation_equivalence.py` is the contract: every factor,
  strength, decision, explanation and identifier is pinned. A change that
  moves the digest is a behaviour change, not a refactor — and note that
  `sum()` uses compensated summation, so even a hand-rolled accumulation
  changes the last bit of every strength.
* **Do not delete the untracked report files** (`IMPLEMENTATION_SUMMARY.md`,
  `RESON_AUDIT_REVIEW_REPORT.md`, `RESON_FINAL_IMPLEMENTATION_REPORT.md`,
  `RESON_IMPLEMENTATION_REPORT.md`). They are historical project records
  from earlier sessions. They are now tracked in Git, so they are safe.
