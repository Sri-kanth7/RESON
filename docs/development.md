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

Supabase is the default backend; existing callers observe no change.

```python
from src.orchestrator import create_application

create_application()            # Supabase (default, needs credentials)
create_application("memory")    # local InMemoryStorage, offline, no credentials
create_application("supabase")  # explicit production backend
```

The `RESON_STORAGE_BACKEND` environment variable (`supabase` or `memory`,
default `supabase`) selects the backend when no explicit argument is given.
Unsupported values raise a clear error; nothing falls back silently, and
selecting `memory` never initializes a Supabase client.

Workflows requiring credentials or external services: anything constructed
with the Supabase backend, `tests/test_storage.py`, and
`tests/test_supabase_connection.py`. All other tests run offline.

## Tests

Full suite (requires live Supabase credentials + network):

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Offline suite (no credentials, no network; the supported local default):

```powershell
.\.venv\Scripts\python.exe -m pytest -q --ignore=tests/test_storage.py --ignore=tests/test_supabase_connection.py
```

`tests/test_storage.py` and `tests/test_supabase_connection.py` are live
integration tests. Their failures with `httpx.ConnectError` / `getaddrinfo`
mean Supabase is unreachable, not that the application is broken.

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
- **No bulk incident/evidence persistence.** Telemetry has bulk writes;
  incident/evidence persistence is single-row. Fine at current scale.
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
