# RESON SQLite Concurrency Fix Report

Date: 2026-10-20
Scope: `src/data/sqlite_storage.py`, `tests/test_sqlite_storage.py`
Branch state: uncommitted working-tree changes on `main` (nothing committed or pushed)

## Symptom

On Windows:

```
python -m pytest tests/test_sqlite_storage.py -q
```

reported 38 passed / 1 failed in ~177 s. The failing test,
`test_concurrent_writes_from_multiple_threads`, raised:

```
sqlite3.OperationalError: database is locked
```

with eight worker threads writing 25 batches × 5 metrics each into one
database file through a shared `SQLiteStorage` instance.

## Root cause (supported by instrumentation)

Reproduced the failure, then instrumented `_connect`, `_ensure_schema`, and
the operation runner to identify the exact failing statement. Two defects
were confirmed:

1. **`PRAGMA journal_mode = WAL` ran on every connection open**
   (`_connect`, old line 249). SQLite does **not** invoke its busy handler
   for journal-mode changes, so the 30-second `busy_timeout` could not
   protect this statement. During initial database setup, eight threads open
   connections simultaneously while other threads already hold write locks
   (schema DDL / inserts); the pragma then fails *immediately* with
   `database is locked`. Instrumentation output:

   ```
   FAIL AT STEP: journal_mode
   sqlite3.OperationalError: database is locked
   ```

   The failure occurred far faster than the 30 s busy timeout (whole
   workload finished in 23.5 s), proving the busy handler was never
   consulted for this statement. A separate probe confirmed the pragma is
   harmless when the database is *already* in WAL mode and quiescent — the
   failure is specific to fresh-file setup under concurrent write activity.
   WAL mode persists in the database file header, so re-asserting it on
   every open was always unnecessary.

2. **Schema initialization was not thread-safe.** `_ensure_schema` used a
   plain `self._schema_ensured` boolean with no synchronization, so all
   eight threads raced through `conn.executescript(...)` DDL concurrently.
   That produced sustained write-lock contention (the single test consumed
   ~45 s, largely busy-handler waiting; other simple tests in the file were
   also multi-second).

Increasing the timeout alone was therefore not sufficient: the failing
statement never consulted the busy handler, and the schema stampede would
have kept the suite pathologically slow regardless.

## Files changed

### `src/data/sqlite_storage.py`

- **One-time WAL setup.** New `_ensure_wal_mode()` sets
  `PRAGMA journal_mode = WAL` once per process per database file, guarded by
  a process-wide `threading.Lock` and a path memo (`_WAL_CONFIGURED_PATHS`),
  with the same bounded retry used everywhere else. The pragma was removed
  from the per-open hot path; `_connect()` now only sets
  `foreign_keys`/`busy_timeout` and confirms WAL when needed. Connections
  that fail during setup are closed before the error propagates (previously
  they leaked).
- **Write serialization.** `SQLiteStorage` gained a per-instance
  `threading.Lock` (`_write_lock`). All eleven `save_*` methods now route
  through a new `_write_op()`, which holds that lock for the whole
  transaction (connect → schema check → execute → commit/rollback → close).
  Reads (`get_*`, `_counts`, `health`) continue through unlocked `_op()`;
  WAL allows them to run while a write is in flight, so the collector can
  publish while the UI reads.
- **Thread-safe schema init.** `_ensure_schema` now double-checks a
  `_schema_lock`, so exactly one thread runs the DDL script and no other
  operation can observe a half-created schema.
- **Bounded retry for transient lock errors.** New
  `_call_with_lock_retry()` (5 attempts, exponential backoff 50 ms → 1 s
  cap) retries **only** `sqlite3.OperationalError` whose message reports a
  locked/busy database. Each retry re-runs the full transaction on a fresh
  connection, which is safe because the failed attempt was rolled back in
  the `except`/`finally` path first. Constraint errors, corruption,
  read-only databases, and all other errors surface unchanged after the
  first attempt — nothing is swallowed and no write is silently dropped.
- **`_counts()`** now routes through `_op()` so it gets the same
  connection management, schema safety, and retry behavior; `health()`
  semantics are unchanged.
- The module docstring documents the concurrency design and rationale.

The public `Storage` interface and all observable behavior (return values,
exception types, inclusive time-range semantics, schema version, WAL
journal mode, `-wal`/`-shm` sidecar files) are unchanged.

