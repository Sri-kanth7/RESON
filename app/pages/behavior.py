"""Behavior Analytics page: baselines vs observed from real analysis."""

import altair as alt
import pandas as pd
import streamlit as st

from app.components import ui
from app.services import backend as svc


def _observed_frame(points) -> pd.DataFrame:
    return pd.DataFrame(
        {"observed": [v for _, v in points]},
        index=pd.DatetimeIndex([t for t, _ in points], name="time"),
    )


def _baseline_chart(frame: pd.DataFrame, stats):
    band = (
        alt.Chart(frame)
        .mark_area(color="#00E5FF", opacity=0.16)
        .encode(
            x=alt.X("time:T", title="Time (UTC)"),
            y=alt.Y("p25:Q", title="Observed value"),
            y2="p75:Q",
        )
    )
    mean = (
        alt.Chart(frame)
        .mark_line(color="#94A3B8", strokeDash=[5, 4], strokeWidth=1.5)
        .encode(x="time:T", y="mean:Q")
    )
    observed = (
        alt.Chart(frame)
        .mark_line(color="#00E5FF", strokeWidth=2)
        .encode(x="time:T", y="observed:Q")
    )
    return (band + mean + observed).properties(height=280)


def _zscore_chart(deviations):
    rows = [
        {"time": d.timestamp, "z": d.z_score, "status": str(getattr(d.status, "value", d.status))}
        for d in deviations
        if d.z_score is not None
    ]
    if not rows:
        return None
    frame = pd.DataFrame(rows)
    return (
        alt.Chart(frame)
        .mark_bar(size=6)
        .encode(
            x=alt.X("time:T", title="Time (UTC)"),
            y=alt.Y("z:Q", title="Z-score (sigma)"),
            color=alt.condition(
                alt.datum.status == "anomalous",
                alt.value("#EF4444"),
                alt.value("#475569"),
            ),
            tooltip=[
                alt.Tooltip("time:T", format="%H:%M:%S"),
                alt.Tooltip("z:Q", title="z-score", format=".2f"),
                alt.Tooltip("status:N"),
            ],
        )
        .properties(height=180)
    )


