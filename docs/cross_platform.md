# Cross-Platform Operation

RESON targets Windows, Linux, and macOS. This document states, per operating
system, what is implemented, what has actually been validated, and what the
known limitations are.

**Validation status matters.** RESON does not claim support for an operating
system it has not been run on. Where a platform has not been validated, this
document says so explicitly.

---

## 1. Support matrix

| Platform | Collector | Implemented | Platform-validated | Validator |
|---|---|---|---|---|
| Linux | `src/data/linux_collector.py` | Yes | **Yes** | This development environment (Linux, Python 3.14.2) |
| Windows | `src/data/windows_collector.py` | Yes | **No** | No Windows machine was available |
| macOS | `src/data/macos_collector.py` | Yes | **No** | No macOS machine was available |

All three collectors implement the same `Collector` interface
(`src/data/collector.py`) and are selected by
`src/data/collector_factory.py`, which imports each platform module lazily.
A platform-specific import failure therefore cannot break another operating
system.

### What the shared test suite proves for all three platforms

`tests/test_platform_collectors.py` exercises the Linux and macOS
collection logic with mocked `psutil` calls, and
`tests/test_collector.py` covers the shared lifecycle for all platforms:

* the collector's source is correct (`windows_local` / `linux_local` /
  `macos_local`),
* the capability map is explicit,
* unavailable values are reported as unavailable rather than fabricated,
* the start/stop lifecycle, duplicate prevention, and health reporting behave
  identically everywhere.

These are *implementation* tests, not *platform* validation.

---

## 2. Linux

### Supported Python versions

Python 3.11, 3.12, and 3.13 are recommended. 3.14 was used for the
validation runs in this environment.

### Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### Permissions

**None required** for the supported measurements. RESON reads only
unprivileged, world-readable sources:

