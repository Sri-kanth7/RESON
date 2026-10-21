"""Source-aware monitoring queries and honest status derivation for RESON.

This module contains the domain logic the dashboard needs but that does not
belong in a Streamlit page:

* **Provenance-aware inventory** — which sources exist, how many records each
  holds, when each was last observed, and which hosts are being monitored.
* **Honest collector status** — derived from actual collector health, recent
  successful samples, and the data actually stored. A UI session flag is
  never treated as proof that a collector is running, and stale telemetry is
  never labelled "live".
* **Operating-mode resolution** — the dashboard's current mode (local
  monitoring, demonstration, hosted, imported history) and the telemetry
  source that mode is allowed to display.

Everything here is pure and deterministic so it can be tested without a
browser, a database, or a collector.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Iterable

from src.data.schemas import (
    Metric,
    SOURCE_HOSTED_COLLECTOR,
    SOURCE_IMPORTED,
    SOURCE_LINUX_LOCAL,
    SOURCE_MACOS_LOCAL,
    SOURCE_SIMULATION,
    SOURCE_WINDOWS_LOCAL,
    classify_source,
)
from src.data.storage import Storage

#: Default age after which telemetry is considered stale.
DEFAULT_STALE_AFTER_SECONDS = 120.0

#: Sources that represent genuine, locally collected host telemetry.
LOCAL_SOURCES = (SOURCE_WINDOWS_LOCAL, SOURCE_LINUX_LOCAL, SOURCE_MACOS_LOCAL)


class MonitoringMode(str, Enum):
    """The operating mode the dashboard is currently displaying."""

    LOCAL = "local"
    """Mode A — a collector on this computer contributes real telemetry."""

    DEMONSTRATION = "demonstration"
    """Mode B — explicitly labeled synthetic demonstration data."""

    HOSTED = "hosted"
    """Mode C — telemetry from a separately running local collector."""

    IMPORTED = "imported"
    """Mode D — imported historical data, never presented as live."""


class CollectorStatus(str, Enum):
    """Accurate, derived state of the monitoring pipeline."""

    RUNNING = "running"
    STOPPED = "stopped"
    DEGRADED = "degraded"
    NO_RECENT_TELEMETRY = "no_recent_telemetry"
    NO_DATA = "no_data"
    ERROR = "error"
    PERMISSION_DENIED = "permission_denied"
    UNSUPPORTED = "unsupported"
    HISTORICAL_ONLY = "historical_only"
    DEMONSTRATION = "demonstration"


#: Human-readable labels for each status.
STATUS_LABELS: dict[str, str] = {
    CollectorStatus.RUNNING.value: "Collector running",
    CollectorStatus.STOPPED.value: "Collector stopped",
    CollectorStatus.DEGRADED.value: "Collector degraded",
    CollectorStatus.NO_RECENT_TELEMETRY.value: "No recent telemetry",
    CollectorStatus.NO_DATA.value: "No data collected yet",
    CollectorStatus.ERROR.value: "Collector error",
    CollectorStatus.PERMISSION_DENIED.value: "Permission denied",
    CollectorStatus.UNSUPPORTED.value: "Data source not supported",
    CollectorStatus.HISTORICAL_ONLY.value: "Historical data only",
    CollectorStatus.DEMONSTRATION.value: "Demonstration data",
}


@dataclass(frozen=True)
class StatusReport:
    """A derived status with the evidence that produced it."""

    status: CollectorStatus
    detail: str
    last_observation: datetime | None = None
    age_seconds: float | None = None
    stale: bool = False

    @property
    def label(self) -> str:
        """Human-readable status label."""
        return STATUS_LABELS.get(self.status.value, self.status.value)

    @property
    def healthy(self) -> bool:
        """Whether the pipeline is currently reporting healthy live data."""
        return self.status == CollectorStatus.RUNNING

    @property
    def is_live(self) -> bool:
        """Whether the displayed data may be described as live.

        Only a running collector with a recent successful sample qualifies.
        Stale data, stopped collectors, and historical data never do.
        """
        return self.healthy and not self.stale


@dataclass(frozen=True)
class SourceSummary:
    """Summary of one telemetry source present in storage."""

    source: str
    metrics: int = 0
    logs: int = 0
    events: int = 0
    deployments: int = 0
    last_observed: datetime | None = None
    hosts: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        """Total records held for this source."""
        return self.metrics + self.logs + self.events + self.deployments

    @property
    def is_live(self) -> bool:
        """Whether this source represents real locally collected telemetry."""
        return self.source in LOCAL_SOURCES


@dataclass(frozen=True)
class HostSummary:
    """Summary of one monitored host."""

    host_id: str
    source: str
    metric_count: int = 0
    last_observed: datetime | None = None

    @property
    def is_live(self) -> bool:
        """Whether this host's data is real local telemetry."""
        return self.source in LOCAL_SOURCES


