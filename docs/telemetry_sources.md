# Telemetry Sources and Data Catalog

This document is the reference for what a telemetry `source` means, which
metrics exist, and how to interpret each field. It is the companion to
`docs/security_and_privacy.md`, which describes what is *not* collected.

---

## 1. Source values

Every telemetry record carries a `source`. The permitted values are defined
in `src/data/schemas.py`.

| Source | Meaning | Live? | Produced by |
|---|---|---|---|
| `windows_local` | Real telemetry collected on a Windows computer | Yes | `WindowsTelemetryCollector` |
| `linux_local` | Real telemetry collected on a Linux computer | Yes | `LinuxTelemetryCollector` |
| `macos_local` | Real telemetry collected on a macOS computer | Yes | `MacOSTelemetryCollector` |
| `hosted_collector` | Telemetry sent to a hosted dashboard by a separately running collector | Yes | Remote ingestion (not implemented in this build) |
| `simulation` | Deterministic synthetic data from the simulator | No | `HistoricalDatasetBuilder` / `SystemSimulator` |
| `imported` | Historical records imported from an external source | No | External import |
| `unknown` | Provenance cannot be established | No | Legacy records, or any record with a missing/unrecognized source |

`classify_source(source)` returns `unknown` for `None` and for any
unrecognized value. A record is **never** inferred to be real merely because
it has a plausible timestamp or a plausible value.

### Filtering by source

Every telemetry read accepts a `source` and a `host_id` filter:

```python
storage.get_metrics(source="linux_local", host_id="reson-14336f25bdc8c777")
storage.get_logs(source="simulation")
storage.get_events(source="hosted_collector")
```

The dashboard applies these filters automatically, driven by the selected
monitoring mode.

---

## 2. Data catalog

### Host-level metrics

Stored with `service` = the host id.

| Metric name | Unit | Meaning |
|---|---|---|
| `cpu_percent` | percent | Host CPU utilization, as a percentage of **all** logical CPUs. Range `[0, 100]`. |
| `memory_percent` | percent | Physical memory utilization. |
| `memory_used_bytes` | bytes | Physical memory in use. |
| `memory_total_bytes` | bytes | Total physical memory. |
| `swap_percent` | percent | Swap/pagefile utilization. A genuine `0` means no swap is configured. |
| `swap_used_bytes` | bytes | Swap in use. |
| `swap_total_bytes` | bytes | Total swap. |

### Process metrics

Stored with `service` = `<host_id>:<pid>`.

| Metric name | Unit | Meaning |
|---|---|---|
| `process_cpu_percent` | percent | CPU consumed by the process as a percentage of **one** logical CPU. Range `[0, 100]`. Not comparable with `cpu_percent`. |
| `process_memory_rss_bytes` | bytes | Resident set size. |

Only PID, name, CPU, RSS, and status are collected. No command line, no
environment, no user identity.

### Disk metrics

Stored with `service` = `<host_id>:disk:<mountpoint>`.

| Metric name | Unit | Meaning |
|---|---|---|
| `disk_total_bytes` | bytes | Total capacity of the mount. |
| `disk_used_bytes` | bytes | Used capacity. |
| `disk_free_bytes` | bytes | Free capacity. |
| `disk_percent` | percent | Used percentage. |
| `disk_read_bytes` | bytes | **Cumulative** bytes read since boot. |
| `disk_write_bytes` | bytes | **Cumulative** bytes written since boot. |

`disk_read_bytes` and `disk_write_bytes` are counters, not rates. RESON
derives rates from successive samples and skips an interval where the
counter decreased (a device reset), so a negative rate is never shown.

### Network metrics

Stored with `service` = `<host_id>:net:<interface>`.

| Metric name | Unit | Meaning |
|---|---|---|
| `network_bytes_sent` | bytes | **Cumulative** bytes sent. |
| `network_bytes_recv` | bytes | **Cumulative** bytes received. |
| `network_packets_sent` | count | **Cumulative** packets sent. |
| `network_packets_recv` | count | **Cumulative** packets received. |
| `network_errin` | count | **Cumulative** inbound errors. |
| `network_errout` | count | **Cumulative** outbound errors. |
| `network_dropin` | count | **Cumulative** inbound drops. |
| `network_dropout` | count | **Cumulative** outbound drops. |

All network metrics are cumulative counters. No packet payloads or traffic
content are ever collected.

### Battery metrics

Stored with `service` = the host id. Only written when a battery is actually
present.

| Metric name | Unit | Meaning |
|---|---|---|
| `battery_percent` | percent | Charge percentage. |
| `battery_seconds_remaining` | seconds | Estimated time remaining. |

A machine with no battery produces **no battery metrics at all**. The
dashboard shows "not applicable" rather than a fabricated percentage.

