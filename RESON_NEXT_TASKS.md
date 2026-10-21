# RESON — Next Tasks

**Date written:** 2026-10-21 (end of the recovery + continuation session)
**Branch:** `main` · **HEAD at time of writing:** the commit that includes this file
**Verified state:** `583 passed, 19 skipped, 0 failed` (`python -m pytest -q`)

This file is the hand-off for the next working session. It is deliberately
separate from `RESON_MASTER_IMPLEMENTATION_PLAN.md` (the authoritative
project record) — that document holds the phase-by-phase status and the
evidence; this one holds only *what to do next*.

---

## 0. Read this first — the state you are picking up

* The implementation of Phases 0–4, 6–8 is **complete and verified**.
  Phases 1–3, 6–8 are implemented, tested and (for Linux) platform-validated.
* **Phase 5 remote ingestion is deliberately NOT implemented.** It is the
  single largest remaining functional gap, and it must stay disabled until
  the security requirements in `docs/deployment.md` §6 are met *and tested*.
* Two defects that broke the **production** (Supabase) backend were found and
  fixed in this session — see §2. Do not assume the Supabase path is
  well-tested: it has never run against a real Supabase project.
* The working tree was clean at the end of the session and everything is
  committed. `git status` should be empty.

### The three checks to run before you change anything

```bash
cd /path/to/RESON
git status                      # must be empty
git log --oneline -3
python -m pytest -q            # expect: 583 passed, 19 skipped, 0 failed
```

> **Warning — `git status` alone once under-reported the working tree.**
> During the recovery session the first `git status` omitted 7 modified
> files that `git diff HEAD --stat` reported correctly. Always cross-check
> with `git diff HEAD --stat` before concluding the tree is clean. This is
> documented in `RESON_MASTER_IMPLEMENTATION_PLAN.md` §0.2.

---

## 1. Owner actions (highest value — no code required)

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
run on real hardware**. RESON does not claim OS support until it does.

Procedure: `docs/cross_platform.md` §6. This is the only thing standing
between the current state and full cross-platform support.

After a successful run, update:
* `RESON_MASTER_IMPLEMENTATION_PLAN.md` §9 (platform-validation evidence),
* `RESON_DEPLOYMENT_READINESS_REPORT.md` §1 and §9,
* `docs/cross_platform.md` (support matrix),
* `README.md` (the limitations table).

### 1.4 Decide on remote ingestion — *design decision, not a task to rush*

Read `docs/deployment.md` §6 first. It lists the eleven requirements that
must be met and tested **before** the path may be enabled. Enabling it
without them would expose an endpoint that accepts arbitrary telemetry.

---

## 2. Technical next tasks (ordered by value)

### 2.1 Bound the correlation engine's pairwise work — *performance*

**Problem (measured, not estimated).** The full default demonstration
timeline (15 batches) produces ~18,900 candidate signal pairs and takes
**~53 s** end to end. A 5-batch timeline produces the same *kind* of result
in **~0.06 s**. The dashboard's "Run Local Demonstration Analysis" button
therefore blocks for the better part of a minute.

**Where:** `src/incidents/correlation.py` (pair evaluation), and
`src/incidents/service.py::derive_evidence` / `analyze`.

**Acceptance:**
* the full default demonstration completes in **under 10 s**;
* the result is **identical** — the incidents, their ids, their evidence
  chains and their severities must not change (ids are content-derived, so
  any change is a behaviour change, not a refactor);
* `scripts/benchmark.py` gains a section that reports the pair count and the
  wall-clock time for the default timeline.

**Suggested approach:** bucket signals by time window before pairing (the
correlation window is already 600 s), and short-circuit pairs that already
fail a cheap factor test before the expensive ones are computed.

### 2.2 Add CI for the offline suite

**Why:** there is no CI in this repository, so nothing prevents a regression
from reaching `main`.

**Acceptance:** a workflow that runs `python -m pytest -q --ignore=tests/test_storage.py --ignore=tests/test_supabase_connection.py` on Python 3.11 and 3.14 (the versions `docs/cross_platform.md` claims support for), with no Supabase credentials — the 19 live integration tests must skip cleanly.

### 2.3 Run the Supabase path against a real project — *unverified*

**Why:** the Supabase backend was **never executed against a real Supabase
project** in this environment. Its provenance behaviour is covered by
`tests/test_supabase_provenance.py` against a fake PostgREST client
(10 tests), which proves the *query construction* and the *policy*, but not
the wire behaviour.

**Acceptance:** with real credentials, the 19 currently-skipped tests pass; a metric round-trip preserves `source`/`host_id`; a source-filtered query returns only that source; and the schema probe correctly detects a table without the provenance columns.

### 2.4 Add macOS unified-log and Linux journald event readers

**Why:** Windows has a bounded, allowlisted event collector
(`src/data/windows_event_log.py`). The other two platforms have none, so
`docs/cross_platform.md` lists system-event collection as unsupported there.

**Where:** the collector interface already has a per-platform event-reader
seam (`src/data/collector.py`); the Windows implementation is the reference
for bounding, allowlisting, deduplication and the checkpoint file.

**Acceptance:** same guarantees as the Windows reader — explicit allowlist, bounded window and record limit, stable record ids, checkpoint deduplication, permission errors surfaced honestly — with a platform test suite that runs on any OS plus a platform validation note.

### 2.5 Add a `--scenarios` option to the standalone agent's demonstration path

**Why:** `run_demonstration(application, scenarios=...)` already accepts a
shorter simulation timeline (added in this session, used by the tests).
Exposing it through the UI and the CLI would let a user run a fast
demonstration instead of waiting ~53 s.

**Acceptance:** the sidebar's demonstration control offers "quick" and
"full"; the quick run is labelled as such and still produces incidents.

### 2.6 Audit the remaining `NotImplemented` / `Deferred` markers

Run:

```bash
grep -rn "NOT IMPLEMENTED\|Not implemented\|Deferred\|TODO\|FIXME" \
  --include=*.py --include=*.md src app docs tests
```

Each hit must still be *true*. Anything that has since been implemented
should be corrected — the documentation must never describe a feature as
missing when it exists, nor claim a feature exists when it does not.

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
* **Do not delete the untracked report files** (`IMPLEMENTATION_SUMMARY.md`,
  `RESON_AUDIT_REVIEW_REPORT.md`, `RESON_FINAL_IMPLEMENTATION_REPORT.md`,
  `RESON_IMPLEMENTATION_REPORT.md`). They are historical project records
  from earlier sessions. They are now tracked in Git, so they are safe.
