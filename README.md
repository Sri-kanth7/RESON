# RESON

**System observability and incident intelligence.**

RESON collects real telemetry from a computer, keeps the history, works out
what changed relative to the machine's own recent behaviour, and explains
what it found with a traceable evidence chain — deterministically, with no
LLM and no paid API in the loop.

The dashboard is honest about where every number came from. Demonstration
data is labeled as demonstration, stale telemetry is never called "live", and
a hosted page never claims to observe the visitor's browser.

---

## What RESON actually does

1. **Collects** CPU, memory, swap, disk, network, process, battery, and
   sensor telemetry from the machine it runs on, with an explicit per-platform
   capability report.
2. **Stores** it with provenance — every record carries the telemetry
   `source` and a stable, non-secret host identifier.
3. **Analyses** it against the machine's own historical baseline, producing
   deviations, change points, behavior fingerprints, and a bounded Behavior
   Shift Score.
4. **Correlates** the evidence into incidents with severity rationale,
   a timeline, a full evidence chain, and deterministic historical matching.
5. **Presents** it in a professional dark-themed dashboard with eight pages,
   an explicit operating mode, and correct empty states.

There is no LLM call anywhere in the core path. Every result is
reproducible from the same inputs.

---

## Supported operating systems

| Platform | Collector | Status |
|---|---|---|
| **Linux** | `src/data/linux_collector.py` | Implemented and **validated** on real hardware |
| **Windows** | `src/data/windows_collector.py` | Implemented; **platform validation pending** (no Windows machine was available) |
| **macOS** | `src/data/macos_collector.py` | Implemented; **platform validation pending** (no macOS machine was available) |

All three share one collector interface, so the collection logic is
identical; only the platform-specific parts differ. See
[`docs/cross_platform.md`](docs/cross_platform.md) for the per-OS support
matrix, permissions, capabilities, and limitations — and for the exact steps
to validate a platform yourself.

---

## Architecture and data flow

```
   platform collector          ingestion           persistence         intelligence        dashboard
  (linux/windows/macos)  ->  TelemetryIngestion  ->  SQLite /      ->  baselines,      ->  app/pages/
                                Service                Supabase /       deviations,          8 pages,
                                                    memory            change points,       source-aware
                                                                        incidents +          queries
                                                                        evidence
```

Every record keeps its `source` and `host_id` from the collector all the way
to the rendered card — including the incidents and evidence links derived
from it. All three storage backends (SQLite, in-memory, Supabase) store,
return and filter on that provenance. Full detail:
[`docs/architecture.md`](docs/architecture.md).

### Time handling

Timestamps are always **stored in UTC** and always **rendered in a named
timezone**. The sidebar has a *Display times in* control (UTC, the host's
own zone, or any IANA zone) and every timestamp states the zone it is
rendered in. A naive timestamp is read as UTC, an aware one is converted
(never relabelled), and daylight saving is applied per instant, so a local
time on screen is always an accurate local time.

### Package layout

```
src/
  common/config.py            Environment configuration (.env), backend selection
  common/timeutil.py          UTC storage -> named display timezone (DST-correct)
  data/
    collector.py              Common Collector interface + BaseCollector lifecycle
    collector_factory.py      Platform resolution (lazy per-platform imports)
    linux_collector.py        Linux telemetry
    windows_collector.py      Windows telemetry
    macos_collector.py        macOS telemetry
    windows_event_log.py      Bounded, allowlisted, deduplicated Windows events
    system_event_log.py       Shared event-collection contract (allowlist, bounds, checkpoint)
    linux_journal.py          Bounded, allowlisted, deduplicated systemd-journal events
    macos_unified_log.py      Bounded, allowlisted, deduplicated unified-log events
    demonstration.py          The deterministic demonstration pipeline (full / quick timelines)
    host_identity.py          Stable, non-secret, hashed host identifier
    agent.py                  Standalone collector agent (CLI entry point)
    monitoring.py             Source-aware queries + honest status derivation
    schemas.py                Data contracts, source values, classify_source()
    ingestion.py              Record routing to storage
    sqlite_storage.py         Persistent local storage (WAL, versioned migrations)
    memory_storage.py         Temporary in-memory storage
    supabase_storage.py       Cloud storage
    dataset.py, dataset_service.py, generator.py, simulator.py
                              Deterministic demonstration data
    storage.py                Storage abstraction
  intelligence/               Baselines, deviations, change points, fingerprints, shift
  incidents/                  Correlation, detection, severity, lifecycle, evidence, matching
  preprocessing/normalizer.py Series grouping and half-open window slicing
  orchestrator.py             Composition root (create_application)
app/
  streamlit_app.py            Entry point: shell, navigation, modes, collector
  theme.py                    Design system (CSS + formatting helpers)
  components/ui.py            Presentation components
  pages/                      Overview, System Resources, Processes, System Events,
                              Incidents, Behavior Analytics, Historical Explorer, Settings
  services/backend.py         UI adapters over the real src/ services
```

