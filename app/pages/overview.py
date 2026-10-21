"""Operations Overview page: real counts, real analysis, honest empty states.

Every figure on this page is derived from the telemetry source selected by the
current monitoring mode. Demonstration data and live host telemetry are never
combined into a single headline number without saying so.
"""

import altair as alt
import pandas as pd
import streamlit as st

from datetime import datetime, timedelta, timezone

from app import theme as th
from app.components import ui
from app.services import backend as svc


def _severity_counts(incidents) -> dict[str, int]:
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for incident in incidents:
        value = str(getattr(incident.severity, "value", incident.severity))
        if value in counts:
            counts[value] += 1
    return counts


def _activity_frame(incidents, ctx) -> pd.DataFrame:
    """Bucket incidents by start hour from real stored records.

    Hours are bucketed in the *display* timezone, so the bucket boundaries
    match the clock the reader sees.
    """
    if not incidents:
        return pd.DataFrame(columns=["hour", "incidents"])
    frame = pd.DataFrame(
        {"hour": [ctx.to_chart_time(i.start_time) for i in incidents]}
    ).assign(hour=lambda d: d["hour"].dt.floor("h"))
    grouped = frame.groupby("hour").size().reset_index(name="incidents")
    return grouped.sort_values("hour")


def _percent(value) -> str:
    return "—" if value is None else f"{value:.1f}%"


def _bytes(value) -> str:
    if value is None:
        return "—"
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(size) < 1024.0 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TiB"


