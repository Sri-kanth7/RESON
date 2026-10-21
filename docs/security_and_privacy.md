# Security and Privacy

RESON monitors real computers, so what it collects — and what it refuses to
collect — is the most important thing to get right. This document states
exactly what RESON does today, what it deliberately does not do, and what is
not yet implemented.

---

## 1. Data minimization principle

RESON applies data minimization by default. A category of telemetry is
collected only when it is operationally necessary, is exposed by the
operating system to an unprivileged process, and can be collected without
credentials or private content.

**The rule RESON follows:** if an API makes sensitive data easy to reach,
that is not a reason to collect it.

---

## 2. What RESON collects

| Category | Fields | Notes |
|---|---|---|
| CPU | Utilization, logical/physical core counts, current frequency, load average | No per-thread attribution |
| Memory | Total, used, available, utilization | |
| Swap | Used, total, utilization | A machine with no swap reports a genuine `0` |
| Disk | Per-mount total/used/free/percent, cumulative read and write counters | Capacity only; **no file names, paths, or contents** |
| Network | Per-interface byte, packet, error, and drop counters | **No packet payloads, no DNS, no connection content** |
| Processes | PID, process name, CPU percent, resident set size, status | **No command-line arguments, no environment, no file handles, no user identity** |
| Battery | Present, percent, charging, seconds remaining | Only when a battery is actually present |
| Sensors | Temperature (°C) and fan speed (RPM) where the OS exposes them | Empty when the hardware exposes none |
| System events | Event id, provider, channel, level, and a truncated message | Allowlisted channels and identifiers only; opt-in |

### What is never collected

RESON does not collect, store, or transmit any of the following:

* Passwords, passphrases, or authentication tokens.
* Environment-variable values.
* Process command-line arguments.
* Browser history, bookmarks, or cookies.
* Packet payloads, TCP/UDP streams, DNS queries, or TLS content.
* File names, file contents, or directory listings.
* User names, home directory paths, or email addresses.
* Full raw system logs when structured fields suffice.
* Screenshots, keystrokes, or clipboard contents.
* Public IP addresses as a host identity.

---

## 3. Host identity

A monitored host needs a stable identifier that is not a sensitive attribute.

RESON derives it in `src/data/host_identity.py`:

1. Read the platform machine identifier — `/etc/machine-id` on Linux, the
   `IOPlatformUUID` via `ioreg` on macOS, the `MachineGuid` registry value on
   Windows.
2. Hash it with SHA-256 and truncate the digest.

The result is stable across reboots, is not reversible into the underlying
identifier, and never encodes a username, a home path, or a network address.
`RESON_HOST_ID` overrides it when an operator wants an explicit identity.

The raw machine identifier is never persisted, logged, or displayed.

---

## 4. Storage and secrets

* Secrets live in the environment (or a local `.env` that is git-ignored).
  They are never written into the frontend, into Streamlit session state, or
  into a log line.
* `.gitignore` excludes `.env`, `.streamlit/secrets.toml`, `*.sqlite3`,
  `reson.db`, and its WAL sidecars.
* SQLite queries use parameter binding throughout; no user input is
  interpolated into SQL.
* The dashboard shows the storage backend and whether it is persistent, local,
  cloud, or RAM-only, so a user is never misled about durability.

---

## 5. Remote transmission

**Remote transmission is off by default, and is not implemented in this
build.**

The collector agent writes only to the locally configured storage backend.
It performs no outbound network calls of its own.

When a hosted dashboard needs real telemetry from another machine, the
following must exist *and be tested* first:

1. Authenticated agents with per-agent credentials.
2. Per-host authorization.
3. Tenant isolation — one tenant can never read another's telemetry.
4. Secure transport with no plaintext fallback.
5. Server-side schema and version validation.
6. Request size limits and per-agent rate limits.
7. Replay and deduplication protection.
8. Secret rotation and revocation.
9. Safe error handling that never leaks credentials or other tenants' data.
10. Auditability of administrative actions.

