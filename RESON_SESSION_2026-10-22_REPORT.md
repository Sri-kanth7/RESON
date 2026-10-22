# RESON — Session Report 2026-10-22

**Session:** 2026-10-22 (continual-improvement session)
**Repository:** `/workspaces/RESON` · **Branch:** `main`
**HEAD at session start:** `c10bb22 docs: record the commit, push and recovery checkpoint in the reports`
**State at session end:** the work below is **in the working tree, not committed**
**Suite:** **639 passed, 19 skipped, 0 failed** (658 tests collected)

---

## 1. What this session was asked to do

The instruction was: *read every Markdown document in the repository, mainly
`reson_next_tasks.md`, complete the work, and write what was achieved in a
new, separate document.*

### Documents read

| Document | What it contributed |
|---|---|
| `RESON_NEXT_TASKS.md` | **The work list.** §1 owner actions, §2 technical tasks, §3 verification commands, §4 the "do not do this casually" list. |
| `RESON_MASTER_IMPLEMENTATION_PLAN.md` | The authoritative project record: phase status, the acceptance matrix, the test-evidence tables, the blockers list, and the recovery audit (§0) that explains the repository's conventions. |
| `RESON_DEPLOYMENT_READINESS_REPORT.md` | The readiness assessment; its measured numbers are the ones quoted in the documents I changed. |
| `RESON_IMPLEMENTATION_REPORT.md`, `RESON_FINAL_IMPLEMENTATION_REPORT.md`, `RESON_AUDIT_REVIEW_REPORT.md`, `IMPLEMENTATION_SUMMARY.md` | Historical records of earlier sessions. Read for context; **not edited** (the hand-off forbids touching them). |
| `README.md`, `docs/architecture.md`, `docs/deployment.md`, `docs/cross_platform.md`, `docs/security_and_privacy.md`, `docs/telemetry_sources.md`, `docs/troubleshooting.md`, `docs/development.md`, `design/.../DESIGN.md` | The user-facing documentation that had to be kept true. |

### Scope decision

`RESON_NEXT_TASKS.md` splits the work in two:

* **§1 — owner actions** (deploy the hosted dashboard, add the provenance
  columns to an existing Supabase project, validate Windows/macOS on real
  hardware, run the Supabase path against a real project, decide on remote
  ingestion). Every one of these needs hosting credentials, a Supabase
  project, or hardware that does not exist in this container.
  **They remain open and are still reported as open.**
* **§2 — technical tasks** (performance, CI, macOS/Linux event readers, the
  `--scenarios` option, the marker audit). These are code work.

**This session completed every §2 technical task.** §2.3 (the live Supabase
run) is the one exception: it is not a code task, it needs a real project,
and it was moved to the owner list (§1.4) rather than silently dropped.

---

## 2. State verified before starting

```bash
git status && git diff HEAD --stat   # clean tree, nothing modified
git log --oneline -3                 # c10bb22, 97361fa, 4ce1662
python -m pytest -q                  # 583 passed, 19 skipped, 0 failed (53.9 s)
```

The hand-off's warning about `git status` under-reporting the tree was
respected: `git status` and `git diff HEAD --stat` were cross-checked at the
start and at the end of the session.

---

## 3. Task 2.1 — Bound the correlation engine's pairwise work

### 3.1 The measurement did not match the hand-off

The hand-off says the full default demonstration "takes ~53 s end to end"
and produces "~18,900 candidate signal pairs". The pair count was right
(**18,721**). The timing was not: on this machine the demonstration took
**~2 s with the in-memory backend** and **37 s (warm) to 102 s (cold) with
SQLite**. Profiling located the cost precisely:

| Stage | Before (measured) |
|---|---|
| Build the dataset | 0.003 s |
| Persist the simulated telemetry | 0.04–0.11 s |
| Baselines + behaviour analysis | 0.007 s |
| Derive deviations and change points | 0.002 s |
| **Correlation engine (18,721 pairs)** | **0.87–0.93 s** |
| **Persist incidents + 18,915 evidence links** | **0.05 s (memory) / 37–102 s (SQLite)** |

