# RESON — Persistent Local SQLite Storage: Implementation Report

**Branch:** `main` (commit `197d3f917477937288553d781add54c4a81c8a41`, verified)
**Status:** Implementation complete, working tree modified, nothing committed or pushed.

---

## Objective recap

Add a production-quality persistent SQLite storage backend alongside the existing
Supabase backend, so users can explicitly choose:

- **Local SQLite** — persistent data on their own machine, no credentials.
- **Supabase** — persistent cloud storage (existing default, unchanged).
- **memory** — retained but labeled temporary/non-persistent (tests and offline
  development only).

No silent fallback between backends; no automatic migration when switching.

---

## Files created

| File | Purpose |
|------|---------|
| `src/data/sqlite_storage.py` | New `SQLiteStorage(Storage)` backend — full storage contract on a persistent SQLite file |
| `tests/test_sqlite_storage.py` | 39 deterministic tests using temporary database files only |

## Files modified

| File | Change |
|------|--------|
| `src/common/config.py` | Added `sqlite` to supported backends and `RESONConfig.storage_backend` Literal; added `sqlite_db_path` field loaded from `RESON_SQLITE_PATH`; Supabase remains the default |
| `src/orchestrator.py` | `create_application("sqlite")` builds `SQLiteStorage(config.sqlite_db_path)` |
| `app/services/backend.py` | `storage_health()` reports backend name and `database_path` for SQLite |
| `app/streamlit_app.py` | Sidebar backend selectbox offers Supabase / sqlite / memory; sqlite labeled "persistent local file" with DB path and operational status; memory labeled "memory only (RAM)"; collector stopped before backend rebuild |
| `.gitignore` | Ignores `*.sqlite3*`, `*.db-*`, `reson.db`, `reson.db-*`, `.reson/` |
| `README.md` | Package layout + Streamlit app sections document SQLite |
| `docs/development.md` | New "Local SQLite Storage" section: selection, default path by OS, `RESON_SQLITE_PATH`, persistence across restarts, safe backup, switching behavior, Supabase security, troubleshooting |

Pre-existing untracked report files (`RESON_AUDIT_REVIEW_REPORT.md`, `RESON_FINAL_IMPLEMENTATION_REPORT.md`, `RESON_IMPLEMENTATION_REPORT.md`) were left untouched.

---

## SQLite schema and storage operations implemented

`SQLiteStorage` implements the complete `Storage` abstraction
(`src/data/storage.py`), following existing method signatures and semantics
(inclusive time bounds, `ORDER BY timestamp ASC, id ASC`, enum/plain-string
filter normalization consistent with `SupabaseStorage`/`InMemoryStorage`):

- **services**: `save_service` (upsert by id), `get_service`
- **metrics**: `save_metric`, `save_metrics`, `get_metrics` (service / name /
  start/end filters)
- **logs**: `save_log`, `save_logs`, `get_logs` (service / level / time)
- **events**: `save_event`, `save_events`, `get_events` (service / type / time)
- **deployments**: `save_deployment`, `save_deployments`, `get_deployments`
  (service / status / time)
- **incidents**: `save_incident`, `get_incidents` (severity / status / scenario /
  time)
- **evidence**: `save_evidence`, `get_evidence` (source/target ids/types /
  relationship / time)

Schema:

- Tables: `services`, `metrics`, `logs`, `events`, `deployments`, `incidents`,
  `evidence`, `schema_migrations`
- `SCHEMA_VERSION = 1` tracked in `schema_migrations`; idempotent initialization
  via `CREATE TABLE IF NOT EXISTS` + `INSERT OR IGNORE` migration record
- Non-destructive migrations ready to extend (increment `SCHEMA_VERSION`)
- Indexes: service, timestamp, composite `(service, timestamp)` on telemetry
  tables; `(source, host_id)` on metrics; incident severity/status/scenario;
  evidence source/target/timestamp

Quality properties:

- Parameterized queries only
- Timestamps stored as ISO-8601 UTC, parsed back to aware UTC datetimes
- Each operation opens a connection, commits on success, rolls back on failure,
  closes in `finally` (no connection leakage)
- WAL journal mode (`-wal`/`-shm` sidecars) + 30s `busy_timeout` so the
  real-time collector's writer thread and UI readers do not fail on transient
  lock contention
- `health()` returns `operational`, `error`, `backend`, `database_path`, `counts`
- Clear, actionable `sqlite3` errors surface; no silent catching

## Telemetry identity preserved

- `Metric.source` and `Metric.host_id` columns are persisted and round-tripped
  (`windows_local` source tags and host identity survive write/read)
- `host_id` is stored separately from `service` — **no change to the collector's
  convention** was required (smallest justified change: none)
- Baseline computation and historical comparison keep filtering by
  `service == host_id` plus `source`, so simulated and real telemetry are never
  mixed