Until those are in place, the hosted mode reports **"unavailable"**. The
current code enforces that: `resolve_mode()` returns `available=False` with
an explicit reason, and
`tests/test_streamlit_app.py::test_hosted_mode_does_not_claim_to_observe_the_visitor`
pins the behaviour.

RESON never silently falls back from a failed real collector to synthetic
telemetry.

---

## 6. Windows event collection

Event collection is opt-in and deliberately narrow.

| Property | Behaviour |
|---|---|
| Channels | `System` and `Application` by default. `Security` is **excluded** — it holds authentication and privilege events. |
| Identifiers | An explicit allowlist of operationally relevant event ids. |
| Window | Bounded (60 minutes by default). |
| Volume | Bounded (200 records per cycle by default). |
| Content | Only the event's own message, truncated to 2000 characters. |
| Deduplication | Stable record ids plus a checkpoint file. |
| Errors | Permission failures are reported explicitly, never shown as "no events". |

The allowlist lives in `src/data/windows_event_log.py` and is configurable by
the operator. Widening it is a deliberate decision with privacy
implications — the documentation for each configuration should state which
categories of event become visible.

---

## 7. Retention and deletion

| Storage | Retention | Deletion |
|---|---|---|
| SQLite (local) | The agent prunes by age (`--retention-days`, default 30) | `purge_before()`; or delete the `.db`, `-wal`, and `-shm` files while RESON is stopped |
| Memory | The process lifetime | It ends with the process |
| Supabase | No automatic pruning in this build | Delete rows in the project, or delete the project |

`purge_before()` removes telemetry older than the cutoff. **Incidents are
deliberately retained** — they are the historical record the user cares
about — and only their evidence rows older than the cutoff are removed.

The agent's retention pruning runs once a minute while the agent is running
and once per cycle in `--once` mode.

---

## 8. Honest reporting requirements

These are product rules, not suggestions, and they are covered by tests:

| Rule | Enforced by |
|---|---|
| Demo data is never shown as live | `source=simulation` tagging in `HistoricalDatasetService`; source-aware reads; the mode banner |
| Unknown provenance stays unknown | `classify_source()` maps `None` to `unknown`; no inference from timestamps or values |
| Stale telemetry is never labelled "live" | `derive_status()` requires a running collector **and** a recent sample for `is_live` |
| A session flag is not proof of a running collector | Status is derived from the collector's own health plus stored data |
| A mode with no data shows nothing in its place | `resolve_mode()` returns `available=False` with a reason |
| Unavailable values are never fabricated | `None` plus explicit capability metadata |

---

## 9. Known gaps

| Gap | Impact | Mitigation |
|---|---|---|
| No remote ingestion | A hosted dashboard cannot yet show real telemetry from another machine | The hosted mode reports "unavailable"; see `docs/deployment.md` §6 |
| No dashboard authentication | A public deployment exposes whatever its backend holds | Do not point a public deployment at private tenant data; use a private deployment or an authenticating proxy |
| No encryption at rest | The local SQLite file is unencrypted | The file lives in the user's own data directory; use full-disk encryption at the OS level |
| `Incident`/`Evidence` provenance on a pre-provenance Supabase project | Records written to a table without the provenance columns cannot be attributed, and are reported as `unknown` rather than as live telemetry | The backend probes the remote schema once per table, reports the limitation in the sidebar and on the Settings page, and refuses to filter by a column it does not have |
| macOS unified log is not read | No macOS system events | Documented as unsupported in `docs/cross_platform.md` |
| No per-tenant access control in the dashboard | Not applicable to a single-user local installation | Required before any multi-user hosted deployment |

---

## 10. Reporting a security issue

Do not open a public issue for a suspected vulnerability. Report it privately
to the repository owner with:

* the affected version or commit,
* the platform and Python version,
* a minimal reproduction,
* the impact you observed.

Do not include real credentials, tokens, or captured telemetry containing
sensitive content in a report — use synthetic data instead.