**The bottleneck was not the pair arithmetic.** It was the *write*: the
evidence chain was stored one row per transaction, and every row opened and
committed its own SQLite connection.

### 3.2 Two changes

**a. One batched write for the evidence chain.**
`Storage.save_evidence_batch()` was added to the storage abstraction. The
default implementation writes each record through `save_evidence`, so no
backend can be left inconsistent; SQLite overrides it with a single
`executemany` in one transaction, Supabase with one request, and the
in-memory backend with one bulk update.
`IncidentIntelligenceService.persist()` now collects the chain's links and
writes them in one call. The set of stored records and their identifiers is
unchanged — the same links, the same de-duplication by evidence id.

**b. Prepare each signal once.**
The engine evaluates every distinct pair, so the *output* is quadratic by
construction; what the implementation controls is the cost of one pair.
Every value that depends on a single signal only — its service, its
environment, its metric-key set, its identifier string, its timestamp — is
now computed once per signal (`_PreparedSignal`) and reused for each of the
~193 pairs that signal appears in, instead of being rebuilt for every pair.
The factor set, the thresholds, the decision rule and the identifiers are
untouched.

### 3.3 The result is identical — and proved to be

> "the result is **identical** — the incidents, their ids, their evidence
> chains and their severities must not change"

This is the acceptance criterion that matters most, so it was verified two
ways.

**1. A direct A/B against the previous implementation.** The pre-change
`src/incidents/correlation.py` was extracted from Git (`git show
HEAD:src/incidents/correlation.py`) and loaded side by side with the new
one. Both were run over a 36-signal set that spans more than the
correlation window (so the time-bounded gate rejects some pairs) and mixes
services, environments, signal types and provenance:

```
candidates: 630 630 equal: True
evidence: 137 137 equal: True
custom config candidates equal: True evidence equal: True
```

Every field of every candidate and every evidence link — endpoints,
relationship type, reference timestamp, service/environment relationship, all
six factors, the strength, the acceptance decision, the explanation, the
identifier and the provenance — is **identical**.

**2. A permanent regression net.** `tests/test_correlation_equivalence.py`
records a SHA-256 digest of that whole decision set (for the default
configuration *and* for a custom one) and fails if it ever moves. It also
asserts that every materialized link keeps the documented content-derived
identifier, and that evaluating a pair directly gives the same candidate as
the sweep.

> **A floating-point trap worth recording.** The first version of the
> optimisation replaced `sum(...)` with a hand-rolled accumulation loop. The
> A/B check caught it: CPython applies *compensated (Neumaier) summation* to
> floats inside `sum()`, so the naive accumulation produced
> `0.4285714285714285` where `sum()` produced `0.4285714285714286` — a
> different last bit in every strength value. The `sum()` call is now
> deliberate, and the comment in the code says why.

### 3.4 Measured result

| Measurement | Before | After |
|---|---|---|
| Default demonstration, in-memory | ~2.0 s | **0.65–0.83 s** |
| Default demonstration, SQLite | **37–102 s** | **0.88–1.03 s** |
| Correlation engine alone (18,721 pairs) | 0.87–0.93 s | 0.61–0.66 s |
| Quick demonstration (5 batches) | ~0.06 s | ~0.06 s |

Acceptance was "under 10 s": met with an order of magnitude of headroom.

`scripts/benchmark.py` gained **section 7**, which reports the pair count and
the wall clock for the default timeline, and asserts that the in-memory and
SQLite runs produce the same number of pairs, links and incidents:

```
7. Default demonstration timeline (correlation engine)
   scenario batches:          15
   telemetry records:          278
   signals evaluated:          194
   candidate signal pairs:     18,721
   accepted evidence links:    18,721
   incidents produced:         1
   wall clock (in-memory):     0.65 s
   wall clock (sqlite):        0.88 s
```

---

## 4. Task 2.2 — CI for the offline suite

`.github/workflows/ci.yml` was added. It runs on every push and pull request
to `main`:

