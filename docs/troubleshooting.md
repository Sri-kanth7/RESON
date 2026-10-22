# Troubleshooting

Symptoms, causes, and fixes. Every fix here is safe: none of them requires
deleting user data or editing the database by hand.

---

## 1. Installation and environment

### `ModuleNotFoundError: No module named 'src'`

You are running from the wrong directory, or the package root is not on the
path. RESON is used from the repository root:

```bash
cd /path/to/RESON
python -m pytest -q
python -m streamlit run app/streamlit_app.py
```

### PowerShell blocks `Activate.ps1`

Do **not** change the machine-wide execution policy. Invoke the interpreter
directly:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app/streamlit_app.py
```

### `pip check` reports a conflict

Resolve it before testing. Do not silently relax the version bounds in
`requirements.txt` — the bounds are there to keep the environment
reproducible.

### `ImportError` mentioning a platform collector

Each platform module is imported lazily, so this should not happen. If it
does, the platform module has a genuine import error — check that `psutil`
is installed and importable:

```bash
python -c "import psutil; print(psutil.__version__)"
```

---

## 2. Storage

### `SUPABASE_URL is not configured`

**Expected** when no `.env` is present. The Supabase backend needs both
`SUPABASE_URL` and `SUPABASE_SECRET_KEY`.

Fix: select **sqlite** (persistent local file, no credentials) or **memory**
(temporary) in the sidebar, or create a `.env` with the credentials.

The sidebar preselects `sqlite` when Supabase credentials are absent, so the
app starts on a backend that works.

### `Storage backend could not be constructed`

The backend could not be initialized. The sidebar shows the underlying
message. Check that:

* `RESON_STORAGE_BACKEND` is one of `supabase`, `sqlite`, `memory`;
* `RESON_SQLITE_PATH` points at a writable location when using SQLite;
* the Supabase credentials are correct and the project is reachable.

Nothing falls back silently to another backend.

### `database is locked`

Transient contention between the collector's writer and the UI's reader.
RESON uses WAL mode plus a 30-second busy timeout, and write transactions are
serialized in-process, so this normally resolves itself.

If it persists: stop the collector and retry, and confirm the database
directory is writable and not on a network share that does not support
WAL.

### The database file disappeared

On a hosted platform the filesystem is ephemeral — a SQLite file written
there does not survive a restart and is not shared between visitors. This is
a platform property, not a RESON bug. See `docs/deployment.md` §3.5 for what
to use instead.

### How do I back up the local database?

Stop RESON, then copy `reson.db` together with its `-wal` and `-shm`
sidecars. Or, while RESON is running, use SQLite's online backup:

```bash
sqlite3 "$(python -c 'from src.data.sqlite_storage import default_sqlite_path; print(default_sqlite_path())')" \
  ".backup '/tmp/reson-backup.db'"
