"""System Resources page: historical CPU, memory, disk, and network analysis.

Every series shown here comes from the telemetry source selected by the
current monitoring mode. Sampling gaps and counter resets are reported
explicitly rather than being smoothed over.
"""

from datetime import datetime, timedelta, timezone

import altair as alt
import pandas as pd
import streamlit as st

from app.components import ui
from app.services import backend as svc
from src.data.monitoring import counter_rates, sampling_gaps

#: Selectable analysis windows, in minutes. ``None`` means the full history.
TIME_RANGES: tuple[tuple[str, int | None], ...] = (
    ("Last 15 minutes", 15),
    ("Last hour", 60),
    ("Last 6 hours", 360),
    ("Last 24 hours", 1440),
    ("Last 7 days", 10080),
    ("All history", None),
)

#: Expected collection interval used to detect sampling gaps.
EXPECTED_INTERVAL_SECONDS = 30.0


def _format_bytes(value) -> str:
    if value is None:
        return "—"
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(size) < 1024.0 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TiB"


def _format_rate(value) -> str:
    if value is None:
        return "—"
    return f"{value / 1024.0:.1f} KiB/s"


def _time_series_chart(frame: pd.DataFrame, title: str, ctx) -> object:
    chart = (
        alt.Chart(frame)
        .mark_line(strokeWidth=1.8)
        .encode(
            x=alt.X("time:T", title=ctx.chart_axis_title()),
            y=alt.Y("value:Q", title="Percent"),
            color=alt.Color(
                "metric:N",
                scale=alt.Scale(
                    domain=["cpu_percent", "memory_percent"],
                    range=["#00E5FF", "#F59E0B"],
                ),
            ),
            tooltip=[
                alt.Tooltip("time:T", title="Time", format="%Y-%m-%d %H:%M:%S"),
                alt.Tooltip("metric:N", title="Metric"),
                alt.Tooltip("value:Q", title="Value", format=".2f"),
            ],
        )
        .properties(height=240, title=title)
    )
    return ui.darken(chart)