* the offline suite —
  `python -m pytest -q --ignore=tests/test_storage.py --ignore=tests/test_supabase_connection.py`
  — on **Python 3.11 and 3.14** (the versions `docs/cross_platform.md`
  states support for), with **no Supabase credentials**;
* a byte-compilation pass over `src`, `app` and `scripts`;
* a separate step that runs the two ignored modules and asserts that the
  live integration tests **skip cleanly with an explicit reason** — the
  acceptance criterion's "the 19 live integration tests must skip cleanly".

Both commands were executed locally and pass:

```
639 passed, 5 skipped in 48.35 s     # the CI command
14 skipped in 0.29 s                 # the live-test skip check
```

**Honest limitation:** the workflow file has never been executed by a CI
provider, because none is reachable from this container. The file is the
deliverable; the commands it runs were run by hand.

---

## 5. Task 2.3 — Run the Supabase path against a real project

**Not possible here, and not attempted.** It needs `SUPABASE_URL` and
`SUPABASE_SECRET_KEY` for a reachable project, plus network access. There
is no `.env` in the repository and no credential in the environment.

What *was* done to reduce the risk of the first real run:

* the backend's provenance behaviour remains covered by the 10 offline tests
  in `tests/test_supabase_provenance.py` (fake PostgREST client);
* the incident/evidence writes on Supabase were changed from `insert` to
  `upsert`, so re-saving an incident cannot fail on the primary key (see
  §9, D9);
* `save_evidence_batch` was implemented for Supabase as a single `upsert`
  request, matching the batched write the local backends use.

The item is carried forward as owner action §1.4 with its original
acceptance criteria.

---

## 6. Task 2.4 — macOS unified-log and Linux journald event readers

### 6.1 The shared contract

`src/data/system_event_log.py` holds the part that must not be reinvented
per platform:

* an explicit **allowlist** — RESON never ingests a whole event log;
* a **bounded window** and a **maximum number of records**;
* **stable record identifiers**, so the same event is never ingested twice;
* **checkpoint de-duplication**, so a restart continues where it left off;
* **permission-aware errors** — a log that cannot be read is reported as
  unavailable *with the reason*, never as "there were no events";
* **content-minimal** collection — only the event's own message text, bounded
  in length.

`src/data/windows_event_log.py` was refactored onto this contract (its 20
existing tests still pass unchanged), and the two new readers were added:

| Platform | Module | Source | Reader |
|---|---|---|---|
| Windows | `src/data/windows_event_log.py` | `windows_local` | `pywin32`, falling back to `wevtutil` |
| Linux | `src/data/linux_journal.py` | `linux_local` | `journalctl --output=json` |
| macOS | `src/data/macos_unified_log.py` | `macos_local` | `log show --style=json` |

The agent now selects the reader per platform through
`create_event_collector()` in `src/data/collector_factory.py`; the platform
modules are imported lazily, so one platform's tooling can never break
another.

### 6.2 The allowlists are explicit

* Linux (`JOURNAL_MATCHES`): `kernel`, `sshd.service`,
  `systemd-networkd.service`, `systemd-resolved.service`, `cron.service`,
  `dbus.service` — and within them only the `emerg`/`alert`/`crit`/`err`
  syslog priorities.
* macOS (`DEFAULT_ALLOWED_CHANNELS`): the `kernel`, `loginwindow` and
  `mDNSResponder` processes — and within them only the `Error` and `Fault`
  message types.

### 6.3 Stable identifiers

* Linux: the journal's own `__CURSOR` (`journal:s=abc;i=1;b=2`), with a
  content hash as a fallback when the cursor is absent.
* macOS: a SHA-256 digest of the entry's own content
  (`unified:<32 hex chars>`).

### 6.4 Evidence

* **32 new tests** — `tests/test_platform_event_logs.py` (29) and
  `tests/test_agent.py` (+3). They cover, for both platforms: the allowlist,
  the bounded window, the record limit, de-duplication within a batch and
  across collections, checkpoint survival across a restart, corrupt
  checkpoints, permission errors surfaced rather than hidden, an empty log
  that is not an error, the source stamped on every record, the parsers
  against recorded command output, and the per-platform reader selection.