def render(storage, demo) -> None:
    ui.page_header(
        "Behavior analytics &amp; deviation profiling",
        "System Behavior Drift &amp; Signature Analytics",
        "Historical baselines compared against observed telemetry using the "
        "recorded analysis. Shift scores are bounded evidence magnitudes — "
        "not probabilities.",
    )

    keys = svc.metric_keys(storage)
    if not keys:
        ui.empty_state(
            "No telemetry stored",
            "Run **Run Local Demonstration Analysis** to generate simulated "
            "telemetry and compute baselines.",
        )
        return

    options = {
        f"{k.service} / {k.environment} / {k.metric_name}": k for k in keys
    }
    selected = st.selectbox("Metric", list(options.keys()))
    key = options[selected]

    if demo is None or key not in demo.baselines:
        with st.container(border=True):
            ui.panel_header(
                "No baseline for this metric in the current session",
                "Baselines come from the leading normal window of the analysis.",
            )
            st.markdown(
                "Run **Run Local Demonstration Analysis** to populate this "
                "view. Stored observations are shown below without a baseline."
            )
            points = svc.series_for_key(storage, key)
            if points:
                st.line_chart(
                    _observed_frame(points), width="stretch"
                )
        return

    baseline = demo.baselines[key]
    stats = baseline.statistics
    points = svc.series_for_key(storage, key)
    key_deviations = [d for d in demo.deviations if d.key == key]
    anomalous = sum(1 for d in key_deviations if d.is_anomalous)
    key_changes = [
        cp for cp in demo.change_points if key in cp.affected_metric_keys
    ]

    z_values = [d.z_score for d in key_deviations if d.z_score is not None]
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        ui.metric_card(
            "Baseline mean",
            f"{stats.mean:.3f}" if stats.mean is not None else "—",
            sub=f"n={stats.count}",
            badge="KDE",
        )
    with c2:
        ui.metric_card(
            "Baseline std",
            f"{stats.std:.3f}" if stats.std is not None else "—",
            sub=(
                f"min/max {stats.min:.2f}/{stats.max:.2f}"
                if stats.min is not None and stats.max is not None
                else ""
            ),
            spark=[stats.mean or 0.0, stats.p50 or 0.0, stats.p75 or 0.0],
        )
    with c3:
        ui.metric_card(
            "Anomalous obs.",
            f"{anomalous} / {len(key_deviations)}",
            sub="in analysis window",
            accent="reson-accent-critical" if anomalous else None,
            spark=z_values[:20] if z_values else None,
            spark_color="#EF4444",
        )
    with c4:
        ui.metric_card(
            "Change points",
            str(len(key_changes)),
            sub="sustained anomalous runs",
            spark=[stats.mean or 0.0, stats.max or 0.0],
        )

    with st.container(border=True):
        ui.panel_header(
            "Observed vs historical baseline",
            "Shaded band is the baseline inter-quartile range (p25–p75); "
            "dashed line is the baseline mean",
        )
        frame = pd.DataFrame(
            {
                "observed": [v for _, v in points],
                "mean": [stats.mean if stats.mean is not None else float("nan")]
                * len(points),
                "p25": [stats.p25 if stats.p25 is not None else float("nan")]
                * len(points),
                "p75": [stats.p75 if stats.p75 is not None else float("nan")]
                * len(points),
            },
            index=pd.DatetimeIndex([t for t, _ in points], name="time"),
        ).reset_index()
        st.altair_chart(
            ui.darken(_baseline_chart(frame, stats)), width="stretch"
        )
        st.caption(
            f"Baseline window: "
            f"{baseline.start_time.strftime('%Y-%m-%d %H:%M UTC')} → "
            f"{baseline.end_time.strftime('%Y-%m-%d %H:%M UTC')} · "
            f"{baseline.explanation}"
        )

    z_chart = _zscore_chart(key_deviations)
    if z_chart is not None:
        with st.container(border=True):
            ui.panel_header(
                "Residual deviation z-score stream",
                "Red bars are observations classified anomalous by the engine",
            )
            st.altair_chart(ui.darken(z_chart), width="stretch")

    with st.container(border=True):
        ui.panel_header("Detected deviations", "Observed vs baseline per sample")
        if not key_deviations:
            st.info("No deviation records for this metric in the analysis window.")
        else:
            rows = [
                {
                    "Time (UTC)": d.timestamp.strftime("%H:%M:%S"),
                    "Value": round(d.value, 3),
                    "Status": str(getattr(d.status, "value", d.status)),
                    "z-score": round(d.z_score, 3) if d.z_score is not None else "—",
                    "rel. dev.": (
                        round(d.relative_deviation, 3)
                        if d.relative_deviation is not None
                        else "—"
                    ),
                }
                for d in sorted(key_deviations, key=lambda d: d.timestamp)
            ]
            st.dataframe(rows, width="stretch", hide_index=True)

    if key_changes:
        with st.container(border=True):
            ui.panel_header("Change-point evidence", "Sustained anomalous runs")
            for cp in key_changes:
                st.markdown(
                    f"<span class='reson-mono' style='font-size:0.75rem'>"
                    f"{cp.start_time.strftime('%H:%M:%S')} → "
                    f"{cp.end_time.strftime('%H:%M:%S UTC')} · "
                    f"{cp.consecutive_batches} batches</span><br>{cp.explanation}",
                    unsafe_allow_html=True,
                )

    service_result = next(
        (
            r
            for r in demo.analysis.service_results
            if r.service == key.service
        ),
        None,
    )
    if service_result is not None:
        with st.container(border=True):
            ui.panel_header(
                f"Behavior fingerprint — {service_result.service}",
                f"state `{service_result.fingerprint.behavior_state}` · "
                f"shift `{service_result.shift.score:.3f}`",
            )
            st.markdown(service_result.shift.explanation)
            if service_result.shift.contributions:
                rows = [
                    {
                        "Metric": c.key.metric_name,
                        "Anomaly rate": round(c.anomaly_rate, 3),
                        "Peak magnitude": round(c.peak_magnitude, 3),
                        "Contribution": round(c.contribution, 3),
                    }
                    for c in service_result.shift.contributions
                ]
                st.dataframe(rows, width="stretch", hide_index=True)
            fp = service_result.fingerprint
            st.markdown(
                f"<span class='reson-sub'>log summary: {dict(fp.log_summary)} · "
                f"event summary: {dict(fp.event_summary)} · deployments: "
                f"{fp.deployment_count} · change points: {fp.change_point_count}"
                "</span>",
                unsafe_allow_html=True,
            )