@dataclass(frozen=True)
class ProcessRow:
    """One aggregated process row for the process inventory."""

    pid: int
    name: str
    service: str
    source: str
    host_id: str | None
    last_seen: datetime
    samples: int = 0
    avg_cpu_percent: float | None = None
    max_cpu_percent: float | None = None
    last_memory_rss_bytes: int | None = None
    max_memory_rss_bytes: int | None = None
    status: str | None = None


@dataclass
class ModeContext:
    """The resolved operating mode and what it is allowed to display."""

    mode: MonitoringMode
    label: str
    description: str
    source: str | None = None
    """Telemetry source filter for this mode; ``None`` means "all sources"."""
    simulated: bool = False
    """Whether the mode displays synthetic data."""
    available: bool = True
    """Whether the mode has any data to display."""
    unavailable_reason: str | None = None
    detail: str = ""
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Source inventory
# ---------------------------------------------------------------------------


def source_inventory(storage: Storage) -> list[SourceSummary]:
    """Return a summary of every telemetry source present in storage.

    Sources are reported exactly as stored. A record without a source is
    reported as ``unknown`` — never inferred to be real telemetry.
    """
    summaries: list[SourceSummary] = []
    for raw_source in storage.list_sources():
        source = classify_source(raw_source or None)
        counts = storage.count_records(source=source)
        last_observed = storage.latest_observation(source=source)
        hosts = tuple(storage.list_hosts(source=source))
        summaries.append(
            SourceSummary(
                source=source,
                metrics=counts.get("metrics", 0),
                logs=counts.get("logs", 0),
                events=counts.get("events", 0),
                deployments=counts.get("deployments", 0),
                last_observed=last_observed,
                hosts=hosts,
            )
        )
    summaries.sort(key=lambda item: item.source)
    return summaries


def available_sources(storage: Storage) -> list[str]:
    """Return the sorted list of sources present in storage."""
    return [summary.source for summary in source_inventory(storage)]


def host_inventory(
    storage: Storage,
    source: str | None = None,
) -> list[HostSummary]:
    """Return a summary of every monitored host, optionally filtered by source.

    Built from indexed per-source queries rather than a full table scan, so
    the cost does not grow with the retained history.
    """
    sources = (
        [classify_source(source)]
        if source is not None
        else [classify_source(raw) for raw in storage.list_sources()]
    )

    summaries: list[HostSummary] = []
    for resolved in sources:
        for host_id in storage.list_hosts(source=resolved):
            counts = storage.count_records(source=resolved, host_id=host_id)
            summaries.append(
                HostSummary(
                    host_id=host_id,
                    source=resolved,
                    metric_count=counts.get("metrics", 0),
                    last_observed=storage.latest_observation(
                        source=resolved, host_id=host_id
                    ),
                )
            )
    summaries.sort(key=lambda item: (item.source, item.host_id))
    return summaries


def latest_observation(
    storage: Storage,
    source: str | None = None,
    host_id: str | None = None,
) -> datetime | None:
    """Return the timestamp of the most recent stored observation."""
    return storage.latest_observation(source=source, host_id=host_id)


# ---------------------------------------------------------------------------
# Status derivation
# ---------------------------------------------------------------------------


