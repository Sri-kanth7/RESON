# Deploying RESON

This guide covers running the dashboard locally, preparing it for a public
hosted deployment, and — importantly — what a public URL can and cannot do.

---

## 1. The one thing to understand first

**A public Streamlit URL cannot collect telemetry from a visitor's computer.**

When someone opens a hosted RESON page, the code that runs is on the *hosting
server*, not on the visitor's machine. The browser does not expose CPU,
memory, battery, process, or event-log access to a Streamlit app. There is no
configuration, flag, or header that changes this.

So a hosted RESON deployment has exactly two honest possibilities:

| Experience | What the visitor sees | How it is produced |
|---|---|---|
| **Hosted demonstration** | Clearly labeled synthetic data | The deterministic simulator, run inside the hosted app |
| **Hosted real monitoring** | Real telemetry from an *authorized* machine | A separate collector agent on that machine, sending data over the authenticated ingestion path |

RESON implements the first today. The second requires the authenticated remote
ingestion path described in §6; until that path is configured and verified,
the hosted mode reports **"unavailable"** rather than showing synthetic data
as if it were live. This behaviour is enforced in
`src/data/monitoring.py::resolve_mode` and covered by
`tests/test_streamlit_app.py::test_hosted_mode_does_not_claim_to_observe_the_visitor`.

---

## 2. Run locally

### 2.1 Install

```bash
python -m pip install -r requirements.txt
```

Python 3.11+ is recommended. See `docs/cross_platform.md` for the
per-platform details.

### 2.2 Launch the dashboard

```bash
python -m streamlit run app/streamlit_app.py
```

