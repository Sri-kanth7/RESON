# RESON Local Development

Windows + VS Code + PowerShell. All commands run from the repository root.
All Python commands must use the project virtual environment.

## Setup

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
```

If PowerShell blocks `Activate.ps1`, do not change the machine-wide
execution policy. Invoke `.\.venv\Scripts\python.exe` directly (as above),
or use a process-scoped bypass for the current shell only.

`.venv/` is already ignored by `.gitignore`. Never commit it.
Never commit `.env`. Never print secret values.

## Configuration

`src/common/config.py` loads `SUPABASE_URL` and `SUPABASE_SECRET_KEY`
(loaded from `.env`) only when the Supabase backend is selected. Everything
else — behavior analysis, incident intelligence, `InMemoryStorage`, the
simulator, and the offline test suite — needs no configuration and no
network.

## Storage selection

Three backends are supported through `RESON_STORAGE_BACKEND` (or the
`storage_backend` argument of `create_application`):

- **`supabase`** — persistent cloud storage. The production default; requires
  `SUPABASE_URL` and `SUPABASE_SECRET_KEY` (see "Configure Supabase securely"
  below).
- **`sqlite`** — persistent local storage on the user's machine. No
  credentials, no network, no Supabase configuration required.
- **`memory`** — temporary, non-persistent in-memory storage. Tests and
  offline development only; data is lost when the process ends.

Supabase remains the default (`RESON_STORAGE_BACKEND=supabase`), so existing
installations observe no change.

```python
from src.orchestrator import create_application

create_application()            # Supabase (default, needs credentials)
create_application("sqlite")    # persistent local storage, no credentials
create_application("memory")    # local InMemoryStorage, offline, no credentials
create_application("supabase")  # explicit production backend
```

Unsupported values raise a clear error; nothing falls back silently, and
selecting `sqlite` or `memory` never initializes a Supabase client.

Workflows requiring credentials or external services: anything constructed
with the Supabase backend, `tests/test_storage.py`, and
`tests/test_supabase_connection.py`. All other tests run offline.

## Local SQLite Storage

RESON can persist everything locally in a SQLite file with no cloud
credentials or network access.

### Selecting SQLite

```powershell
# Via environment variable (used when no explicit backend argument is given)
$env:RESON_STORAGE_BACKEND="sqlite"
python -m streamlit run app/streamlit_app.py