### Sensor metrics

Stored with `service` = `<host_id>:sensor:<name>`.

| Metric name | Unit | Meaning |
|---|---|---|
| `sensor_value` | celsius / rpm | A hardware sensor reading. |

Where the OS exposes no sensors (typical for macOS, and for most virtual
machines), no sensor metrics are produced and the capability is reported as
unsupported.

### Host-level fields

| Field | Meaning |
|---|---|
| `timestamp` | Observation time. Persisted in UTC. |
| `service` | The host id for host metrics; a subsystem-scoped name otherwise. |
| `environment` | `development`, `staging`, or `production`. Real host telemetry is recorded as `production`. |
| `source` | The telemetry source (see §1). |
| `host_id` | The stable, non-secret host identifier. |

### Derived records (incidents and evidence)

`Incident` and `Evidence` are not observations — they are derived from
observations — but they carry the same two provenance fields so an
analytical result can always be traced back to the telemetry it came from.

| Field | Meaning |
|---|---|
| `Incident.source` | The source of the incident's supporting observations. Set only when they all agree; a cluster that mixes sources is left unattributed (read back as `unknown`). |
| `Incident.host_id` | The host the incident was detected on, under the same rule. |
| `Evidence.source` | The source of the observation the evidence link points at. |
| `Evidence.host_id` | The host that observation came from. |

A `None` value means "provenance not established" — for example an incident
detected before provenance was recorded. It is reported as `unknown` and is
never inferred to be live telemetry.

---

## 3. Time semantics

| Timestamp | Meaning |
|---|---|
| `timestamp` (on a record) | **Observation time** — when the measurement was taken on the host. |
| Database row | Stores the observation time in UTC as an ISO-8601 string. |
| `Incident.start_time` | The time of the first supporting signal. |
| `Incident.end_time` | The time of the last supporting signal, or `None` while the incident is open. |
| Ingestion time | Not currently recorded as a separate field. |
| Detection time | Not currently recorded as a separate field. |

**Naive timestamps** (no timezone) are treated as UTC when persisted and are
read back as timezone-aware UTC datetimes. This is the legacy-data path: the
wall-clock value is preserved as a UTC instant.

### Display timezone

Storage is always UTC. **Rendering** happens in a display timezone selected in
the sidebar (the host's own zone by default, or any IANA zone). The rules,
implemented in `src/common/timeutil.py`:

* A naive datetime is interpreted as UTC — never silently reinterpreted as
  local time.
* An aware datetime is *converted*, never relabelled: the instant is
  preserved and only the rendering changes.
* Every rendered timestamp names its zone, so a reader never has to guess
  which clock they are looking at.
* Daylight saving is applied per instant, using the offset that actually
  applied at that moment (so a repeated DST hour renders correctly).
* Chart axes are labelled with the zone.

The display timezone is a presentation concern only. It is never written to
telemetry, and the host's own zone is never stored with a record.

---

## 4. Where each source comes from

| Source | Produced by | Command |
|---|---|---|
| `linux_local`, `windows_local`, `macos_local` | The platform collector in the agent | `python -m src.data.agent --backend sqlite` |
| `simulation` | The deterministic simulator, through the dataset service | The **Run Local Demonstration Analysis** button, or `HistoricalDatasetService.build_and_persist()` |
| `hosted_collector` | Remote ingestion | **Not implemented in this build** |
| `imported` | An external import step | **Not implemented in this build** |
| `unknown` | Legacy records that predate provenance tracking | Automatic |

---

## 5. Inspecting the data

```python
from src.data.sqlite_storage import SQLiteStorage
from src.data.schemas import classify_source

storage = SQLiteStorage()          # or SQLiteStorage("/path/to/reson.db")

# What sources exist, and how much of each?
from src.data.monitoring import source_inventory
for summary in source_inventory(storage):
    print(summary.source, summary.total, summary.last_observed)

# Only real local telemetry:
live = storage.get_metrics(source="linux_local")

# Everything, including legacy records with no provenance:
everything = storage.get_metrics()

# Classify a record that has no source:
classify_source(None)              # -> "unknown"
```

---

## 6. Data flow summary

```
collector sample
  └─> HostSample / ProcessSample / DiskSample / NetworkSample / BatterySample / SensorSample
        └─> collection_to_metrics()          (src/data/collector.py)
              └─> Metric records, each carrying source + host_id
                    └─> TelemetryIngestionService.ingest_batch()
                          └─> Storage.save_metrics()
                                └─> SQLite / Supabase / memory
                                      └─> source-aware queries
                                            └─> baselines, deviations, change points
                                                  └─> incidents + evidence
                                                        └─> dashboard
```

At no point in that chain is the `source` dropped, rewritten, or inferred.