### `tests/test_sqlite_storage.py`

- Existing `test_concurrent_writes_from_multiple_threads` is unchanged in
  workload and assertions (8 threads × 25 batches × 5 metrics, exact
  record-count and value-set equality, worker exceptions collected and
  asserted empty).
- **New** `test_concurrent_reads_while_writes_are_in_progress`: 4 writer
  threads and 4 reader threads start on a barrier; readers poll
  `_counts()`/`get_metrics()` while writers publish; any thread exception
  fails the test; final row count is exact. Readers also verify committed
  state is valid mid-write: each successive count read is non-decreasing
  (writers only insert), and every observed row must match the exact
  writer encoding (service, unit, metric name derived from the value, and
  membership in the precomputed set of expected values), so a torn or
  partial row would fail deterministically. No sleeps are used.
- **New** `test_failed_write_rolls_back_completely_and_releases_lock`: a
  batch containing a duplicate primary key must raise
  `sqlite3.IntegrityError` (no retry, no swallowing), leave zero partial
  rows, and release the write lock so a subsequent write succeeds.
- **New** `test_wal_journal_mode_is_enabled_and_persistent`: connections
  report `journal_mode = wal`, including a second instance re-opening the
  same file (validating the one-time WAL setup).

## Concurrency strategy implemented

1. WAL enabled once per process per file (serialized, bounded-retry),
   persisting across instances and processes via the DB file header.
2. Per-instance write lock serializes all `save_*` transactions; reads stay
   concurrent (WAL single-writer/multi-reader).
3. Schema DDL executed by exactly one thread per instance.
4. Every operation: fresh connection, `busy_timeout = 30000` +
   `timeout=30.0`, commit on success, rollback on failure, close in
   `finally` — plus a bounded (5-attempt) backoff retry strictly limited to
   transient locked/busy errors.
5. Cross-process contention (not covered by the in-process lock) relies on
   the busy handler first, then the bounded retry.

## Tests executed and results

Environment: Windows 11, Python 3.13.15, SQLite 3.50.4, pytest (venv).

| Command | Result | Runtime |
|---|---|---|
| `python -m pytest tests/test_sqlite_storage.py -q` (before fix, reproduced) | 1 failed, 39 run: `test_concurrent_writes_from_multiple_threads` — `database is locked` | 45.77 s (test) |
| `python -m pytest tests/test_sqlite_storage.py -q` (after fix) | **42 passed** (39 original + 3 new) | 302.71 s |
| `python -m pytest tests/test_sqlite_storage.py -q --durations=12` (after fix) | **42 passed** | 273.67 s |
| `python -m pytest tests/ -q` (after fix) | **384 passed, 14 failed** (all failures pre-existing/network-dependent, see below) | 337.15 s |

Repeated concurrency regression
(`tests/test_sqlite_storage.py::test_concurrent_writes_from_multiple_threads`,
one fresh database per run):

| Run | Result | Runtime |
|---|---|---|
| 1 | passed | 127.94 s |
| 2 | passed | 44.37 s |
| 3 | passed | 53.72 s |
| 4 | passed | 57.59 s |
| 5 | passed | 60.72 s |
| 6 | passed | 41.70 s |
| 7 | passed | 97.22 s |
| 8 | passed | 53.25 s |
| 9 | passed | 67.21 s |
| 10 | passed | 62.01 s |

**10 / 10 passed, zero intermittent failures.**

`git diff --check`: clean (exit 0). `git status` before and after: only
`src/data/sqlite_storage.py` and `tests/test_sqlite_storage.py` modified;
nothing committed or pushed; no unrelated files touched.

## Post-review cleanup and validation (this run)

The read-only review found no correctness defects and requested two small
improvements, both applied without touching production storage code:

1. The prose runtime range above was corrected from `44–128 s` to
   `41.70–127.94 s` to match the authoritative test table.
2. The reader loop in `test_concurrent_reads_while_writes_are_in_progress`
   previously asserted only `current >= 0` (trivially true). It now records
   observations and asserts two deterministic invariants during concurrent
   writes, using the existing barrier start plus per-reader recorded state
   (no sleeps, no timing assumptions):
   - **Monotonic committed state**: each successive `_counts()` observation
     by the same reader is non-decreasing, since writers only insert.
   - **Row validity**: every row returned by `get_metrics()` must have the
     expected service, unit, a metric name matching its value's encoded
     writer index, and a value in the precomputed set of the 200 records
     the writers commit — a torn or partial row fails deterministically.