Open the printed URL (normally <http://localhost:8501>).

The sidebar offers:

* **Storage backend** — `sqlite` (persistent, local file, no credentials),
  `memory` (temporary, RAM only), `supabase` (persistent cloud, needs
  credentials). Supabase is preselected when its credentials are present;
  otherwise `sqlite` is preselected so the app starts on a working backend.
* **Monitoring mode** — local, demonstration, hosted, imported.
* **Start observation** — starts a collector in this process and ingests its
  output into the selected backend.

### 2.3 Run the standalone collector agent

For continuous collection, run the agent as its own process:

```bash
python -m src.data.agent --backend sqlite --interval 5 --retention-days 30
```

| Option | Meaning |
|---|---|
| `--backend` | `sqlite` (default), `memory`, `supabase` |
| `--interval` | Seconds between collection cycles (minimum 1.0) |
| `--retention-days` | Delete telemetry older than this. `0` disables pruning |
| `--events` | Also collect Windows system events (Windows only) |
| `--platform` | Override the platform (`windows`, `linux`, `macos`) |
| `--host-id` | Override the stable host identifier |
| `--once` | Run a single cycle and exit (useful for verification) |

Stop it with `Ctrl+C`; it handles `SIGINT` and `SIGTERM` and shuts the
collector down cleanly.

Verify it works:

```bash
python -m src.data.agent --backend sqlite --once
```

Expected output:

```
RESON collector agent starting: backend=sqlite host=reson-... source=linux_local interval=5.0s
Collected and ingested 80 metric record(s).
```

---

## 3. Prepare for a public hosted deployment

The repository is already structured for Streamlit Community Cloud and
similar platforms.

### 3.1 What is already in the repository

| File | Purpose |
|---|---|
| `.streamlit/config.toml` | Headless mode, usage statistics off, dark theme, no secrets |
| `requirements.txt` | Runtime dependencies, version-bounded |
| `Procfile` | `streamlit run app/streamlit_app.py` for Procfile-based platforms |
| `app/streamlit_app.py` | The deployment entry point |

There are no absolute local paths, no Windows-only imports at module scope,
and no dependency on a local database file for the app to start.

### 3.2 Streamlit Community Cloud

1. Push the repository to GitHub (the owner performs this — RESON does not
   commit or push).
2. In Streamlit Community Cloud, choose **New app** and point it at the
   repository.
3. Set **Main file path** to `app/streamlit_app.py`.
4. Leave **Python version** on the default, or select 3.11+.
5. Deploy. The first build installs `requirements.txt` and starts the app.

### 3.3 Secrets

Secrets belong in the platform's secret store, never in the repository.

| Secret | Required for | Notes |
|---|---|---|
| `SUPABASE_URL` | Supabase backend | Only needed if the deployment uses Supabase |
| `SUPABASE_SECRET_KEY` | Supabase backend | Only needed if the deployment uses Supabase |
| `RESON_HOST_ID` | Optional | Pins the host identity of a remote collector |
| `RESON_STORAGE_BACKEND` | Optional | Overrides the preselected backend |

Do **not** put secrets in `.streamlit/config.toml`, in `app/`, or in any
module that is imported by the frontend. RESON never reads collector secrets
into Streamlit session state.

### 3.4 Supabase tables and telemetry provenance

Every RESON record — metrics, logs, events, deployments, incidents and
evidence — carries a `source` and a `host_id`. The **SQLite** backend
creates and migrates those columns automatically. A **Supabase** project is
created outside RESON, so its tables must have them:

```sql
ALTER TABLE metrics    ADD COLUMN source TEXT;
ALTER TABLE metrics    ADD COLUMN host_id TEXT;
ALTER TABLE logs       ADD COLUMN source TEXT;
ALTER TABLE logs       ADD COLUMN host_id TEXT;
ALTER TABLE events     ADD COLUMN source TEXT;
ALTER TABLE events     ADD COLUMN host_id TEXT;
ALTER TABLE deployments ADD COLUMN source TEXT;
ALTER TABLE deployments ADD COLUMN host_id TEXT;
ALTER TABLE incidents  ADD COLUMN source TEXT;
ALTER TABLE incidents  ADD COLUMN host_id TEXT;
ALTER TABLE evidence   ADD COLUMN source TEXT;
ALTER TABLE evidence   ADD COLUMN host_id TEXT;
```

Recommended indexes:

```sql
CREATE INDEX idx_metrics_source_host ON metrics (source, host_id);
CREATE INDEX idx_metrics_source_timestamp ON metrics (source, timestamp);
CREATE INDEX idx_incidents_source_start ON incidents (source, start_time);
CREATE INDEX idx_evidence_source_timestamp ON evidence (source, timestamp);
```

If the columns are missing, the backend **detects it** (it probes each table
once), reports it in the sidebar and on the Settings page, and refuses to
apply a source filter to a table that has no such column — a silently
ignored filter would mix live and synthetic data. Records written to such a
project are reported as `unknown`, never as live telemetry.

### 3.5 Storage on a hosted platform — read this before you trust the data

A hosted platform's filesystem is **not durable shared storage**:

* Streamlit Community Cloud's filesystem is ephemeral. A SQLite file written
  there disappears when the app restarts and is **not** shared between
  visitors.
* Therefore, a local SQLite file on a hosted platform must never be presented
  as durable, shared, production storage.

Practical consequences:

| Need | Use |
|---|---|
| Public demonstration | `memory` backend + the demonstration analysis. Data is per-session and clearly labeled. |
| Durable single-user local monitoring | `sqlite` on the user's own machine (not hosted) |
| Shared, multi-user, durable monitoring | A managed database (Supabase or equivalent) with the authenticated ingestion path of §6 |

RESON does not silently substitute one of these for another: the sidebar
always states the backend and whether it is persistent, local, cloud, or
RAM-only.

---

## 4. Clean-environment verification

Run this on a machine that has never seen the project, to confirm the
repository is self-contained.

```bash
git clone <your-fork> reson-verify
cd reson-verify
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip check

# 1. The offline test suite must pass.
python -m pytest -q --ignore=tests/test_storage.py --ignore=tests/test_supabase_connection.py

# 2. The app must start and serve a healthy endpoint.
python -m streamlit run app/streamlit_app.py &
sleep 15
curl -fsS http://localhost:8501/_stcore/health && echo " <- healthy"

# 3. The collector agent must collect real telemetry.
python -m src.data.agent --backend sqlite --once

# 4. The dashboard must render every page in every mode.
python -m pytest -q tests/test_streamlit_app.py
```

Expected results:

* The offline suite passes.
* `/_stcore/health` returns `ok`.
* The agent reports a non-zero number of ingested records.
* The dashboard tests pass.

---

## 5. Operational notes for a hosted deployment

| Concern | How RESON handles it |
|---|---|
| Filesystem persistence | Never presented as durable on a hosted platform; the sidebar states the backend type. |
| App sleep / cold start | The dashboard starts with an empty view and honest empty states. A remote collector keeps collecting independently of the dashboard. |
| Database persistence | Use Supabase (or another managed store) for anything that must outlive the process. |
| Concurrent sessions | The `memory` backend is per-session. A shared backend must be configured for concurrent access. |
| Resource limits | The collector runs in the *user's* process, not the hosted one, so a hosted instance never samples the host at a high rate. |
| Network restrictions | The hosted app makes no outbound network calls except to the configured storage backend. |
| Authentication / authorization | Not implemented for the dashboard. See §6 and `docs/security_and_privacy.md`. A public dashboard exposes whatever its backend holds — do not point it at a private tenant's data without the tenant isolation of §6. |
| Retention and deletion | The agent prunes by age. Deleting a Supabase project's data removes it. |
| Public vs private access | The dashboard itself has no access control. Use a private deployment (or a reverse proxy with authentication) for non-public data. |

---

## 6. Remote (hosted) ingestion — required for hosted real monitoring

**Remote ingestion is not enabled in this build.** This section documents what
must be implemented and verified before it can be enabled. Enabling it early
would mean exposing an endpoint that accepts arbitrary telemetry, which is
exactly the failure mode to avoid.

Required before enabling:

1. **Authenticated agents** — each collector proves its identity with a
   per-agent credential.
2. **Per-host authorization** — the server checks that the agent is allowed
   to report for the host it claims.
3. **Tenant isolation** — one tenant can never read another tenant's
   telemetry.
4. **Secure transport** — TLS, with no plaintext fallback.
5. **Server-side validation** — every payload is validated against a schema
   and a version before it is stored.
6. **Request size and rate limits** — a bounded payload size and a per-agent
   rate limit.
7. **Replay and deduplication protection** — a per-agent monotonic sequence
   or an equivalent replay guard.
8. **Schema and version validation** — incompatible versions are rejected
   with a clear error, not silently coerced.
9. **Secret rotation and revocation** — a compromised agent credential can be
   revoked without redeploying.
10. **Safe error handling** — errors never echo credentials, internal paths,
    or other tenants' identifiers.
11. **Auditability** — administrative actions (enrolling an agent, rotating
    a credential, revoking access) are recorded.

Until all of these are implemented *and tested*, the hosted mode must
continue to report "unavailable" — which is what the current code does.

---

## 7. Troubleshooting

See `docs/troubleshooting.md`.