def render(ctx, live: dict | None = None) -> None:
    """Render the operations overview for the active monitoring mode."""
    ui.page_header(
        "Fleet observability / incident overview",
        "System Operations &amp; Incident Pulse",
        "Deterministic incident intelligence over stored telemetry. Every "
        "figure below comes from the selected backend and the active data "
        "source — never invented, and never mixed across sources.",
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

    # ------------------------------------------------------------------
    # Collector / pipeline status (derived, never a session flag)
    # ------------------------------------------------------------------
    status = ctx.status
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        ui.metric_card(
            "Pipeline status",
            status.label,
            sub=status.detail,
            badge="LIVE" if ctx.live else "NOT LIVE",
            accent=None if ctx.live else "reson-accent-high",
        )
    with c2:
        age = status.age_seconds
        ui.metric_card(
            "Telemetry freshness",
            "—" if age is None else f"{age:.0f}s",
            sub=(
                "since last observation"
                if age is not None
                else "no observation recorded"
            ),
            badge="FRESH" if (age is not None and age <= 120) else "STALE",
        )
    with c3:
        latest = (
            ctx.format_timestamp(status.last_observation)
            if status.last_observation is not None
            else "never"
        )
        ui.metric_card(
            "Last observation",
            latest,
            sub=f"source: {source or 'all'}",
        )
    with c4:
        ui.metric_card(
            "Monitored hosts",
            str(len(ctx.hosts)),
            sub="hosts with data in this source",
            badge=ctx.mode.label.upper(),
        )

    if not ctx.mode.available:
        with st.container(border=True):
            ui.panel_header(
                "Nothing to display for this mode",
                ctx.mode.unavailable_reason or "No data is available.",
            )
            st.markdown(
                "The dashboard will not substitute another mode's data for "
                "this one. Change the monitoring mode in the sidebar, start "
                "the local collector, or run the demonstration analysis."
            )

    counts = svc.storage_counts(storage, source=source)
    backend_total = sum(
        item.total for item in ctx.sources
    )
    incidents = svc.ordered_incidents(storage, source=source)
    active = [
        i
        for i in incidents
        if str(getattr(i.status, "value", i.status)).lower() != "resolved"
    ]
    severities = _severity_counts(incidents)
    critical_high = severities["critical"] + severities["high"]
    total = (
        counts["metrics"]
        + counts["logs"]
        + counts["events"]
        + counts["deployments"]
    )

    # ------------------------------------------------------------------
    # Resource snapshot (real stored values for this source)
    # ------------------------------------------------------------------
    latest_metrics = svc.latest_metric_values(
        storage, source=source, host_id=ctx.host_id
    )
    cpu = latest_metrics.get("cpu_percent")
    memory = latest_metrics.get("memory_percent")
    memory_total = latest_metrics.get("memory_total_bytes")

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        ui.metric_card(
            "CPU utilization",
            _percent(cpu.value if cpu else None),
            sub="host CPU, percent of all logical cores",
            badge="PERCENT" if cpu else "UNAVAILABLE",
        )
    with c2:
        ui.metric_card(
            "Memory utilization",
            _percent(memory.value if memory else None),
            sub=f"total {_bytes(memory_total.value) if memory_total else '—'}",
            badge="PERCENT" if memory else "UNAVAILABLE",
        )
    with c3:
        disk_metrics = [
            m
            for name, m in latest_metrics.items()
            if name.startswith("disk_")
        ]
        ui.metric_card(
            "Disk capacity",
            "—",
            sub="per-volume detail on System Resources",
            badge="SEE DETAIL" if disk_metrics else "NOT COLLECTED",
        )
    with c4:
        net_metrics = [
            m
            for name, m in latest_metrics.items()
            if name.startswith("network_")
        ]
        ui.metric_card(
            "Network throughput",
            "—",
            sub="per-interface detail on System Resources",
            badge="SEE DETAIL" if net_metrics else "NOT COLLECTED",
        )

    # ------------------------------------------------------------------
    # KPI bar
    # ------------------------------------------------------------------
    k1, k2, k3, k4 = st.columns(4)
    with k1:
        ui.metric_card(
            "Active incidents",
            str(len(active)),
            sub=f"{critical_high} critical/high · {len(incidents)} total stored",
            badge=f"{severities['critical']} P1 · {severities['high']} P2",
            trend_dir="up" if active else "ok",
            accent="reson-accent-critical" if critical_high else None,
        )
    with k2:
        demo = ctx.demo
        if demo is not None and demo.analysis is not None:
            score = demo.analysis.overall_score
            changed = ", ".join(demo.analysis.changed_services) or "none"
            ui.metric_card(
                "Behavior shift",
                f"{score:.3f}",
                sub=f"changed: {changed}",
                badge="ELEVATED" if score >= 0.5 else "STEADY",
                spark=[
                    r.shift.score for r in demo.analysis.service_results
                ]
                or None,
            )
        else:
            ui.metric_card(
                "Behavior shift",
                "—",
                sub="no analysis in the current session",
                badge="NO DATA",
            )
    with k3:
        ui.metric_card(
            "Telemetry volume",
            f"{total:,}",
            sub=(
                f"{counts['metrics']:,} metrics · {counts['logs']:,} logs · "
                f"{counts['events']:,} events · "
                f"{counts['deployments']:,} deployments"
            ),
            badge=f"source: {source or 'all'}",
        )
    with k4:
        combined = sum(item.total for item in ctx.sources)
        shown = sum(
            item.total
            for item in ctx.sources
            if source is None or item.source == source
        )
        ui.metric_card(
            "Scope",
            f"{shown:,}",
            sub=(
                f"of {combined:,} records across all sources"
                if combined > shown
                else "records in the selected source"
            ),
            badge=("COMBINED" if combined > shown else "FILTERED"),
        )

    if total == 0:
        with st.container(border=True):
            if backend_total == 0:
                ui.panel_header(
                    "First run — storage is empty",
                    "No telemetry is stored in the selected backend.",
                )
                st.markdown(
                    "Start the local collector to collect real telemetry, or "
                    "run the local demonstration analysis from the sidebar. "
                    "RESON never substitutes synthetic data for missing live "
                    "data."
                )
            else:
                ui.panel_header(
                    "No telemetry in this source",
                    f"The backend holds no records with source '{source}'.",
                )
                st.markdown(
                    "Other sources are present in this backend. Switch the "
                    "monitoring mode in the sidebar to view them, or start "
                    "the local collector to produce live telemetry for this "
                    "source."
                )

    # ------------------------------------------------------------------
    # Historical trend
    # ------------------------------------------------------------------
    # The overview shows a bounded window so page load time does not grow
    # with the retained history. The System Resources page offers longer
    # windows for deeper analysis.
    trend_window_hours = 24
    trend_start = datetime.now(timezone.utc) - timedelta(hours=trend_window_hours)
    series = svc.metric_series(
        storage,
        source=source,
        host_id=ctx.host_id,
        metric_names=("cpu_percent", "memory_percent"),
        start=trend_start,
    )
    trend_points = []
    for name, points in series.items():
        for timestamp, value in points:
            trend_points.append(
                {
                    "time": ctx.to_chart_time(timestamp),
                    "metric": name,
                    "value": value,
                }
            )
    if trend_points:
        frame = pd.DataFrame(trend_points)
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
            .properties(height=220)
        )
        with st.container(border=True):
            ui.panel_header(
                "Resource trend",
                f"CPU and memory over the last {trend_window_hours} hours "
                f"(source: {source or 'all'})",
            )
            st.altair_chart(ui.darken(chart), width="stretch")
            st.caption(
                "This overview shows a bounded window so it stays fast. Use "
                "**System Resources** for longer ranges and sampling-gap "
                "analysis."
            )
    else:
        with st.container(border=True):
            ui.panel_header(
                "Resource trend",
                f"No samples in the last {trend_window_hours} hours",
            )
            st.markdown(
                "No CPU or memory samples are stored for this source in the "
                "displayed window."
            )

    # ------------------------------------------------------------------
    # Incident activity + severity distribution
    # ------------------------------------------------------------------
    left, right = st.columns([8, 4])
    with left:
        with st.container(border=True):
            ui.panel_header(
                "Incident activity over time",
                "Stored incidents bucketed by start hour "
                f"({ctx.timezone_name} local)",
            )
            frame = _activity_frame(incidents, ctx)
            if frame.empty:
                st.markdown(
                    "No incidents stored. Zero is a valid analytical result."
                )
            else:
                chart = (
                    alt.Chart(frame)
                    .mark_bar(color="#00E5FF", size=14)
                    .encode(
                        x=alt.X(
                            "hour:T",
                            title=f"Start hour ({ctx.timezone_name})",
                            axis=alt.Axis(format="%m-%d %H:%M"),
                        ),
                        y=alt.Y(
                            "incidents:Q",
                            title="Incidents",
                            axis=alt.Axis(format="d"),
                        ),
                        tooltip=[
                            alt.Tooltip(
                                "hour:T", title="Hour", format="%Y-%m-%d %H:%M"
                            ),
                            alt.Tooltip("incidents:Q", title="Incidents"),
                        ],
                    )
                    .properties(height=220)
                )
                st.altair_chart(ui.darken(chart), width="stretch")
    with right:
        with st.container(border=True):
            ui.panel_header("Severity distribution", "All stored incidents")
            if not incidents:
                st.markdown("No incidents stored.")
            else:
                sev_frame = pd.DataFrame(
                    [
                        {"severity": name.upper(), "incidents": value}
                        for name, value in severities.items()
                    ]
                )
                colors = {
                    "CRITICAL": "#EF4444",
                    "HIGH": "#F59E0B",
                    "MEDIUM": "#F59E0B",
                    "LOW": "#10B981",
                }
                chart = (
                    alt.Chart(sev_frame)
                    .mark_bar()
                    .encode(
                        y=alt.Y(
                            "severity:N",
                            title=None,
                            sort=["CRITICAL", "HIGH", "MEDIUM", "LOW"],
                        ),
                        x=alt.X("incidents:Q", title="Incidents"),
                        color=alt.Color(
                            "severity:N",
                            scale=alt.Scale(
                                domain=list(colors),
                                range=list(colors.values()),
                            ),
                            legend=None,
                        ),
                        tooltip=["severity", "incidents"],
                    )
                    .properties(height=140)
                )
                st.altair_chart(ui.darken(chart), width="stretch")

    # ------------------------------------------------------------------
    # Registry + recent events
    # ------------------------------------------------------------------
    left, right = st.columns([8, 4])
    with left:
        with st.container(border=True):
            ui.panel_header(
                "Correlated Incident Registry",
                f"{len(incidents)} logged · {len(active)} active",
            )
            if not incidents:
                if ctx.demo is not None:
                    st.markdown(
                        "Analysis complete — **no incidents detected** in this "
                        "window. Zero is a valid result: no trigger evidence "
                        "met the correlation policy."
                    )
                else:
                    st.markdown(
                        "Telemetry is stored but no analysis has run yet. Use "
                        "**Run Local Demonstration Analysis** to analyze it."
                    )
            else:
                for incident in incidents:
                    ui.registry_card(
                        severity=incident.severity,
                        identifier=th.short_id(incident.id),
                        title=incident.title,
                        status=incident.status,
                        meta=(
                            f"{ctx.format_timestamp(incident.start_time)}"
                            f" · {', '.join(incident.affected_services) or '—'}"
                        ),
                    )
    with right:
        with st.container(border=True):
            ui.panel_header(
                "Recent events",
                f"Events stored with source '{source}'",
            )
            events = svc.recent_events(storage, source=source, limit=12)
            if not events:
                st.markdown("No events stored for this source.")
            else:
                for event in events:
                    ui.stream_item(
                        ctx.format_timestamp(
                            event.timestamp, fmt="%m-%d %H:%M:%S"
                        ),
                        str(
                            getattr(event.event_type, "value", event.event_type)
                        ),
                        event.description,
                        "chip-corr",
                    )

    # ------------------------------------------------------------------
    # Live collector detail (only meaningful in local mode)
    # ------------------------------------------------------------------
    collector = ctx.collector
    if collector is not None and live is not None:
        with st.container(border=True):
            ui.panel_header(
                "Live collector",
                f"Source: {collector.source} · host: {collector.host_id} · "
                f"ingesting into the selected ({ctx.backend_name}) backend",
            )
            health = collector.health()
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                ui.metric_card(
                    "Collector state",
                    health.state.upper(),
                    sub=(
                        f"{health.sample_count} sample(s) · "
                        f"{health.error_count} error(s)"
                    ),
                    badge="RUNNING" if health.running else "STOPPED",
                )
            with c2:
                host = collector.last_host_measurement
                ui.metric_card(
                    "CPU",
                    (
                        f"{host.cpu_percent:.1f}%"
                        if host is not None and host.cpu_percent is not None
                        else "—"
                    ),
                    sub="in-process sample",
                )
            with c3:
                host = collector.last_host_measurement
                ui.metric_card(
                    "Memory",
                    (
                        f"{host.memory_percent:.1f}%"
                        if host is not None and host.memory_percent is not None
                        else "—"
                    ),
                    sub="in-process sample",
                )
            with c4:
                ui.metric_card(
                    "Processes observed",
                    str(len(collector.last_process_measurements)),
                    sub="in the last collection cycle",
                )
            if health.last_error:
                st.error(f"Collection error: {health.last_error}")
            st.caption(
                "Capabilities reported by this platform collector: "
                + ", ".join(
                    f"{name}={'yes' if value else 'no'}"
                    for name, value in sorted(health.capabilities.items())
                )
            )

        readiness = live.get("readiness", {})
        if readiness:
            with st.container(border=True):
                ui.panel_header(
                    "Baseline readiness",
                    "Evidence-based readiness, per stored metric identity",
                )
                rows = [
                    {
                        "Metric": f"{key.service} / {key.metric_name}",
                        "Readiness": str(getattr(state, "value", state)),
                    }
                    for key, state in sorted(readiness.items())
                ]
                st.dataframe(rows, width="stretch", hide_index=True)
                st.caption(
                    "READY = enough samples for a baseline. "
                    "INSUFFICIENT_DATA = collecting, not enough yet. "
                    "UNAVAILABLE = no observations. STALE = baseline "
                    "predates the current period."
                )

        comparison = live.get("comparison")
        comparison_error = live.get("comparison_error")
        with st.container(border=True):
            ui.panel_header(
                "Historical comparison — today vs prior day (UTC calendar days)",
                "Real observation windows compared through the existing "
                "intelligence engine; missing history is reported, not faked. "
                "The comparison windows are UTC calendar days; individual "
                f"timestamps are shown in {ctx.timezone_name} local time.",
            )
            if comparison_error:
                st.error(f"Comparison unavailable: {comparison_error}")
            elif comparison is None:
                ui.empty_state(
                    "No host selected",
                    "Start local observation to enable today-vs-prior-day "
                    "comparison.",
                )
            elif not comparison.sufficient:
                st.info(
                    "Cross-day comparison unavailable: "
                    + "; ".join(comparison.reasons)
                )
            else:
                anomalous = [d for d in comparison.deviations if d.is_anomalous]
                c1, c2, c3 = st.columns(3)
                c1.metric_card(
                    "Baselines ready",
                    str(len(comparison.metrics_compared)),
                    sub="of compared metrics",
                )
                c2.metric_card(
                    "Anomalous deviations",
                    str(len(anomalous)),
                    sub="current window vs baseline",
                )
                c3.metric_card(
                    "Change points",
                    str(len(comparison.change_points)),
                    sub="sustained anomalous runs",
                )
