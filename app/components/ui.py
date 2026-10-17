"""Shared rendering components for the RESON Streamlit application.

Pure presentation helpers only: they format values the pages have already
obtained from real backend services. No component reads storage, runs an
analysis, or invents a figure.
"""

from __future__ import annotations

from typing import Iterable, Sequence

import streamlit as st

from app import theme as th

# Altair axis/grid palette drawn from the Obsidian Telemetry spec.
_AXIS = "#3B494C"
_GRID = "#1A2438"
_LABEL = "#94A3B8"
_TEXT = "#F8FAFC"


def page_header(kicker: str, title: str, description: str) -> None:
    """Render the standard page kicker, title and description block."""
    st.markdown(f'<div class="reson-kicker">{kicker}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="reson-title">{title}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="reson-desc">{description}</div>', unsafe_allow_html=True)


def topbar(
    environment: str,
    data_source: str,
    simulated: bool,
    reachable: bool,
) -> None:
    """Render the shared top context bar (brand + environment + status)."""
    if not reachable:
        live = '<span class="reson-live off">BACKEND UNREACHABLE</span>'
    elif simulated:
        live = '<span class="reson-live sim">SIMULATED TELEMETRY</span>'
    else:
        live = '<span class="reson-live">LIVE DATA</span>'
    st.markdown(
        '<div class="reson-topbar">'
        '<div class="reson-topbar-left">'
        '<span class="reson-brand"><span class="reson-mark">◉</span> RESON</span>'
        '<span class="reson-env">env: ' + environment + "</span>"
        '<span class="reson-env">source: ' + data_source + "</span>"
        "</div>"
        '<div class="reson-topbar-right">' + live + "</div>"
        "</div>",
        unsafe_allow_html=True,
    )


def panel_header(title: str, subtitle: str | None = None) -> None:
    """Render a panel title with an optional technical subtitle."""
    st.markdown(
        f'<div class="reson-panel-title">{title}</div>', unsafe_allow_html=True
    )
    if subtitle:
        st.markdown(
            f'<div class="reson-panel-sub">{subtitle}</div>',
            unsafe_allow_html=True,
        )


def metric_card(
    label: str,
    value: str,
    sub: str | None = None,
    badge: str | None = None,
    trend: str | None = None,
    trend_dir: str | None = None,
    spark: Sequence[float] | None = None,
    spark_color: str = "#00E5FF",
    accent: str | None = None,
) -> None:
    """Render one telemetry metric card.

    ``spark`` must be a real numeric sequence from the backend; a flat or
    absent sequence renders the value without a fabricated trend line.
    """
    badge_html = (
        f'<span class="reson-badge">{badge}</span>' if badge else ""
    )
    trend_html = ""
    if trend:
        css = {"up": "up", "ok": "ok", "down": "ok"}.get(trend_dir or "", "flat")
        trend_html = f'<span class="reson-trend {css}">{trend}</span>'
    spark_html = ""
    if spark is not None:
        spark_html = th.sparkline(spark, color=spark_color)
    sub_html = f'<div class="reson-sub">{sub}</div>' if sub else ""
    surround = f"reson-card {accent}" if accent else "reson-card"
    st.markdown(
        f'<div class="{surround}">'
        '<div class="reson-card-head">'
        f'<div class="reson-kicker">{label}</div>{badge_html}'
        "</div>"
        '<div style="display:flex;align-items:baseline;gap:0.5rem">'
        f'<div class="reson-value">{value}</div>{trend_html}</div>'
        f"{sub_html}{spark_html}"
        "</div>",
        unsafe_allow_html=True,
    )


def registry_card(
    severity: object,
    identifier: str,
    title: str,
    status: object,
    meta: str,
) -> None:
    """Render one severity-accented incident registry entry."""
    st.markdown(
        f'<div class="reson-card {th.accent_class(severity)}">'
        f"{th.severity_chip(severity)} {th.status_chip(status)} "
        f"<span class='reson-mono' style='font-size:0.75rem;color:#94A3B8'>"
        f"{identifier}</span>"
        f'<div style="font-weight:600;margin-top:0.3rem">{title}</div>'
        f'<div class="reson-sub">{meta}</div>'
        "</div>",
        unsafe_allow_html=True,
    )


def mesh_row(name: str, detail: str, status: str, tone: str) -> None:
    """Render one service-health row for the mesh health panel."""
    st.markdown(
        '<div class="reson-mesh-row">'
        f'<div><span class="reson-mesh-name">{name}</span><br>'
        f'<span class="reson-mesh-meta">{detail}</span></div>'
        f"<div>{th.chip(status, tone)}</div>"
        "</div>",
        unsafe_allow_html=True,
    )


def stream_item(timestamp: str, source: str, message: str, tone: str) -> None:
    """Render one behavior-shift stream entry."""
    st.markdown(
        '<div class="reson-stream-item">'
        f'<span class="reson-mono" style="font-size:0.7rem;color:#475569">'
        f"{timestamp}</span> "
        f'{th.chip(source, tone)}'
        f'<div style="font-size:0.8rem;margin-top:0.2rem">{message}</div>'
        "</div>",
        unsafe_allow_html=True,
    )


def flow(nodes: Sequence[tuple[str, str, str, str]]) -> None:
    """Render a horizontal evidence node flow.

    Each node is ``(title, detail, mono_id, css_class)``. Used to show the
    real evidence chain (incident → signal → source) without inventing edges.
    """
    parts = ['<div class="reson-flow">']
    for index, (title, detail, mono_id, css) in enumerate(nodes):
        if index:
            parts.append('<div class="reson-flow-arrow">→</div>')
        parts.append(
            f'<div class="reson-flow-node {css}">'
            f"<div style='font-size:0.8rem;font-weight:600'>{title}</div>"
            f"<div class='reson-mono' style='font-size:0.7rem;color:#94A3B8'>"
            f"{mono_id}</div>"
            f"<div style='font-size:0.72rem;color:#94A3B8'>{detail}</div>"
            "</div>"
        )
    parts.append("</div>")
    st.markdown("".join(parts), unsafe_allow_html=True)


def darken(chart):
    """Apply the Obsidian Telemetry palette to an Altair chart."""
    return (
        chart.configure_view(stroke=None)
        .configure_axis(
            grid=True,
            gridColor=_GRID,
            domainColor=_AXIS,
            tickColor=_AXIS,
            labelColor=_LABEL,
            titleColor=_LABEL,
            labelFont="JetBrains Mono, monospace",
            titleFont="JetBrains Mono, monospace",
        )
        .configure_legend(
            labelColor=_LABEL,
            titleColor=_LABEL,
            labelFont="JetBrains Mono, monospace",
        )
        .configure_title(color=_TEXT)
    )


def empty_state(title: str, body: str, tone: str = "info") -> None:
    """Render a consistent empty / unavailable panel body."""
    with st.container(border=True):
        panel_header(title)
        st.markdown(body)


def as_points(series: Iterable[float]) -> list[float]:
    """Materialize a numeric series for sparkline rendering."""
    return [float(value) for value in series]