---

## Local installation

```bash
python -m pip install -r requirements.txt
```

Python 3.11+ is recommended. On Windows, invoke the venv interpreter
directly (`.\.venv\Scripts\python.exe`) rather than changing the PowerShell
execution policy.

### Run the dashboard

```bash
python -m streamlit run app/streamlit_app.py
```

### Run the collector

Two options:

**In the dashboard** — the sidebar's **Start observation** button starts a
collector in the app process. Convenient for a quick look.

**As a standalone agent** (recommended for continuous monitoring) — a separate
process that does not depend on a browser session being open:

```bash
python -m src.data.agent --backend sqlite --interval 5 --retention-days 30
```

| Option | Meaning |
|---|---|
| `--backend` | `sqlite` (default, persistent local file), `memory`, `supabase` |
| `--interval` | Seconds between cycles (minimum 1.0) |
| `--retention-days` | Delete telemetry older than this; `0` disables pruning |
| `--events` | Also collect operating-system system events (Windows Event Log, systemd journal, macOS unified log) |
| `--platform` | Override the platform (`windows`, `linux`, `macos`) |
| `--host-id` | Override the stable host identifier |
| `--once` | Run one cycle and exit (verification) |
| `--demonstrate` | Run the deterministic demonstration pipeline once and exit |
| `--quick` | With `--demonstrate`: use the short 5-batch timeline instead of the full 15-batch one |
| `--scenarios` | With `--demonstrate`: an explicit comma-separated scenario timeline |

Stop the agent with `Ctrl+C`; it handles `SIGINT`/`SIGTERM` and shuts the
collector down cleanly.

### Run the demonstration

The same deterministic demonstration the dashboard runs is available from the
command line:

```bash
# The full 15-batch timeline
python -m src.data.agent --backend sqlite --demonstrate

# The quick 5-batch timeline (a fraction of the time, still detects incidents)
python -m src.data.agent --backend sqlite --demonstrate --quick

# An explicit scenario timeline
python -m src.data.agent --backend sqlite --demonstrate \
    --scenarios normal,normal,normal,error_spike,normal
```

Everything it writes is tagged `source="simulation"` and is never presented
as live telemetry. In the dashboard, the sidebar offers the same **full** and
**quick** timelines next to the **Run Local Demonstration Analysis** button.

### Verify a collection

```bash
python -m src.data.agent --backend sqlite --once
```

```
RESON collector agent starting: backend=sqlite host=reson-14336f25bdc8c777 source=linux_local interval=5.0s
Collected and ingested 80 metric record(s).
```

---

## Monitoring modes

The dashboard's mode is explicit and shown on every page.

| Mode | Shows | Source |
|---|---|---|
| **Local monitoring** | Real telemetry from a collector on this computer | `windows_local` / `linux_local` / `macos_local` |
| **Demonstration** | Clearly labeled synthetic data | `simulation` |
| **Hosted monitoring** | Telemetry received from a separately running collector | `hosted_collector` |
| **Imported history** | Imported historical records | `imported` |

A mode with no data says so and shows nothing in its place. RESON never
substitutes one mode's data for another's, and running the demonstration
analysis switches the dashboard into demonstration mode so its results are
never mistaken for live telemetry.

---

## Storage configuration

| Backend | Durability | Credentials | Use |
|---|---|---|---|
| `sqlite` | Persistent local file | None | Local single-host monitoring (recommended default) |
| `supabase` | Persistent, shared cloud | `SUPABASE_URL`, `SUPABASE_SECRET_KEY` | Shared / multi-user deployments |
| `memory` | RAM only, per process | None | Offline development and tests |

The local SQLite file lives in an OS-appropriate user data directory
(`%APPDATA%\RESON\reson.db` on Windows,
`~/Library/Application Support/RESON/reson.db` on macOS,
`$XDG_DATA_HOME/reson/reson.db` on Linux). Override it with
`RESON_SQLITE_PATH`.

The dashboard preselects `sqlite` when Supabase credentials are absent, so
it always starts on a backend that works — and the sidebar always states
which backend is in use and whether it is persistent.

---

## Running the tests

```bash
# Full suite
python -m pytest -q

# Offline suite — no credentials, no network
python -m pytest -q --ignore=tests/test_storage.py --ignore=tests/test_supabase_connection.py
```

19 of the tests are **live Supabase integration tests**. They are marked
`requires_supabase` and are **skipped with an explicit reason** when
`SUPABASE_URL` / `SUPABASE_SECRET_KEY` are not configured; their assertions
are unchanged and run in full when the credentials are present.