- Verified by `test_metric_persists_source_and_host_id`,
  `test_metric_save_and_get_round_trip`, and
  `test_collector_ingestion_into_sqlite_round_trip`

## Backend selection behavior

- `SUPPORTED_STORAGE_BACKENDS = ("supabase", "sqlite", "memory")`; default
  remains `supabase` so existing installations observe no change
- Selected via `RESON_STORAGE_BACKEND` env var or `create_application("sqlite")`
  / `"supabase"` / `"memory"` argument
- Invalid values raise `RuntimeError`/`ValueError` before anything initializes;
  **nothing falls back silently**
- SQLite constructs and runs with **no Supabase credentials**; `config.supabase
  is None` in local mode
- Supabase without credentials fails loudly (`RuntimeError: SUPABASE_URL ...`)
  with no fallback
- Switching backends stops the running collector first (`_stop_collector`),
  rebuilds the app, and never migrates/copies/merges/deletes records — each
  backend keeps its own history (verified by
  `test_switching_backends_does_not_migrate_history`)

## Storage health reporting

- Sidebar shows: Mode (backend), persistence label ("persistent local file" /
  "persistent cloud" / "memory only (RAM)"), database path for SQLite, and
  `operational: yes/no`
- Failed reads surface `Storage read failed: <error>` in the sidebar
- Backend error panel shown when construction fails; write/query failures are
  surfaced accurately, not treated as proof of success

## Persistence across restarts

- Writes commit to the SQLite file; reopening against the same path returns all
  records (verified by `test_persistence_across_reopen` and `test_counts_across_reopen`)
- End-to-end script: `create_application("sqlite")` twice against the same
  default file, metric retained with `source="windows_local"`,
  `host_id="host-42"`; health reported operational with DB path

## Concurrent access

- WAL mode + busy timeout; `test_concurrent_writes_from_multiple_threads`
  writes from 8 threads and verifies no lost records

## Testing results

```
python -m pytest tests/ -q  →  376 passed, 19 failed
tests/test_sqlite_storage.py          39 passed
tests/test_storage_selection.py       local-mode tests passed
tests/test_ui_services.py             passed
tests/test_streamlit_app.py           passed
tests/test_historical_comparison.py   passed
tests/test_windows_collector.py       passed
```

The 19 failures are **pre-existing Supabase credential-unavailable** failures
(no `.env` in this environment): `test_config.py`×2, `test_orchestrator.py`,
`test_storage.py`×9, `test_storage_selection.py` default/supabase ×2,
`test_supabase_connection.py`. They fail with `RuntimeError: SUPABASE_URL is
not configured` — live integration tests requiring credentials + network,
distinct from offline unit results. Documented in `docs/development.md`.

- `python -m pip check` → "No broken requirements found."
- `git diff --check` → clean

Test coverage includes: DB initialization, every storage operation, metric
round trips with `source`/`host_id`, logs/events/deployments/incidents/evidence,
time-range inclusive boundaries, historical comparison using stored SQLite data,
persistence across reopen, schema migration preserving records, empty DB and
malformed input, SQLite errors/rollback, backend selection and health, local
mode without Supabase credentials, Supabase without credentials failing clearly,
switching backends without history migration, collector ingestion into SQLite.

## Supabase integration status

Unchanged. `supabase` remains the default production backend; `SupabaseStorage`
and `get_supabase_client` were not modified. Backend selection now routes
explicitly — selecting `sqlite` never touches Supabase. No Supabase network was
used in this test run.

## Windows telemetry limitations

Developed and verified on **Linux Codespaces only**. No Windows validation is
claimed, per safety rules. The SQLite default path covers Windows
(`%APPDATA%\RESON\reson.db`), and the collector module is cross-platform by
design (`psutil`), but actual Windows behavior was not exercised.

## Working tree (clean of DB/credential artifacts)

Modified: `.gitignore`, `README.md`, `app/services/backend.py`,
`app/streamlit_app.py`, `docs/development.md`, `src/common/config.py`,
`src/orchestrator.py`

New: `src/data/sqlite_storage.py`, `tests/test_sqlite_storage.py`

Pre-existing untracked reports left untouched:
`RESON_AUDIT_REVIEW_REPORT.md`, `RESON_FINAL_IMPLEMENTATION_REPORT.md`,
`RESON_IMPLEMENTATION_REPORT.md`

No `.env`, no real database, no real telemetry records committed.

---

**Definition of done:** All criteria met — Local SQLite and Supabase are both
selectable; SQLite persists every required data type across restarts; local
operation requires no Supabase configuration; source/host metadata survive
persistence; intelligence services query the selected backend correctly; storage
failures are explicit with no silent backend switching; tests verify actual
database persistence; documentation describes the implementation; working tree
contains no secrets or database files.

**Nothing was committed or pushed, pending explicit approval.**