```

Never edit the database file by hand.

---

## 3. Collector

### "Start observation" does nothing / the collector stops immediately

Look at the error shown in the sidebar. Common causes:

* `psutil` is not installed in the environment the app is running in.
* The process lacks permission for a specific resource — RESON reports the
  exact error.
* The storage backend rejected the write — check the backend error above.

### The dashboard says "No recent telemetry" but the collector is running

The collector is running but no observation has been stored recently. Check:

* Is the selected monitoring mode the one that matches the collector's
  source? The mode selector defaults to **Local monitoring**, which shows
  the platform's local source.
* Is the collector writing into the backend the dashboard is reading?
  Switching backends stops the collector — this is deliberate, so an
  observation loop never outlives the backend it writes into.
* Has the retention window pruned the data? Check `--retention-days`.

### The status says "Historical data only"

No collector is attached, but stored telemetry exists. The data is real
history, not live. Start the collector, or run the standalone agent.

### The status says "Collector degraded" or "No recent telemetry"

The collector is running but has not produced a recent successful sample.
Either collection is failing (the error is shown next to the status) or the
interval is long relative to the staleness threshold (120 seconds by
default).

### The dashboard shows data from a different computer

This should be impossible: the mode filter is source-based and the host id
is per-collector. If you see it, check the **Monitoring mode** selector —
switching to **Local monitoring** shows the current platform's source, and
**Hosted monitoring** shows `hosted_collector` records.

### Battery shows a percentage on a desktop

It should not: a machine with no battery produces no battery metrics, and
the capability map reports battery support honestly. If you see a battery
percentage, the value came from a real battery.

---

## 4. Collector agent

### `python -m src.data.agent` fails to start

```
interval_seconds must be at least 1.0 seconds
```

The `--interval` floor is 1.0 second. This protects the monitored machine
from being sampled far too aggressively.

### The agent ingests nothing

Run it once with output:

```bash
python -m src.data.agent --backend sqlite --once
```

If it reports `Collected and ingested 0 metric record(s)`, the collector
returned no usable samples. Check `psutil`:

```bash
python -c "
import psutil
print('cpu', psutil.cpu_percent(interval=1))
print('mem', psutil.virtual_memory().percent)
"
```

### No system events are collected

Event collection is opt-in. Enable it with `--events`. It reads the
platform's own event log: the **Windows Event Log**, the **systemd journal**
on Linux, and the **macOS unified log**.

If it still collects nothing:

* **Windows** — without `pywin32`, RESON falls back to `wevtutil`. Check it
  works: `wevtutil qe System /c:5 /f:text`.
* **Linux** — check the journal itself: `journalctl -p err -n 5`. A machine
  without systemd has no journal at all; RESON then reports the collection
  as unavailable, not as "no events".
* **macOS** — check the unified log: `log show --last 1h --predicate
  'process == "kernel"'`. Some subsystems need elevated privileges.
* The channel/unit/process may be unreadable at the current privilege level.
  The error is reported explicitly; RESON never pretends "no events" when it
  could not read the log.
* The allowlist is narrow by design. Widen
  `DEFAULT_ALLOWED_EVENT_IDS` in `src/data/windows_event_log.py`,
  `JOURNAL_MATCHES` in `src/data/linux_journal.py` or
  `DEFAULT_ALLOWED_CHANNELS` in `src/data/macos_unified_log.py` if you need
  more, and review the privacy implications first.

### The agent's database grows too large

Set `--retention-days`. The agent prunes telemetry older than the cutoff
every minute while it runs. Incidents are deliberately retained.

---

## 5. Dashboard

### A page shows an empty state even though data exists

Check the **Monitoring mode**. The pages are strictly source-filtered, so
data stored under a different source is intentionally not shown. The
**Settings** page lists every source present in the backend with its record
counts.

### The demo button seems to do nothing

The demonstration analysis writes records under the `simulation` source and
switches the dashboard into **Demonstration** mode. If you then switch back
to **Local monitoring**, the simulation data is correctly hidden.

The sidebar offers two timelines next to the button: **Full timeline (15
batches)** and **Quick timeline (5 batches)**. The quick run produces the
same kind of result — an incident with its evidence chain — in a fraction of
the time, and the sidebar labels it *"Quick simulated analysis ready"*. The
same pipeline is available from the command line:

```bash
python -m src.data.agent --backend sqlite --demonstrate --quick
```

### Everything is labelled "SIMULATED"

That is the demonstration mode working as intended. Switch the mode back to
**Local monitoring** to view live telemetry.

### A chart is empty

Every chart is driven by stored data for the selected source and window. An
empty chart means no samples exist in that window — it is not a rendering
failure. The System Resources page also reports sampling gaps explicitly
rather than interpolating over them.

---

## 6. Tests

### 19 tests are skipped with "requires SUPABASE_URL and SUPABASE_SECRET_KEY"

**Expected without Supabase credentials.** Those 19 tests are live
integration tests against a real Supabase project
(`tests/test_storage.py`, `tests/test_supabase_connection.py`, plus the
Supabase-default cases in `tests/test_config.py`,
`tests/test_orchestrator.py`, and `tests/test_storage_selection.py`).

They are marked `requires_supabase` and are skipped with an explicit reason
when the credentials are absent. Their assertions are unchanged and run in
full when the credentials are present.

The offline suite covers the same code paths with the SQLite and in-memory
backends:

```bash
python -m pytest -q --ignore=tests/test_storage.py --ignore=tests/test_supabase_connection.py
# 639 passed, 5 skipped
```

To run the integration tests, create a `.env` with:

```
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SECRET_KEY=your-secret-key
```

and re-run `python -m pytest -q`.

### A test fails only on Windows or macOS

Platform-specific behaviour is covered by
`tests/test_platform_collectors.py` with mocked `psutil` calls, so those
tests are platform independent. If a genuinely platform-dependent test
fails, that is a real finding — capture the platform, the Python version, and
the failure output, and record it in the implementation plan.

### The dashboard tests are slow

`tests/test_streamlit_app.py` drives the real Streamlit `AppTest` harness and
starts the app repeatedly. Expect roughly 20–30 seconds.

---

## 7. Deployment

### The hosted app starts but shows "Select a working storage backend"

No backend could be constructed. On a hosted platform this usually means
Supabase credentials are missing. Either add them as platform secrets or
rely on `sqlite` (ephemeral) / `memory` (per session) for a demonstration.

### The hosted app's data disappears after a restart

The hosted filesystem is ephemeral. Use a managed database for anything that
must persist. See `docs/deployment.md` §3.5.

### The sidebar says "this backend cannot store telemetry provenance"

Your Supabase project was created before RESON added the `source`/`host_id`
columns, so records written to it cannot be attributed to a source. RESON
reports this instead of hiding it, and the affected records are shown as
`unknown` — never as live telemetry. Add the columns as described in
`docs/deployment.md` §3.4.

### "Cannot filter 'metrics' by 'source'"

The same cause: the remote table has no `source` column. RESON refuses the
filter rather than ignoring it, because an ignored filter would return
records from every source at once. See `docs/deployment.md` §3.4.

### "Hosted real monitoring" says unavailable

Correct and intended. Remote ingestion is not implemented in this build, and
RESON will not show synthetic data as if it were live telemetry. See
`docs/deployment.md` §6 for what must be built first.

---

## 8. Still stuck

Collect these before asking for help:

```bash
python --version
python -c "import psutil, streamlit, pydantic; print('deps ok')"
python -c "import platform; print(platform.platform())"
python -m pytest -q 2>&1 | tail -5
```

Then include the error text, the operating mode you were in, the selected
storage backend, and whether the collector was running.