Everything else runs offline against the SQLite and in-memory backends:

```
639 passed, 19 skipped
```

No assertion was weakened or skipped to make the suite green — the skips are
precondition guards on live integration tests, and they are reported, not
hidden.

---

## Deploying the hosted dashboard

The repository is prepared for Streamlit Community Cloud and similar
platforms: `.streamlit/config.toml`, `requirements.txt`, a `Procfile`, and
`app/streamlit_app.py` as the entry point.

```bash
# Or on a Procfile-based platform
web: streamlit run app/streamlit_app.py --server.headless true --server.address 0.0.0.0 --server.port ${PORT:-8501}
```

Full instructions, including the clean-environment verification procedure:
[`docs/deployment.md`](docs/deployment.md).

### Why a hosted URL needs a separate collector

**A public RESON URL cannot read your computer's CPU, memory, battery,
processes, or event logs.** The code that runs on a hosted Streamlit
instance executes on the hosting server. The browser does not expose those
APIs to a Streamlit app, and no configuration flag changes that.

So a hosted deployment offers two separate experiences:

- **Hosted demonstration** — the deterministic simulator runs inside the
  hosted app and every view is labeled as simulated. Available today.
- **Hosted real monitoring** — a collector agent runs on the machine you
  want to watch and sends telemetry to the hosted instance over an
  authenticated ingestion path. This requires agent authentication,
  per-host authorization, tenant isolation, transport security, server-side
  validation, rate limits, replay protection, and secret rotation. **That
  path is not implemented in this build**, so the hosted mode reports
  "unavailable" rather than showing synthetic data as if it were live.

The requirement list is in [`docs/deployment.md`](docs/deployment.md) §6.

### Durability on a hosted platform

A hosted platform's filesystem is ephemeral. A SQLite file written there does
not survive a restart and is not shared between visitors, so it must never be
presented as durable shared storage. Use a managed database (Supabase) for
anything that must persist.

---

## Privacy

RESON applies data minimization by default. It **does not** collect
passwords, tokens, environment-variable values, process command-line
arguments, browser history, packet payloads, file contents, or user
identities. It **does** collect CPU/memory/swap utilization, disk capacity
and I/O counters, network interface counters, process PID/name/CPU/RSS,
battery status where a battery exists, and hardware sensors where the OS
exposes them.

Remote transmission is off by default and is not implemented. Full detail:
[`docs/security_and_privacy.md`](docs/security_and_privacy.md).

---

## Current limitations and known issues

| Limitation | Status |
|---|---|
| Windows and macOS platform validation | **Pending** — implemented and unit-tested, but never run on real Windows/macOS hardware |
| Remote (hosted) ingestion | **Not implemented** — hosted real monitoring reports "unavailable" |
| Dashboard authentication / tenant isolation | **Not implemented** — a public deployment exposes whatever its backend holds |
| Windows/macOS system-event collection on real hardware | **Platform validation pending** — the journal (Linux) and unified-log (macOS) readers are implemented and tested offline; only the Linux journal reader has been run against the real binary |
| Provenance columns on an existing Supabase project | **Owner action** — a project created before the provenance columns cannot attribute records; the backend detects this, reports it, and refuses to filter by a column it does not have |
| Supabase live integration tests | Require credentials; skipped with an explicit reason when absent |
| Browser automation | Not present — the dashboard is verified with Streamlit's `AppTest` harness |

---

## Documentation

| Document | Contents |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | Data flow, layer boundaries, collector lifecycle, configuration |
| [`docs/deployment.md`](docs/deployment.md) | Local run, hosted deployment, clean-environment verification, remote-ingestion requirements |
| [`docs/cross_platform.md`](docs/cross_platform.md) | Per-OS support matrix, permissions, capabilities, limitations, how to validate |
| [`docs/security_and_privacy.md`](docs/security_and_privacy.md) | What is and is not collected, host identity, secrets, retention, known gaps |
| [`docs/telemetry_sources.md`](docs/telemetry_sources.md) | Source values, full data catalog, time semantics |
| [`docs/troubleshooting.md`](docs/troubleshooting.md) | Symptoms and fixes |
| [`docs/development.md`](docs/development.md) | Day-to-day development workflow |
| [`RESON_MASTER_IMPLEMENTATION_PLAN.md`](RESON_MASTER_IMPLEMENTATION_PLAN.md) | Phase-by-phase status, evidence, blockers, next action |
| [`RESON_DEPLOYMENT_READINESS_REPORT.md`](RESON_DEPLOYMENT_READINESS_REPORT.md) | Deployment readiness assessment |

---

## License

See [`LICENSE`](LICENSE).