* **A live run on this machine.** The real `journalctl` binary was invoked
  (`python -m src.data.agent --backend sqlite --once --events`): the agent
  ingested 97 metric records and collected 0 events, because **this
  container has no journal entries**. The important part is that it reported
  *"no events"*, not an error — the honest-empty-state path is proven on
  real hardware.

> **What is not proven:** no journal *entry* has been read on real hardware
> (this container's journal is empty), and no macOS machine was available at
> all. Both platforms remain *implemented, platform validation pending*, and
> the documentation says so.

---

## 7. Task 2.5 — `--scenarios` for the standalone agent's demonstration path

### 7.1 The pipeline moved into `src/`

`run_demonstration()` lived in the dashboard layer (`app/services/backend.py`),
which the command line cannot import without inverting the layering. It now
lives in **`src/data/demonstration.py`** and both the dashboard and the CLI
call the same code. `app/services/backend.py` re-exports it, so the pages are
unchanged.

The module also owns the timeline profiles:

| Profile | Scenarios | Batches |
|---|---|---|
| `full` | the default timeline | 15 |
| `quick` | `normal, normal, normal, error_spike, normal` | 5 |

The quick profile is not a shortcut: it is the same builder, the same
ingestion, the same baselines, the same detectors and the same persistence
over a smaller window. It still detects an incident.

### 7.2 The command line

```bash
python -m src.data.agent --backend sqlite --demonstrate
python -m src.data.agent --backend sqlite --demonstrate --quick
python -m src.data.agent --backend sqlite --demonstrate \
    --scenarios normal,normal,normal,error_spike,normal
```

Measured:

```
Demonstration complete: 278 simulated record(s), 1 incident(s), timeline=full, 1.10s.
Demonstration complete: 91 simulated record(s), 1 incident(s), timeline=quick, 0.06s.
```

An unusable `--scenarios` value exits with status **2** and an explicit
message rather than running something unexpected.

### 7.3 The sidebar

The sidebar now offers **"Full timeline (15 batches)"** and **"Quick timeline
(5 batches)"** next to the demonstration button. The quick run is labelled
*"Quick simulated analysis ready"*, so the reader knows which run produced
the numbers. Both are covered by `AppTest` tests.

---

## 8. Task 2.6 — Audit of the `Not implemented` / `Deferred` markers

The command from the hand-off was run over `src`, `app`, `docs`, `tests` and
`scripts`:

```bash
grep -rn "NOT IMPLEMENTED\|Not implemented\|Deferred\|TODO\|FIXME" \
  --include=*.py --include=*.md src app docs tests
```

**Three markers had become false** and were corrected in the same pass:

| Marker | Was | Now |
|---|---|---|
| `docs/cross_platform.md` — Linux "System events \| Not implemented" | wrong | implemented (`src/data/linux_journal.py`) |
| `docs/cross_platform.md` — macOS "System events \| Not implemented" | wrong | implemented (`src/data/macos_unified_log.py`) |
| `README.md` — "macOS unified log collection \| **Not implemented**" | wrong | implemented, platform validation pending |

**Every remaining marker is still true**, and was left in place:

* remote ingestion — not implemented (the hosted mode reports
  "unavailable", which is the honest behaviour);
* dashboard authentication and tenant isolation — not implemented;
* the `imported` source — not implemented in this build.

`docs/security_and_privacy.md` §9 was corrected too: the entry that said
"macOS unified log is not read" now says the reader is implemented and
*platform validation pending*.

---

## 9. Two further defects found and fixed

Neither was in the hand-off; both were found while doing the work.

| # | Defect | Severity | Fix |
|---|---|---|---|
| **D9** | `save_incident` used a plain `INSERT` on SQLite and Supabase. Re-saving an incident — which is exactly what the dashboard's **mark as investigating** action does — failed with a primary-key violation on a persistent backend. The in-memory backend already replaced by id, so the backends disagreed. | Product defect, persistent backends | Incidents and evidence are now **upserts** on SQLite and Supabase, matching the in-memory semantics. Covered by a new test. |
| **D10** | System events were ingested with `host_id=None`, and the new Linux/macOS readers would also have produced events without a source, so an event could not be attributed to a machine. | Provenance defect | The agent now falls back to the collector's host id and source when a reader does not stamp them. |

---

## 10. Test evidence

| Run | Result |
|---|---|
| Baseline at session start | **583 passed, 19 skipped, 0 failed** (53.9 s) |
| Final full suite | **639 passed, 19 skipped, 0 failed** (658 tests collected) |
| Final offline suite (the CI command) | **639 passed, 5 skipped, 0 failed** |
| The 19 skips | The live Supabase integration tests, skipped with an **explicit reason** when the credentials are absent. Their assertions are unchanged and run in full when the credentials are present. No assertion was weakened, deleted or hidden. |

**56 new tests**, all of them written for this session:

| File | Tests | Covers |
|---|---|---|
| `tests/test_correlation_equivalence.py` | 4 | The recorded digest of every correlation decision (factors, strengths, accept/reject, explanations, identifiers), for the default *and* a custom configuration; the direct-vs-sweep equivalence; the content-derived identifiers |
| `tests/test_demonstration_performance.py` | 5 | The demonstration's wall clock against the 10 s budget (memory *and* SQLite); one batched write instead of 18,721; the batched and per-row writes storing the same records; re-saving an incident updating it |
| `tests/test_demonstration_profiles.py` | 9 | The full/quick profiles, the explicit scenario list, the labelling, and that the quick run still detects incidents |
| `tests/test_platform_event_logs.py` | 29 | The Linux journal and macOS unified log: allowlists, bounding, de-duplication, checkpointing, parsing, error handling, reader selection, and a live run against the real `journalctl` |
| `tests/test_agent.py` | +7 | Event ingestion with provenance, a failed event read not stopping metric collection, the platform-selected reader, and the `--demonstrate` / `--quick` / `--scenarios` command line |
| `tests/test_streamlit_app.py` | +2 | The sidebar's quick/full control, and that the quick run is labelled and still produces the incident registry |

### Runtime verification

| Check | Result |
|---|---|
| `streamlit run app/streamlit_app.py` | starts; `GET /_stcore/health` → **HTTP 200 / `ok`**; `GET /` → **HTTP 200** (re-verified after the changes, port 8599) |
| All 8 pages × 4 modes via `AppTest` | **no exceptions** |
| `python -m src.data.agent --backend sqlite --once` | 97 real metric records, `source=linux_local`, stable host id |
| `python -m src.data.agent --backend sqlite --once --events` | runs the real `journalctl`; 0 events (no journal in this container) and **no error** |
| `python -m src.data.agent --backend sqlite --demonstrate` | 278 simulated records, 1 incident, `timeline=full` |
| `python -m src.data.agent --backend sqlite --demonstrate --quick` | 91 simulated records, 1 incident, `timeline=quick`, 0.06 s |
| Secret scan across the repository | **no secrets found**; no `.env` present |
| Test isolation | tests write only to `tmp_path`; the user data directory is left clean |

---

## 11. Files changed

**Modified (23):** `README.md`, `RESON_DEPLOYMENT_READINESS_REPORT.md`,
`RESON_MASTER_IMPLEMENTATION_PLAN.md`, `RESON_NEXT_TASKS.md`,
`app/services/backend.py`, `app/streamlit_app.py`, `docs/architecture.md`,
`docs/cross_platform.md`, `docs/deployment.md`,
`docs/security_and_privacy.md`, `scripts/benchmark.py`,
`src/data/agent.py`, `src/data/collector_factory.py`,
`src/data/memory_storage.py`, `src/data/sqlite_storage.py`,
`src/data/storage.py`, `src/data/supabase_storage.py`,
`src/data/windows_event_log.py`, `src/incidents/correlation.py`,
`src/incidents/models.py`, `src/incidents/service.py`, `tests/test_agent.py`,
`tests/test_streamlit_app.py`

**New (9):** `.github/workflows/ci.yml`, `src/data/demonstration.py`,
`src/data/system_event_log.py`, `src/data/linux_journal.py`,
`src/data/macos_unified_log.py`, `tests/test_correlation_equivalence.py`,
`tests/test_demonstration_performance.py`,
`tests/test_demonstration_profiles.py`, `tests/test_platform_event_logs.py`

**Untouched, deliberately:** the four historical report files
(`IMPLEMENTATION_SUMMARY.md`, `RESON_AUDIT_REVIEW_REPORT.md`,
`RESON_FINAL_IMPLEMENTATION_REPORT.md`, `RESON_IMPLEMENTATION_REPORT.md`).

**No Git operation was performed**: no commit, push, reset, clean, stash,
rebase or history rewrite. The work is in the working tree for review.

---

## 12. Documentation updated

| Document | Change |
|---|---|
| `RESON_MASTER_IMPLEMENTATION_PLAN.md` | New §0.7 (this session's scope, the two new defects D9/D10), the module list, Phase 2 task 2.10, Phase 7 task 7.4, the acceptance matrix, the status table, the test-evidence tables, the platform-validation table, the blockers table (§11) and the next action (§12) |
| `RESON_NEXT_TASKS.md` | Rewritten as the hand-off for the *next* session: every §2 item marked with its acceptance record, the owner items kept and made explicit, and the verification commands extended with the new ones |
| `RESON_DEPLOYMENT_READINESS_REPORT.md` | Re-measured counts, the new capabilities in the summary, the measured performance table, and a new §10 recording what changed and what is still not done |
| `README.md` | The new CLI options, the demonstration section, the package layout, and the corrected limitations table |
| `docs/cross_platform.md` | The event-collection support matrix, the Linux journal and macOS unified-log sections, their permissions and limitations, and the extended validation procedure |
| `docs/architecture.md` | The new system-event layer and the new test files |
| `docs/security_and_privacy.md` | The corrected known-gaps row and the system-event data row |
| `docs/deployment.md` | The `--events` description |

---

## 13. What was **not** done, and why

These remain open, and every document still says so:

| Item | Why |
|---|---|
| Deploy the hosted dashboard | Needs the owner's hosting account. No public URL exists and none is claimed. |
| Add the provenance columns to an existing Supabase project | Needs access to the owner's project. |
| Validate Windows and/or macOS on real hardware | No such hardware exists in this container. Both platforms — and their event readers — remain *implemented, platform validation pending*. |
| Run the Supabase path against a real project | Needs credentials and network access to a real project. |
| Implement remote ingestion | Deliberately not implemented until the eleven requirements in `docs/deployment.md` §6 are met **and tested**. The hosted mode reports "unavailable", which is the correct behaviour. |
| Dashboard authentication and tenant isolation | Blocked behind remote ingestion. Documented as a gap. |

---

## 14. How to verify this session's work

```bash
cd /workspaces/RESON

# 1. The suite
python -m pytest -q                    # expect: 639 passed, 19 skipped, 0 failed
python -m pytest -q --ignore=tests/test_storage.py \
    --ignore=tests/test_supabase_connection.py     # the CI command

# 2. The performance claim
python scripts/benchmark.py            # section 7: 18,721 pairs, ~0.7-1.0 s

# 3. The behaviour claim (the correlation digest)
python -m pytest -q tests/test_correlation_equivalence.py

# 4. The demonstration, from the CLI
python -m src.data.agent --backend memory --demonstrate --quick

# 5. The event readers
python -m src.data.agent --backend sqlite --once --events
python -m pytest -q tests/test_platform_event_logs.py

# 6. The application still starts and serves
streamlit run app/streamlit_app.py --server.headless true --server.port 8501 &
curl -s http://127.0.0.1:8501/_stcore/health     # expect: ok

# 7. The working tree is what you think it is
git status && git diff HEAD --stat
```