def derive_status(
    collector=None,
    storage: Storage | None = None,
    source: str | None = None,
    host_id: str | None = None,
    now: datetime | None = None,
    stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
) -> StatusReport:
    """Derive the accurate status of the monitoring pipeline.

    The derivation never trusts a UI session flag. It combines:

    * the collector's own reported state and last error,
    * the time of the last successful sample,
    * the most recent observation actually present in storage.

    A collector that is running but has produced no recent sample is
    ``degraded`` or ``no_recent_telemetry`` — never ``running`` with live
    data. Storage that only holds historical records is ``historical_only``.
    """
    reference = now or datetime.now(timezone.utc)
    last_observation = (
        latest_observation(storage, source=source, host_id=host_id)
        if storage is not None
        else None
    )
    age: float | None = None
    if last_observation is not None:
        age = max(0.0, (reference - last_observation).total_seconds())

    if collector is not None:
        last_error = getattr(collector, "last_error", None)

        if last_error:
            lowered = str(last_error).lower()
            if "permission" in lowered or "access denied" in lowered:
                return StatusReport(
                    status=CollectorStatus.PERMISSION_DENIED,
                    detail=str(last_error),
                    last_observation=last_observation,
                    age_seconds=age,
                    stale=age is not None and age > stale_after_seconds,
                )
            return StatusReport(
                status=CollectorStatus.ERROR,
                detail=str(last_error),
                last_observation=last_observation,
                age_seconds=age,
                stale=age is not None and age > stale_after_seconds,
            )

        running = bool(getattr(collector, "is_running", False))
        if running:
            if age is None:
                return StatusReport(
                    status=CollectorStatus.NO_RECENT_TELEMETRY,
                    detail=(
                        "The collector is running but no observation has been "
                        "stored yet."
                    ),
                    last_observation=last_observation,
                    age_seconds=age,
                    stale=True,
                )
            if age > stale_after_seconds:
                return StatusReport(
                    status=CollectorStatus.NO_RECENT_TELEMETRY,
                    detail=(
                        f"The collector is running but the newest observation "
                        f"is {age:.0f}s old."
                    ),
                    last_observation=last_observation,
                    age_seconds=age,
                    stale=True,
                )
            return StatusReport(
                status=CollectorStatus.RUNNING,
                detail=(
                    f"Collector running; newest observation {age:.0f}s old."
                ),
                last_observation=last_observation,
                age_seconds=age,
                stale=False,
            )

        # Collector exists but is not running.
        if last_observation is None:
            return StatusReport(
                status=CollectorStatus.NO_DATA,
                detail=(
                    "The collector is stopped and no telemetry has been "
                    "collected yet."
                ),
                last_observation=last_observation,
                age_seconds=age,
                stale=True,
            )
        return StatusReport(
            status=CollectorStatus.STOPPED,
            detail=(
                f"The collector is stopped; the newest observation is "
                f"{age:.0f}s old."
            ),
            last_observation=last_observation,
            age_seconds=age,
            stale=True,
        )

    # No collector at all.
    if storage is None:
        return StatusReport(
            status=CollectorStatus.NO_DATA,
            detail="No collector is attached and storage is unavailable.",
            stale=True,
        )
    if last_observation is None:
        return StatusReport(
            status=CollectorStatus.NO_DATA,
            detail=(
                "No collector is running and no telemetry has been stored "
                "yet."
            ),
            stale=True,
        )
    return StatusReport(
        status=CollectorStatus.HISTORICAL_ONLY,
        detail=(
            f"No collector is running; only historical data is available "
            f"(newest observation {age:.0f}s old)."
        ),
        last_observation=last_observation,
        age_seconds=age,
        stale=True,
    )


# ---------------------------------------------------------------------------
# Operating mode
# ---------------------------------------------------------------------------

#: The telemetry source each mode displays.
MODE_SOURCES: dict[MonitoringMode, str | None] = {
    MonitoringMode.LOCAL: None,  # resolved to the platform source at runtime
    MonitoringMode.DEMONSTRATION: SOURCE_SIMULATION,
    MonitoringMode.HOSTED: SOURCE_HOSTED_COLLECTOR,
    MonitoringMode.IMPORTED: SOURCE_IMPORTED,
}

MODE_LABELS: dict[MonitoringMode, str] = {
    MonitoringMode.LOCAL: "Local monitoring",
    MonitoringMode.DEMONSTRATION: "Demonstration",
    MonitoringMode.HOSTED: "Hosted monitoring",
    MonitoringMode.IMPORTED: "Imported history",
}

MODE_DESCRIPTIONS: dict[MonitoringMode, str] = {
    MonitoringMode.LOCAL: (
        "Real telemetry collected by a collector running on this computer."
    ),
    MonitoringMode.DEMONSTRATION: (
        "Clearly labeled synthetic data produced by the deterministic "
        "simulator. It is never presented as live system telemetry."
    ),
    MonitoringMode.HOSTED: (
        "Telemetry received from a separately running local collector through "
        "the authenticated ingestion path. Opening this page does not observe "
        "your computer."
    ),
    MonitoringMode.IMPORTED: (
        "Historical records imported from an external source. They are not "
        "live observations."
    ),
}