Validation actually executed on **Windows 11** in this run:

| Command | Result | Runtime |
|---|---|---|
| `python -m pytest tests/test_sqlite_storage.py -q` | **42 passed** | 281.23 s |
| Both concurrency tests repeated 5× (run 1) | 2 passed | 150.85 s |
| Both concurrency tests repeated 5× (run 2) | 2 passed | 440.73 s |
| Both concurrency tests repeated 5× (run 3) | 2 passed | 238.35 s |
| Both concurrency tests repeated 5× (run 4) | 2 passed | 117.44 s |
| Both concurrency tests repeated 5× (run 5) | 2 passed | 90.04 s |

**5 / 5 repeat runs passed** (each run covering
`test_concurrent_writes_from_multiple_threads` and the strengthened
`test_concurrent_reads_while_writes_are_in_progress`), zero failures.
Runtime variance between runs is filesystem/antivirus load, not lock
contention (see runtime note below).

`git diff --check`: clean (exit 0). `git status --short`: exactly
` M src/data/sqlite_storage.py`, ` M tests/test_sqlite_storage.py`,
`?? RESON_SQLITE_CONCURRENCY_FIX_REPORT.md` — nothing committed, pushed,
stashed, reset, or deleted.

## Full-suite failures and classification

The 14 failures in `python -m pytest tests/ -q` are all
`httpx.ConnectError: [Errno 11001] getaddrinfo failed` from live-network
tests against a Supabase instance:

- `tests/test_storage.py` — 13 tests (Supabase-backed storage suite)
- `tests/test_supabase_connection.py` — 1 test

Classification: **environment / credential-dependent, pre-existing**. Verified
by stashing the fix and re-running `tests/test_supabase_connection.py` on
the pristine tree — identical failure. These tests exercise
`SupabaseStorage`, a module untouched by this change. No regression was
introduced by this fix; every non-network test in the suite passes.

## Runtime note (pre-existing, not a regression)

This machine's filesystem (likely antivirus scanning of `%TEMP%` SQLite
files) makes every *first* touch of a brand-new database file cost ~3–10 s,
and each commit cost tens to hundreds of milliseconds — measured at
**9.02 s** for the first operation on a fresh file with the original code
vs **9.68 s** with the fix (subsequent operations: 5–10 ms in both), and
200 sequential single-threaded commits taking 16.13 s with raw `sqlite3`
and no locks involved. This is why individual tests in the file take
seconds even single-threaded, and why the concurrency test needs tens of
seconds to complete 200 committed batches. The pre-fix test reached 45 s
only while *failing* (half the threads aborted instantly on the pragma
error and the rest busy-waited); the fixed test takes 41.70–127.94 s while
completing 100 % of the work. All lock-wait instrumentation post-fix shows
no 30 s busy-timeout events (max single operation 10.4 s, all successes).

## Platforms tested

- **Windows**: all results above were produced on Windows 11 — the
  originally reported platform. Verified fixed here.
- **Linux**: not available in this session; Linux validation remains
  outstanding. The fix uses only portable `sqlite3` + `threading`
  primitives (no Windows-specific code paths), and WAL + busy timeouts are
  the documented cross-platform SQLite concurrency mechanism, but Windows
  results do not prove Linux correctness.

## Remaining limitations

- **Multiple processes / multiple instances concurrently first-touching a
  brand-new database file** can still interleave DDL: WAL setup is retried
  across processes, and each instance's schema script is idempotent
  (`CREATE ... IF NOT EXISTS`), but two instances racing their *first*
  schema scripts at different stages could in principle observe a
  half-created schema from the other process. This race predates this
  change and cannot be closed without file locking or a coordinator;
  within a single process (the application's actual usage: one
  `SQLiteStorage` per application) it is now fully serialized.
- The WAL setup lock is process-wide, so two threads opening *different*
  new database files for the first time serialize briefly on it. Bounded
  and negligible.
- Commit latency on antivirus-scanned filesystems (see runtime note) is
  environmental; per-operation connection open/close is retained from the
  original design deliberately, to keep the change minimal.