| Data | Source | Privileges |
|---|---|---|
| CPU, memory, swap | `/proc/stat`, `/proc/meminfo` | none |
| Load average | `/proc/loadavg` | none |
| Disk capacity | `statvfs` on each mount | none |
| Disk I/O counters | `/proc/diskstats` | none |
| Network counters | `/proc/net/dev` | none |
| Processes | `/proc/<pid>` | none (other users' processes may be partially hidden) |
| Battery | `/sys/class/power_supply` | none |
| Temperatures, fans | `/sys/class/hwmon` | none |

Individual processes owned by other users may return `AccessDenied`. RESON
skips those processes and continues; it never fails the whole cycle.

### Starting and stopping the collector

```bash
# Continuous collection, own process
python -m src.data.agent --backend sqlite --interval 5

# Single cycle (verification)
python -m src.data.agent --backend sqlite --once
```

Stop with `Ctrl+C` (the agent handles `SIGINT` and `SIGTERM`).

### Platform capabilities

| Capability | Supported | Notes |
|---|---|---|
| CPU | Yes | Utilization, logical/physical core counts, current frequency, 1-minute load average |
| Memory | Yes | Total, used, available, utilization |
| Swap | Yes | Used, total, utilization (a machine with no swap correctly reports `0`) |
| Disk | Yes | Per-mount capacity plus I/O counters; pseudo filesystems and file bind-mounts are filtered out |
| Network | Yes | Per-interface counters, packets, errors, and drops |
| Processes | Yes | PID, name, CPU, RSS, status |
| Battery | Yes | Present/absent is reported honestly; a desktop reports "not applicable" |
| Sensors | Yes | hwmon temperatures (°C) and fan speeds (RPM); empty when the hardware exposes none |
| System events | Not implemented | Linux uses journald/syslog; the Windows event-log collector is Windows-only |

### Known limitations

* **Filesystem capacity is per mount point.** Bind mounts of individual files
  (common in containers) are excluded because their capacity is meaningless.
* **Disk and network counters are cumulative.** RESON stores them as counters
  and derives rates from successive samples. A counter that decreases (a
  device reset) is skipped rather than reported as a negative rate.
* **Sensors depend on the kernel and hardware.** Many virtual machines and
  containers expose no `hwmon` sensors at all; RESON reports an empty set
  rather than a placeholder.
* **Swap of `0` is a genuine zero**, not "unavailable": a machine with no
  swap configured really does have zero swap.

### Validation evidence

```
$ python -m src.data.agent --backend sqlite --once
RESON collector agent starting: backend=sqlite host=reson-14336f25bdc8c777 \
    source=linux_local interval=5.0s
Collected and ingested 80 metric record(s).

metrics rows: 80
  ('cpu_percent', 82.6, 'percent', 'linux_local', 'reson-14336f25bdc8c777')
  ('memory_percent', 53.6, 'percent', 'linux_local', 'reson-14336f25bdc8c777')
  ('memory_used_bytes', 4467703808.0, 'bytes', 'linux_local', 'reson-14336f25bdc8c777')
  ('memory_total_bytes', 8328073216.0, 'bytes', 'linux_local', 'reson-14336f25bdc8c777')
  ('process_cpu_percent', 0.0, 'percent', 'linux_local', 'reson-14336f25bdc8c777')
  ('process_memory_rss_bytes', 585728.0, 'bytes', 'linux_local', 'reson-14336f25bdc8c777')
distinct sources: [('linux_local',)]
```

---

## 3. Windows

### Supported Python versions

Python 3.11, 3.12, and 3.13 are recommended.

### Installation

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Invoke the venv interpreter directly if PowerShell blocks `Activate.ps1`.
Do not change the machine-wide execution policy.

### Permissions

**None required** for CPU, memory, disk, network, and process collection.

System event collection is **opt-in and may require elevation** depending on
the channel and the machine's audit policy:

| Channel | Typical requirement |
|---|---|
| `System` | Usually readable as a normal user |
| `Application` | Usually readable as a normal user |
| `Security` | Requires elevation — **not in the RESON allowlist by default** |

The `Security` channel is deliberately excluded. It contains authentication
and privilege events whose content is sensitive.

### Starting and stopping the collector

```powershell
.\.venv\Scripts\python.exe -m src.data.agent --backend sqlite --interval 5 --events
```

### Platform capabilities

| Capability | Supported | Notes |
|---|---|---|
| CPU | Yes | Utilization, core counts, frequency |
| Memory | Yes | Total, used, available, utilization |
| Swap | Yes | Through the pagefile counters |
| Disk | Yes | Per-volume capacity plus I/O counters |
| Network | Yes | Per-interface counters, packets, errors, drops |
| Processes | Yes | PID, name, CPU, RSS, status |
| Battery | Yes | Present/absent reported honestly |
| Sensors | Best effort | Requires WMI or vendor drivers; empty when unavailable |
| System events | Yes | Bounded, allowlisted, deduplicated, with a checkpoint |

### Windows event collection

Event collection is enabled with `--events` (or `collect_events=True` in the
agent configuration).

How it behaves:

* **Allowlist only.** By default it reads the `System` and `Application`
  channels, and within them only a specific list of operationally relevant
  event identifiers (for example `6008` unexpected shutdown, `7031` service
  terminated unexpectedly, `1000` application fault). The list is in
  `src/data/windows_event_log.py` and is configurable.
* **Bounded.** Each collection is limited to a recent time window (60 minutes
  by default) and a maximum number of records (200 by default).
* **Deduplicated.** Each record carries a stable identifier, and a checkpoint
  file records what has already been ingested, so a restart does not
  re-ingest the same events.
* **Permission aware.** If a channel cannot be read, the failure is reported
  explicitly with the reason. RESON never presents "could not read" as "there
  were no events".
* **Content-minimal.** Only the event's own message text is kept, truncated
  to a bounded length. RESON does not ingest raw log payloads.

Two backends are used automatically: `pywin32`'s `win32evtlog` when it is
installed, and the built-in `wevtutil` command otherwise.

### Known limitations

* **Platform validation is pending.** The collector and the event-log
  collector are implemented and covered by offline tests, but no real Windows
  run has been performed in this environment. Treat Windows as
  *implemented, unvalidated* until it is run on a Windows machine.
* **Sensor readings need drivers.** `psutil.sensors_temperatures()` returns
  little on Windows without vendor support. RESON reports an empty set
  instead of a fabricated value.
* **Event-log collection needs `pywin32` for the richest output.** Without
  it, `wevtutil` is used, which is slower and returns less detail.
* **The `Security` channel is not collected by default.** Enabling it would
  require elevation and would collect authentication events; RESON leaves
  that decision explicit and to the operator.

---

## 4. macOS

### Supported Python versions

Python 3.11, 3.12, and 3.13 are recommended.

### Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### Permissions

**None required** for the supported measurements.

### Starting and stopping the collector

```bash
python -m src.data.agent --backend sqlite --interval 5
```

### Platform capabilities

| Capability | Supported | Notes |
|---|---|---|
| CPU | Yes | Utilization, core counts, frequency, load average |
| Memory | Yes | Total, used, available, utilization |
| Swap | Yes | Used, total, utilization |
| Disk | Yes | Per-volume capacity plus I/O counters |
| Network | Yes | Per-interface counters, packets, errors, drops |
| Processes | Yes | PID, name, CPU, RSS, status |
| Battery | Yes | Present/absent reported honestly (a Mac mini has none) |
| Sensors | **No** | macOS hides CPU/GPU temperatures from unprivileged processes. The capability is reported as `False` and RESON returns an empty set rather than a fabricated reading. |
| System events | Not implemented | The macOS unified log is not read yet; the Windows event-log collector is Windows-only |

### Known limitations

* **Platform validation is pending.** The implementation is complete and
  covered by the shared test suite, but no real macOS run has been performed.
* **No temperature sensors.** This is an operating-system restriction, not a
  RESON limitation. RESON reports the capability as unsupported so the
  dashboard shows "not available" instead of a made-up number.
* **No unified-log collection yet.**

---

## 5. Cross-platform behaviour that is guaranteed

| Behaviour | Guarantee |
|---|---|
| Source attribution | Every record carries the correct platform source. A Linux host is never labelled `windows_local`. |
| Host identity | Stable, non-secret, hashed machine identifier. Never a username, home path, or IP address. |
| Timestamps | Persisted in UTC. Naive legacy timestamps are treated as UTC and read back as aware datetimes. |
| Unavailable data | Reported as unavailable with explicit capability metadata. Never fabricated, never a fake zero. |
| Counter semantics | Cumulative counters are stored as counters; rates are derived from successive samples with reset detection. |
| Privacy | No command-line arguments, environment variables, credentials, packet payloads, or browser history are collected on any platform. |
| Migrations | Non-destructive, versioned, repeatable, and applied transactionally. |

---

## 6. How to validate a platform yourself

To convert "implemented" into "validated" for Windows or macOS:

```bash
# 1. The implementation tests must pass.
python -m pytest -q tests/test_collector.py tests/test_platform_collectors.py \
    tests/test_windows_event_log.py tests/test_agent.py

# 2. The collector must gather real data on that machine.
python -m src.data.agent --backend sqlite --once

# 3. The stored records must carry the right provenance.
python -c "
import sqlite3, os
conn = sqlite3.connect(os.environ['RESON_SQLITE_PATH'])
print(conn.execute('SELECT source, COUNT(*) FROM metrics GROUP BY source').fetchall())
print(conn.execute('SELECT DISTINCT host_id FROM metrics').fetchall())
"

# 4. The dashboard must render every page in every mode.
python -m pytest -q tests/test_streamlit_app.py
```

Record the machine's OS version, Python version, and the output of step 2 in
the implementation plan, and update the support matrix above.