def platform_source() -> str:
    """Return the telemetry source of the platform this process runs on."""
    import platform as _platform

    system = _platform.system().strip().lower()
    if system.startswith("win"):
        return SOURCE_WINDOWS_LOCAL
    if system == "darwin":
        return SOURCE_MACOS_LOCAL
    return SOURCE_LINUX_LOCAL


def resolve_mode(
    mode: MonitoringMode | str,
    collector=None,
    storage: Storage | None = None,
    demo_active: bool = False,
) -> ModeContext:
    """Resolve the operating mode and the source it is allowed to display.

    The resolution is honest: a mode whose source holds no data reports
    ``available=False`` with a reason instead of silently showing another
    mode's data.
    """
    if isinstance(mode, str):
        try:
            resolved = MonitoringMode(mode)
        except ValueError:
            resolved = MonitoringMode.LOCAL
    else:
        resolved = mode

    if resolved is MonitoringMode.LOCAL:
        source = getattr(collector, "source", None) or platform_source()
    else:
        source = MODE_SOURCES.get(resolved)

    context = ModeContext(
        mode=resolved,
        label=MODE_LABELS[resolved],
        description=MODE_DESCRIPTIONS[resolved],
        source=source,
        simulated=resolved is MonitoringMode.DEMONSTRATION,
    )

    if storage is None:
        context.available = False
        context.unavailable_reason = "Storage is unavailable."
        return context

    if source is None:
        context.available = True
        return context

    present = {
        summary.source: summary for summary in source_inventory(storage)
    }
    summary = present.get(classify_source(source))
    if summary is None or summary.total == 0:
        context.available = False
        if resolved is MonitoringMode.HOSTED:
            context.unavailable_reason = (
                "No telemetry has been received from a remote collector. "
                "Hosted real monitoring is not active; start the local "
                "collector agent with remote ingestion enabled and "
                "authenticated."
            )
        elif resolved is MonitoringMode.DEMONSTRATION:
            context.unavailable_reason = (
                "No demonstration data has been generated yet. Run the local "
                "demonstration analysis from the sidebar."
            )
        else:
            context.unavailable_reason = (
                f"No records with source '{source}' are stored in the "
                "selected backend."
            )
        return context

    context.available = True
    context.detail = (
        f"{summary.total:,} record(s) from source '{summary.source}'."
    )
    if summary.last_observed is not None:
        context.detail += (
            f" Newest observation {summary.last_observed.strftime('%Y-%m-%d %H:%M:%S UTC')}."
        )
    if demo_active and resolved is not MonitoringMode.DEMONSTRATION:
        context.detail += (
            " A demonstration analysis has also run in this session; its "
            "records are stored under source 'simulation' and are excluded "
            "from this view."
        )
    return context


# ---------------------------------------------------------------------------
# Process inventory
# ---------------------------------------------------------------------------