# Explicitly, in code
from src.orchestrator import create_application
app = create_application("sqlite")
```

In the Streamlit app, choose **sqlite** in the sidebar "Storage backend"
selectbox. The selectbox offers **Supabase** (persistent cloud storage) and
**Local SQLite** (persistent local storage) as the two durable choices;
**memory** is shown as temporary, non-persistent storage.

### Where local data is stored

By default the database lives at an OS-appropriate user data location:

- Windows: `%APPDATA%\RESON\reson.db`
- macOS: `~/Library/Application Support/RESON/reson.db`
- Linux/other Unix: `$XDG_DATA_HOME/reson/reson.db`
  (falling back to `~/.local/share/reson/reson.db`)

Override the location with the `RESON_SQLITE_PATH` environment variable:

```powershell
$env:RESON_SQLITE_PATH="C:\RESON\reson.db"
# or
$env:RESON_SQLITE_PATH="~/.local/share/reson/reson.db"
```

The path is also exposed in the Streamlit sidebar health status under
"Mode: sqlite · persistent local file · database_path".

### Persistence across restarts

Records are committed to the SQLite file on every write, so data survives
closing and reopening the application. Reopen the same database path (or rely
on the default) and all metrics, logs, events, deployments, incidents,
evidence, and services are still there. Each backend keeps its own history:
switching to Supabase does not read the SQLite file, and switching back to
SQLite does not read Supabase. Nothing is migrated, copied, merged, or deleted
when you switch backends.

### Backing up the database safely

Back up the `reson.db` file itself (plus its `-wal` and `-shm` sidecars when
present, since WAL mode is used). The safest approaches:

- Copy the database while RESON is stopped, or
- Use the SQLite online backup API (`sqlite3.Connection.iterdump` or
  `VACUUM INTO`) while RESON is running, which produces a consistent snapshot
  without blocking writers for long.

Never edit the database file by hand, and never commit `reson.db` or its
sidecars to version control (`.gitignore` already excludes them).

### What happens when you switch storage backends

Stop any running real-time collector before switching: the sidebar stops the
collector automatically when the backend changes, because an active
observation loop must not outlive the backend it writes into. After switching,
the new backend starts from its own stored history; the previous backend's
data remains intact in its own store. Switching is explicit, never silent, and
never falls back to another backend if the chosen one cannot be constructed.

### Configuring Supabase securely

Supabase remains the default production backend and needs
`SUPABASE_URL` and `SUPABASE_SECRET_KEY`. Store them in `.env` (never commit
it — `.gitignore` excludes `.env`) and only select `supabase` when both are
present:

```
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SECRET_KEY=your-secret-key
```

Without both, constructing the Supabase backend raises a clear
`RuntimeError` naming the missing variable; nothing falls back silently. Use
`sqlite` or `memory` for fully offline operation.

### Troubleshooting storage failures

- `SUPABASE_URL is not configured` / `SUPABASE_SECRET_KEY is not configured`:
  expected without `.env`; switch to `sqlite` (or `memory`) in the sidebar,
  or set the credentials in `.env`.
- Storage backend could not be constructed: the sidebar shows the backend
  error panel with the underlying message. Verify `RESON_STORAGE_BACKEND` is
  one of `supabase`, `sqlite`, `memory`, and that `RESON_SQLITE_PATH` points
  at a writable location when using SQLite.
- Database write/query failures are surfaced as errors, not silent fallbacks.
  The sidebar reports the selected backend, the database path (SQLite), and
  whether reads are operational. Inspect `storage_health` output in the
  sidebar for the concrete error.
- `database is locked`: transient contention between the collector's writer
  thread and UI reads retries internally for up to 30 seconds (WAL mode +
  busy timeout). If it persists, stop the collector and retry.

## Tests

Full suite:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Offline suite (no credentials, no network):

```powershell
.\.venv\Scripts\python.exe -m pytest -q --ignore=tests/test_storage.py --ignore=tests/test_supabase_connection.py
```

19 tests are live Supabase integration tests. They carry the
`requires_supabase` marker and are **skipped with an explicit reason** when
`SUPABASE_URL` / `SUPABASE_SECRET_KEY` are absent; their assertions are
unchanged and run in full when the credentials are present. Everything else
runs offline against the SQLite and in-memory backends.

`InMemoryStorage` (`src/data/memory_storage.py`) implements the same
`Storage` abstraction as `SupabaseStorage` with identical filter semantics
(inclusive time bounds, plain-string equality, timestamp-ordered results),
so offline development and tests exercise the real contract.

## Streamlit application

Run the dashboard (from the repository root):

```powershell
.\.venv\Scripts\python.exe -m streamlit run app/streamlit_app.py
```

The sidebar selects the storage backend. **memory** runs fully offline with
no credentials or network; **supabase** is the production default and needs
`SUPABASE_URL` and `SUPABASE_SECRET_KEY`. The application never falls back
silently between backends. With an empty backend, use **Run Local
Demonstration Analysis** to run the deterministic simulator through the real
ingestion and intelligence pipeline; simulated telemetry is labeled.

The UI is a presentation layer only: `app/services/backend.py` adapts the
existing `src/` services, and the pages render their real outputs. No
intelligence algorithm is reimplemented in Streamlit.

`app/streamlit_app.py` is exercised offline by `tests/test_streamlit_app.py`
(Streamlit `AppTest`) and `app/services/backend.py` by
`tests/test_ui_services.py`.

## Troubleshooting

- `SUPABASE_URL is not configured` → expected without `.env`; use
  `create_application("memory")` and the offline suite.
- `pip check` reports a conflict → resolve it before testing; do not
  silently upgrade version constraints in `requirements.txt`.
- Supabase `DeprecationWarning` about `timeout`/`verify` → upstream client
  notice, harmless for local runs.

## Known limitations (unresolved, by design)

- **Storage vs analysis time bounds.** Storage queries (`SupabaseStorage`,
  `InMemoryStorage`) filter timestamps inclusively on both ends, while
  analysis slicing (`slice_series`, detector windowing) uses half-open
  `[start, end)`. Both semantics are pinned by tests and intentionally
  unchanged: no production path currently feeds storage query results
  directly into analysis windows (the only storage reads are
  `get_incidents`/`get_evidence` in `historical_candidates`, without time
  bounds), so the mismatch is latent. A future caller bridging the two must
  convert bounds explicitly. Changing either side needs a product decision.
- **Incidents are still written one row at a time.** Telemetry *and*
  evidence have bulk writes (`save_metrics`, `save_logs`, `save_events`,
  `save_deployments`, `save_evidence_batch`), but `save_incident` has no bulk
  counterpart. One analysis produces a handful of incidents, so this is not a
  bottleneck; a workload that produces thousands of incidents per run would
  need one.
- **Session-only analytical detail.** Severity explanations, per-signal
  detail, per-pair correlation factors, and fingerprint/shift contributions
  are produced during an analysis run and are not persisted by design.
  Stored incidents reload with their incident record and evidence chain
  (IDs, timestamps, strengths, explanations); re-run the demonstration
  analysis to regenerate the session-only detail. The UI states this
  explicitly rather than inventing values.
- **No browser automation in the repo.** Streamlit behaviour is verified with
  the built-in `AppTest` harness offline; visual review against the Stitch
  exports is a manual step.
