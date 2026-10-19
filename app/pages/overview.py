"""Operations Overview page: real counts, real analysis, honest empty states."""

import altair as alt
import pandas as pd
import streamlit as st

from app import theme as th
from app.components import ui
from app.services import backend as svc
from src.data.windows_collector import CollectorState


def _severity_counts(incidents) -> dict[str, int]:
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for incident in incidents:
        value = str(getattr(incident.severity, "value", incident.severity))
        if value in counts:
            counts[value] += 1
    return counts


def _activity_frame(incidents) -> pd.DataFrame:
    """Bucket incidents by start hour from real stored records."""
    if not incidents:
        return pd.DataFrame(columns=["hour", "incidents"])
    frame = pd.DataFrame(
        {"hour": [i.start_time for i in incidents]}
    ).assign(hour=lambda d: d["hour"].dt.floor("h"))
    grouped = frame.groupby("hour").size().reset_index(name="incidents")
    return grouped.sort_values("hour")


def _service_rows(storage, demo):
    """Derive per-service telemetry rows from stored metrics (real data)."""
    metrics = storage.get_metrics()
    by_service: dict[str, int] = {}
    for metric in metrics:
        by_service[metric.service] = by_service.get(metric.service, 0) + 1
    changed = set(demo.analysis.changed_services) if (
        demo is not None and demo.analysis is not None
    ) else set()
    return sorted(by_service.items()), changed


