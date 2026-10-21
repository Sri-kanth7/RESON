"""Processes page: sortable, paginated process inventory from real telemetry.

Only privacy-conscious process data is shown: an identifier, the process
name, CPU and memory utilization, and the observation time. RESON does not
collect command-line arguments, environment variables, or any other process
content.
"""

from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from app.components import ui
from app.services import backend as svc

#: Rows shown per page.
PAGE_SIZE = 25


def _format_bytes(value) -> str:
    if value is None:
        return "—"
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(size) < 1024.0 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TiB"


def render(ctx) -> None:
    ui.page_header(
        "Processes / resource consumers",
        "Process Inventory",
        "A privacy-conscious inventory built from real collector samples. "
        "Command-line arguments, environment variables, and other process "
        "contents are never collected.",
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

    left, right, third = st.columns([3, 2, 2])
    with left:
        window_label = st.selectbox(
            "Observation window",
            ("Last 5 minutes", "Last 15 minutes", "Last hour", "All history"),
            index=1,
        )
    with right:
        host_id = st.text_input(
            "Host filter (blank = all hosts)",
            value=ctx.host_id or "",
        )
        host_filter = host_id.strip() or None
    with third:
        limit = st.number_input(
            "Max processes",
            min_value=10,
            max_value=1000,
            value=200,
            step=10,
        )

    minutes = {
        "Last 5 minutes": 5,
        "Last 15 minutes": 15,
        "Last hour": 60,
        "All history": None,
    }[window_label]
    since = (
        datetime.now(timezone.utc) - timedelta(minutes=minutes)
        if minutes is not None
        else None
    )

    rows = svc.process_inventory(
        storage,
        source=source,
        host_id=host_filter,
        limit=int(limit),
        since=since,
    )

    with st.container(border=True):
        ui.panel_header(
            "Process inventory",
            f"{len(rows)} process(es) observed · {window_label}",
        )
        if not rows:
            ui.empty_state(
                "No process samples in this window",
                "No process resource metrics are stored for the selected "
                "source, host, and time range. Process data only exists once "
                "a collector has run on this machine.",
            )
        else:
            latest_seen = max(row.last_seen for row in rows)
            c1, c2, c3 = st.columns(3)
            with c1:
                ui.metric_card(
                    "Processes observed",
                    str(len(rows)),
                    sub="distinct PIDs in this window",
                )
            with c2:
                top_cpu = rows[0]
                ui.metric_card(
                    "Top CPU consumer",
                    f"{top_cpu.max_cpu_percent or 0.0:.1f}%" if top_cpu.max_cpu_percent else "—",
                    sub=f"pid {top_cpu.pid}",
                )
            with c3:
                ui.metric_card(
                    "Last refreshed",
                    ctx.format_clock(latest_seen),
                    sub=f"newest observation in this set ({ctx.timezone_name})",
                )

            frame = pd.DataFrame(
                [
                    {
                        "PID": row.pid,
                        "Host": row.host_id or "—",
                        "Source": row.source,
                        "Avg CPU %": (
                            round(row.avg_cpu_percent, 2)
                            if row.avg_cpu_percent is not None
                            else "—"
                        ),
                        "Peak CPU %": (
                            round(row.max_cpu_percent, 2)
                            if row.max_cpu_percent is not None
                            else "—"
                        ),
                        "RSS (last)": _format_bytes(row.last_memory_rss_bytes),
                        "RSS (peak)": _format_bytes(row.max_memory_rss_bytes),
                        "Samples": row.samples,
                        f"Last seen ({ctx.timezone_name})": ctx.format_timestamp(
                            row.last_seen
                        ),
                    }
                    for row in rows
                ]
            )
            st.dataframe(
                frame,
                width="stretch",
                hide_index=True,
                column_config={
                    "PID": st.column_config.NumberColumn("PID", width="small"),
                    "Avg CPU %": st.column_config.ProgressColumn(
                        "Avg CPU %",
                        min_value=0.0,
                        max_value=100.0,
                        format="%.2f",
                    ),
                    "Peak CPU %": st.column_config.NumberColumn(
                        "Peak CPU %", format="%.2f"
                    ),
                },
            )
            st.caption(
                f"Showing up to {len(rows)} processes, ordered by peak CPU "
                "then resident memory. The full table is filterable and "
                "sortable in the dataframe view."
            )