def process_inventory(
    storage: Storage,
    source: str | None = None,
    host_id: str | None = None,
    limit: int = 100,
    since: datetime | None = None,
) -> list[ProcessRow]:
    """Return an aggregated process inventory from stored process metrics.

    Only process resource metrics (``process_cpu_percent`` and
    ``process_memory_rss_bytes``) are aggregated; no command line, arguments,
    or environment data is collected or shown.
    """
    metrics = storage.get_metrics(source=source, host_id=host_id)
    if since is not None:
        metrics = [m for m in metrics if m.timestamp >= since]

    rows: dict[tuple[str, int], dict] = {}
    for metric in metrics:
        if not metric.metric_name.startswith("process_"):
            continue
        # service is "<host_id>:<pid>" for process metrics.
        service = metric.service
        host_part, _, pid_part = service.rpartition(":")
        try:
            pid = int(pid_part)
        except ValueError:
            continue
        key = (host_part or service, pid)
        entry = rows.setdefault(
            key,
            {
                "name": service,
                "source": classify_source(metric.source),
                "host_id": metric.host_id,
                "last_seen": metric.timestamp,
                "samples": 0,
                "cpu_sum": 0.0,
                "cpu_count": 0,
                "max_cpu": None,
                "last_rss": None,
                "max_rss": None,
            },
        )
        entry["samples"] += 1
        if metric.timestamp >= entry["last_seen"]:
            entry["last_seen"] = metric.timestamp
        if metric.metric_name == "process_cpu_percent":
            entry["cpu_sum"] += metric.value
            entry["cpu_count"] += 1
            if entry["max_cpu"] is None or metric.value > entry["max_cpu"]:
                entry["max_cpu"] = metric.value
        elif metric.metric_name == "process_memory_rss_bytes":
            entry["last_rss"] = metric.value
            if entry["max_rss"] is None or metric.value > entry["max_rss"]:
                entry["max_rss"] = metric.value

    result = [
        ProcessRow(
            pid=pid,
            name=entry["name"],
            service=f"{host}:{pid}",
            source=entry["source"],
            host_id=entry["host_id"],
            last_seen=entry["last_seen"],
            samples=entry["samples"],
            avg_cpu_percent=(
                entry["cpu_sum"] / entry["cpu_count"]
                if entry["cpu_count"]
                else None
            ),
            max_cpu_percent=entry["max_cpu"],
            last_memory_rss_bytes=entry["last_rss"],
            max_memory_rss_bytes=entry["max_rss"],
        )
        for (host, pid), entry in rows.items()
    ]
    result.sort(
        key=lambda row: (
            -(row.max_cpu_percent or 0.0),
            -(row.last_memory_rss_bytes or 0.0),
        )
    )
    return result[: max(1, limit)]


# ---------------------------------------------------------------------------
# Metric series
# ---------------------------------------------------------------------------