def render(storage, demo, backend_name: str, live: dict | None = None) -> None:
    ui.page_header(
        "Fleet observability / incident overview",
        "System Operations &amp; Incident Pulse",
        "Deterministic incident intelligence over stored telemetry. Every "
        "figure below comes from the selected backend or the current session "
        "analysis — never invented.",
    )

    live = live or {}
    collector = live.get("collector")

    # ------------------------------------------------------------------
    # Live observations (real Windows telemetry)
    # ------------------------------------------------------------------
    if collector is not None:
        with st.container(border=True):
            ui.panel_header(
                "Live observations — real Windows telemetry",
                f"Source: windows_local · host: {collector.host_id} · "
                f"ingesting into the selected ({backend_name}) backend",
            )
            host = collector.last_host_measurement
            state_tone = (
                "chip-critical"
                if collector.state == CollectorState.ERROR
                else "chip-low"
            )
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                ui.metric_card(
                    "Collector",
                    collector.state.value.upper(),
                    trend_dir="ok" if collector.state == CollectorState.RUNNING else "down",
                )
                st.markdown(
                    f"<span class='reson-sub'>{th.chip(collector.state.value.upper(), state_tone)}</span>",
                    unsafe_allow_html=True,
                )
            with c2:
                ui.metric_card(
                    "CPU",
                    f"{host.cpu_percent:.1f}%" if host and host.cpu_percent is not None else "—",
                    sub="host utilization, percent",
                )
            with c3:
                ui.metric_card(
                    "Memory",
                    f"{host.memory_percent:.1f}%" if host and host.memory_percent is not None else "—",
                    sub="physical memory utilization, percent",
                )
            with c4:
                last = (
                    host.timestamp.strftime("%H:%M:%S UTC")
                    if host is not None
                    else "never"
                )
                ui.metric_card(
                    "Last sample",
                    last,
                    sub="UTC observation timestamp",
                )
            if collector.last_error:
                st.error(f"Collection error: {collector.last_error}")
            procs = collector.last_process_measurements
            if procs:
                st.markdown(
                    f"<span class='reson-sub'>"
                    f"{len(procs)} process(es) observed in the last cycle"
                    "</span>",
                    unsafe_allow_html=True,
                )

    counts = svc.storage_counts(storage)
    incidents = svc.ordered_incidents(storage)
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

    # KPI bar ------------------------------------------------------------
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        ui.metric_card(
            "Active incidents",
            str(len(active)),
            sub=f"{critical_high} critical/high · {len(incidents)} total stored",
            badge=f"{severities['critical']} P1 · {severities['high']} P2",
            trend_dir="up" if active else "ok",
            accent="reson-accent-critical" if critical_high else None,
        )
    with col2:
        if demo is not None and demo.analysis is not None:
            score = demo.analysis.overall_score
            changed = ", ".join(demo.analysis.changed_services) or "none"
            sparks = [
                r.shift.score for r in demo.analysis.service_results
            ]
            ui.metric_card(
                "Behavior shift",
                f"{score:.3f}",
                sub=f"changed: {changed}",
                badge="ELEVATED" if score >= 0.5 else "STEADY",
                spark=sparks or None,
            )
        else:
            ui.metric_card(
                "Behavior shift",
                "—",
                sub="analysis not yet run",
                badge="NO DATA",
            )
    with col3:
        ui.metric_card(
            "Telemetry volume",
            f"{total:,}",
            sub=(
                f"{counts['metrics']:,} metrics · {counts['logs']:,} logs · "
                f"{counts['events']:,} events · {counts['deployments']:,} deployments"
            ),
            badge=f"{counts['services']} services",
        )
    with col4:
        ui.metric_card(
            "Data source",
            backend_name,
            sub=f"{counts['services']} services observed"
            + (" · SIMULATED telemetry" if demo is not None else ""),
            badge="SIMULATED" if demo is not None else "STORED",
        )

    if total == 0:
        with st.container(border=True):
            ui.panel_header(
                "First run — storage is empty",
                "No telemetry is stored in the selected backend.",
            )
            st.markdown(
                "Use **Run Local Demonstration Analysis** in the sidebar to "
                "generate deterministic simulated telemetry and execute the "
                "real ingestion and intelligence pipeline. Simulated telemetry "
                "is always labeled."
            )
        return

    # Incident activity + severity distribution --------------------------
    left, right = st.columns([8, 4])
    with left:
        with st.container(border=True):
            ui.panel_header(
                "Incident activity over time",
                "Stored incidents bucketed by start hour (UTC)",
            )
            frame = _activity_frame(incidents)
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
                            title="Start hour (UTC)",
                            axis=alt.Axis(format="%m-%d %H:%M"),
                        ),
                        y=alt.Y(
                            "incidents:Q",
                            title="Incidents",
                            axis=alt.Axis(format="d"),
                        ),
                        tooltip=[
                            alt.Tooltip("hour:T", title="Hour", format="%Y-%m-%d %H:%M"),
                            alt.Tooltip("incidents:Q", title="Incidents"),
                        ],
                    )
                    .properties(height=220)
                )
                st.altair_chart(
                    ui.darken(chart), width="stretch"
                )
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
                st.altair_chart(
                    ui.darken(chart), width="stretch"
                )

    # Registry + service mesh + shift stream -----------------------------
    left, right = st.columns([8, 4])
    with left:
        with st.container(border=True):
            ui.panel_header(
                "Correlated Incident Registry",
                f"{len(incidents)} logged · {len(active)} active",
            )
            if not incidents:
                if demo is not None:
                    st.markdown(
                        "Analysis complete — **no incidents detected** in this "
                        "window. Zero is a valid result: no trigger evidence met "
                        "the correlation policy."
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
                            f"{incident.start_time.strftime('%Y-%m-%d %H:%M:%S')} UTC"
                            f" · {', '.join(incident.affected_services) or '—'}"
                        ),
                    )
    with right:
        with st.container(border=True):
            ui.panel_header("Service mesh health", "From stored telemetry")
            rows, changed = _service_rows(storage, demo)
            if not rows:
                st.markdown("No services observed.")
            else:
                for service, metric_count in rows:
                    degraded = service in changed
                    ui.mesh_row(
                        service,
                        f"{metric_count:,} observations",
                        "DEGRADED" if degraded else "NOMINAL",
                        "chip-critical" if degraded else "chip-low",
                    )

    # Session evidence ---------------------------------------------------
    if demo is not None and demo.incident_result is not None:
        result = demo.incident_result
        anomalous = [d for d in demo.deviations if d.is_anomalous]
        with st.container(border=True):
            ui.panel_header(
                "Behavior shift stream",
                "Real deviations and change points from the session analysis",
            )
            c1, c2, c3, c4 = st.columns(4)
            c1.markdown(
                f'<div class="reson-sub">Anomalous deviations</div>'
                f'<div class="reson-mono">{len(anomalous)}</div>',
                unsafe_allow_html=True,
            )
            c2.markdown(
                f'<div class="reson-sub">Change points</div>'
                f'<div class="reson-mono">{len(demo.change_points)}</div>',
                unsafe_allow_html=True,
            )
            c3.markdown(
                f'<div class="reson-sub">Accepted correlations</div>'
                f'<div class="reson-mono">{len(result.correlation_evidence)}</div>',
                unsafe_allow_html=True,
            )
            c4.markdown(
                f'<div class="reson-sub">Pairs evaluated</div>'
                f'<div class="reson-mono">{len(result.correlation_candidates)}</div>',
                unsafe_allow_html=True,
            )
            if anomalous:
                top = sorted(
                    anomalous,
                    key=lambda d: abs(d.z_score or 0.0),
                    reverse=True,
                )[:5]
                for deviation in top:
                    z = deviation.z_score
                    tone = "chip-critical" if abs(z or 0) >= 4 else "chip-high"
                    ui.stream_item(
                        deviation.timestamp.strftime("%H:%M:%S"),
                        deviation.key.service,
                        f"<span class='reson-mono'>{deviation.key.metric_name}</span> "
                        f"deviated to {deviation.value:.3f} "
                        f"(z={z:.2f})" if z is not None else
                        f"<span class='reson-mono'>{deviation.key.metric_name}</span> "
                        f"deviated to {deviation.value:.3f}",
                        tone,
                    )

    # ------------------------------------------------------------------
    # Baseline readiness and historical comparison (live observations)
    # ------------------------------------------------------------------
    if live:
        host_id = collector.host_id if collector else None
        if host_id:
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
                            "Readiness": str(
                                getattr(state, "value", state)
                            ),
                            "Historical samples": live.get(
                                "historical_sample_counts", {}
                            ).get(key, 0),
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
                "Historical comparison — today vs prior day (UTC)",
                "Real observation windows compared through the existing "
                "intelligence engine; missing history is reported, not faked.",
            )
            if comparison_error:
                st.error(f"Comparison unavailable: {comparison_error}")
            elif comparison is None:
                ui.empty_state(
                    "No host selected",
                    "Start real Windows observation to enable "
                    "today-vs-prior-day comparison.",
                )
            elif not comparison.sufficient:
                st.info(
                    "Cross-day comparison unavailable: "
                    + "; ".join(comparison.reasons)
                )
                if comparison.incompatible_units:
                    st.markdown(
                        "Units disagree (not compared): "
                        + ", ".join(comparison.incompatible_units)
                    )
                if comparison.missing_metrics:
                    st.markdown(
                        "Requested metrics with no data: "
                        + ", ".join(comparison.missing_metrics)
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
                st.markdown(
                    "<span class='reson-sub'>"
                    f"Historical window: "
                    f"{comparison.historical_window[0].strftime('%Y-%m-%d %H:%M UTC')} → "
                    f"{comparison.historical_window[1].strftime('%Y-%m-%d %H:%M UTC')} · "
                    f"Current window: "
                    f"{comparison.current_window[0].strftime('%Y-%m-%d %H:%M UTC')} → "
                    f"{comparison.current_window[1].strftime('%Y-%m-%d %H:%M UTC')}"
                    "</span>",
                    unsafe_allow_html=True,
                )
                if comparison.reasons:
                    st.caption(" | ".join(comparison.reasons))
                if anomalous:
                    with st.container(border=True):
                        ui.panel_header(
                            "Deviations vs prior-day baseline",
                            "Correlated evidence, not a confirmed cause.",
                        )
                        rows = [
                            {
                                "Time (UTC)": d.timestamp.strftime("%H:%M:%S"),
                                "Metric": d.key.metric_name,
                                "Value": round(d.value, 3),
                                "z-score": (
                                    round(d.z_score, 3)
                                    if d.z_score is not None
                                    else "—"
                                ),
                            }
                            for d in sorted(
                                anomalous, key=lambda d: d.timestamp
                            )[:10]
                        ]
                        st.dataframe(rows, width="stretch", hide_index=True)