def render(ctx) -> None:
    ui.page_header(
        "System resources / historical analysis",
        "Resource History &amp; Capacity",
        "CPU, memory, disk, and network history for the selected source. "
        "Counter-derived rates are computed from successive samples; a reset "
        "counter is skipped rather than reported as a negative rate.",
    )

    ui.mode_banner(
        mode_label=ctx.mode.label,
        description=ctx.mode.description,
        available=ctx.mode.available,
        reason=ctx.mode.unavailable_reason,
        detail=ctx.mode.detail or None,
        simulated=ctx.simulated,
    )

    storage = ctx.storage
    source = ctx.source

    left, right = st.columns([3, 2])
    with left:
        range_label = st.selectbox(
            "Analysis window",
            [label for label, _ in TIME_RANGES],
            index=1,
        )
    with right:
        host_id = st.text_input(
            "Host filter (blank = all hosts)",
            value=ctx.host_id or "",
            help="Filter every panel below to one host identifier.",
        )
        host_filter = host_id.strip() or None

    minutes = dict(TIME_RANGES)[range_label]
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=minutes) if minutes is not None else None

    series = svc.metric_series(
        storage,
        source=source,
        host_id=host_filter,
        metric_names=("cpu_percent", "memory_percent"),
        start=start,
        end=end,
    )

    # ------------------------------------------------------------------
    # CPU / memory
    # ------------------------------------------------------------------
    points = []
    for name, values in series.items():
        for timestamp, value in values:
            points.append(
                {
                    "time": ctx.to_chart_time(timestamp),
                    "metric": name,
                    "value": value,
                }
            )

    with st.container(border=True):
        ui.panel_header(
            "CPU and memory",
            f"{range_label} · source: {source or 'all'}",
        )
        if not points:
            ui.empty_state(
                "No samples in this window",
                "No CPU or memory samples are stored for the selected source "
                "and time range.",
            )
        else:
            frame = pd.DataFrame(points)
            st.altair_chart(
                _time_series_chart(frame, "CPU and memory utilization", ctx),
                width="stretch",
            )
            cpu_points = series.get("cpu_percent", [])
            memory_points = series.get("memory_percent", [])
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                ui.metric_card(
                    "CPU samples",
                    str(len(cpu_points)),
                    sub="in this window",
                )
            with c2:
                ui.metric_card(
                    "CPU mean",
                    (
                        f"{sum(v for _, v in cpu_points) / len(cpu_points):.1f}%"
                        if cpu_points
                        else "—"
                    ),
                    sub="across the window",
                )
            with c3:
                ui.metric_card(
                    "Memory mean",
                    (
                        f"{sum(v for _, v in memory_points) / len(memory_points):.1f}%"
                        if memory_points
                        else "—"
                    ),
                    sub="across the window",
                )
            with c4:
                gaps = sampling_gaps(
                    cpu_points or memory_points,
                    expected_interval_seconds=EXPECTED_INTERVAL_SECONDS,
                )
                ui.metric_card(
                    "Sampling gaps",
                    str(len(gaps)),
                    sub=(
                        f"longest {max(g[2] for g in gaps):.0f}s"
                        if gaps
                        else "none detected"
                    ),
                    badge="GAP" if gaps else "CONTIGUOUS",
                    accent="reson-accent-high" if gaps else None,
                )

            if gaps:
                st.markdown(
                    "**Collection gaps detected** — data is missing between "
                    "these times; the chart does not interpolate across them."
                )
                st.dataframe(
                    [
                        {
                            f"Gap start ({ctx.timezone_name})": (
                                ctx.format_timestamp(g[0])
                            ),
                            f"Gap end ({ctx.timezone_name})": (
                                ctx.format_timestamp(g[1])
                            ),
                            "Seconds": round(g[2], 1),
                        }
                        for g in gaps[:20]
                    ],
                    width="stretch",
                    hide_index=True,
                )

    # ------------------------------------------------------------------
    # Disk
    # ------------------------------------------------------------------
    volumes = svc.volume_inventory(storage, source=source, host_id=host_filter)
    with st.container(border=True):
        ui.panel_header(
            "Disk capacity",
            "Latest observed value per mounted volume",
        )
        if not volumes:
            ui.empty_state(
                "No disk samples stored",
                "The selected source contains no filesystem observations. "
                "Disk metrics are only present when a collector that reports "
                "them has been running.",
            )
        else:
            st.dataframe(
                [
                    {
                        "Mount point": volume.mountpoint,
                        "Host": volume.host_id or "—",
                        "Total": _format_bytes(volume.total_bytes),
                        "Used": _format_bytes(volume.used_bytes),
                        "Free": _format_bytes(volume.free_bytes),
                        "Used %": (
                            f"{volume.percent:.1f}%"
                            if volume.percent is not None
                            else "—"
                        ),
                        f"Last seen ({ctx.timezone_name})": (
                            ctx.format_timestamp(volume.last_seen)
                            if volume.last_seen
                            else "—"
                        ),
                    }
                    for volume in sorted(
                        volumes, key=lambda v: -(v.percent or 0.0)
                    )
                ],
                width="stretch",
                hide_index=True,
            )

    # ------------------------------------------------------------------
    # Network
    # ------------------------------------------------------------------
    interfaces = svc.interface_inventory(
        storage, source=source, host_id=host_filter
    )
    with st.container(border=True):
        ui.panel_header(
            "Network interfaces",
            "Latest cumulative counters; rates are derived from successive "
            "samples",
        )
        if not interfaces:
            ui.empty_state(
                "No network samples stored",
                "The selected source contains no network interface "
                "observations.",
            )
        else:
            rows = []
            for interface in interfaces:
                sent_series = svc.metric_series(
                    storage,
                    source=source,
                    host_id=host_filter,
                    metric_names=("network_bytes_sent",),
                    start=start,
                    end=end,
                ).get("network_bytes_sent", [])
                recv_series = svc.metric_series(
                    storage,
                    source=source,
                    host_id=host_filter,
                    metric_names=("network_bytes_recv",),
                    start=start,
                    end=end,
                ).get("network_bytes_recv", [])
                sent_rates = counter_rates(sent_series)
                recv_rates = counter_rates(recv_series)
                rows.append(
                    {
                        "Interface": interface.interface,
                        "Bytes sent": _format_bytes(interface.bytes_sent),
                        "Bytes received": _format_bytes(interface.bytes_recv),
                        "Send rate": (
                            _format_rate(sent_rates[-1][1]) if sent_rates else "—"
                        ),
                        "Receive rate": (
                            _format_rate(recv_rates[-1][1]) if recv_rates else "—"
                        ),
                        "Errors in/out": (
                            f"{interface.errin or 0} / {interface.errout or 0}"
                        ),
                        "Drops in/out": (
                            f"{interface.dropin or 0} / {interface.dropout or 0}"
                        ),
                        f"Last seen ({ctx.timezone_name})": (
                            ctx.format_timestamp(interface.last_seen)
                            if interface.last_seen
                            else "—"
                        ),
                    }
                )
            st.dataframe(rows, width="stretch", hide_index=True)
            st.caption(
                "Rates are derived from successive samples of the cumulative "
                "counters. A counter that decreased (a reset) is skipped, so "
                "no negative rate is ever shown. No packet payloads or "
                "traffic content are collected."
            )