def metric_series(
    storage: Storage,
    source: str | None = None,
    host_id: str | None = None,
    metric_names: Iterable[str] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict[str, list[tuple[datetime, float]]]:
    """Return time series per metric name, filtered by source/host/window."""
    names = set(metric_names) if metric_names is not None else None
    series: dict[str, list[tuple[datetime, float]]] = {}
    for metric in storage.get_metrics(
        source=source, host_id=host_id, start_time=start, end_time=end
    ):
        if names is not None and metric.metric_name not in names:
            continue
        series.setdefault(metric.metric_name, []).append(
            (metric.timestamp, metric.value)
        )
    for points in series.values():
        points.sort(key=lambda point: point[0])
    return series


def latest_metric_values(
    storage: Storage,
    source: str | None = None,
    host_id: str | None = None,
) -> dict[str, Metric]:
    """Return the most recent value of every metric name."""
    return {
        metric.metric_name: metric
        for metric in storage.get_latest_metrics(source=source, host_id=host_id)
    }


# ---------------------------------------------------------------------------
# Volumes, interfaces, counters and sampling gaps
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VolumeRow:
    """One filesystem/volume as observed in storage."""

    mountpoint: str
    host_id: str | None
    total_bytes: int | None = None
    used_bytes: int | None = None
    free_bytes: int | None = None
    percent: float | None = None
    read_bytes: int | None = None
    write_bytes: int | None = None
    last_seen: datetime | None = None


@dataclass(frozen=True)
class InterfaceRow:
    """One network interface as observed in storage."""

    interface: str
    host_id: str | None
    bytes_sent: int | None = None
    bytes_recv: int | None = None
    packets_sent: int | None = None
    packets_recv: int | None = None
    errin: int | None = None
    errout: int | None = None
    dropin: int | None = None
    dropout: int | None = None
    last_seen: datetime | None = None


def _split_subsystem_service(service: str) -> tuple[str, str, str] | None:
    """Split a ``<host>:<kind>:<name>`` service reference."""
    parts = service.split(":", 2)
    if len(parts) != 3:
        return None
    return parts[0], parts[1], parts[2]


def volume_inventory(
    storage: Storage,
    source: str | None = None,
    host_id: str | None = None,
) -> list[VolumeRow]:
    """Return the observed filesystems/volumes for the selected source."""
    latest: dict[tuple[str, str], dict] = {}
    for metric in storage.get_metrics(source=source, host_id=host_id):
        parsed = _split_subsystem_service(metric.service)
        if parsed is None or parsed[1] != "disk":
            continue
        key = (parsed[0], parsed[2])
        entry = latest.setdefault(
            key,
            {
                "host_id": metric.host_id,
                "total_bytes": None,
                "used_bytes": None,
                "free_bytes": None,
                "percent": None,
                "read_bytes": None,
                "write_bytes": None,
                "last_seen": None,
            },
        )
        field_name = {
            "disk_total_bytes": "total_bytes",
            "disk_used_bytes": "used_bytes",
            "disk_free_bytes": "free_bytes",
            "disk_percent": "percent",
            "disk_read_bytes": "read_bytes",
            "disk_write_bytes": "write_bytes",
        }.get(metric.metric_name)
        if field_name is None:
            continue
        if entry["last_seen"] is None or metric.timestamp >= entry["last_seen"]:
            entry[field_name] = metric.value
            entry["last_seen"] = metric.timestamp
    return [
        VolumeRow(
            mountpoint=key[1],
            host_id=value["host_id"],
            total_bytes=value["total_bytes"],
            used_bytes=value["used_bytes"],
            free_bytes=value["free_bytes"],
            percent=value["percent"],
            read_bytes=value["read_bytes"],
            write_bytes=value["write_bytes"],
            last_seen=value["last_seen"],
        )
        for key, value in latest.items()
    ]


def interface_inventory(
    storage: Storage,
    source: str | None = None,
    host_id: str | None = None,
) -> list[InterfaceRow]:
    """Return the observed network interfaces for the selected source."""
    latest: dict[tuple[str, str], dict] = {}
    for metric in storage.get_metrics(source=source, host_id=host_id):
        parsed = _split_subsystem_service(metric.service)
        if parsed is None or parsed[1] != "net":
            continue
        key = (parsed[0], parsed[2])
        entry = latest.setdefault(
            key,
            {
                "host_id": metric.host_id,
                "bytes_sent": None,
                "bytes_recv": None,
                "packets_sent": None,
                "packets_recv": None,
                "errin": None,
                "errout": None,
                "dropin": None,
                "dropout": None,
                "last_seen": None,
            },
        )
        field_name = {
            "network_bytes_sent": "bytes_sent",
            "network_bytes_recv": "bytes_recv",
            "network_packets_sent": "packets_sent",
            "network_packets_recv": "packets_recv",
            "network_errin": "errin",
            "network_errout": "errout",
            "network_dropin": "dropin",
            "network_dropout": "dropout",
        }.get(metric.metric_name)
        if field_name is None:
            continue
        if entry["last_seen"] is None or metric.timestamp >= entry["last_seen"]:
            entry[field_name] = metric.value
            entry["last_seen"] = metric.timestamp
    return [
        InterfaceRow(
            interface=key[1],
            host_id=value["host_id"],
            bytes_sent=value["bytes_sent"],
            bytes_recv=value["bytes_recv"],
            packets_sent=value["packets_sent"],
            packets_recv=value["packets_recv"],
            errin=value["errin"],
            errout=value["errout"],
            dropin=value["dropin"],
            dropout=value["dropout"],
            last_seen=value["last_seen"],
        )
        for key, value in latest.items()
    ]


def counter_rates(
    points: list[tuple[datetime, float]],
) -> list[tuple[datetime, float]]:
    """Derive per-second rates from successive cumulative counter samples.

    A counter that decreases between two samples has been **reset** (the
    device or interface was recreated). Such an interval is skipped rather
    than reported as a negative rate.
    """
    rates: list[tuple[datetime, float]] = []
    for (t0, v0), (t1, v1) in zip(points, points[1:]):
        elapsed = (t1 - t0).total_seconds()
        if elapsed <= 0:
            continue
        delta = v1 - v0
        if delta < 0:
            # Counter reset: the interval is not a valid rate.
            continue
        rates.append((t1, delta / elapsed))
    return rates


def sampling_gaps(
    points: list[tuple[datetime, float]],
    expected_interval_seconds: float = 60.0,
    factor: float = 3.0,
) -> list[tuple[datetime, datetime, float]]:
    """Return sampling gaps longer than ``factor`` × the expected interval.

    Each entry is ``(gap_start, gap_end, seconds)``. Gaps are reported so a
    missing or failed collection is visible instead of silently interpolating
    over it.
    """
    gaps: list[tuple[datetime, datetime, float]] = []
    threshold = max(expected_interval_seconds, 1.0) * factor
    for (t0, _), (t1, _) in zip(points, points[1:]):
        elapsed = (t1 - t0).total_seconds()
        if elapsed > threshold:
            gaps.append((t0, t1, elapsed))
    return gaps
