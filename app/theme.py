"""Obsidian Telemetry visual system for the RESON Streamlit application.

Implements the Google Stitch design specification
(``design/stitch_reson_incident_intelligence_platform``) as Streamlit CSS
plus small formatting helpers. No backend logic lives here.
"""

OBSIDIAN_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap');

/* Canvas */
.stApp {
    background-color: #0B0F17;
    color: #F8FAFC;
    font-family: 'Inter', -apple-system, 'Segoe UI', sans-serif;
}

/* Sidebar */
section[data-testid="stSidebar"] {
    background-color: #111827;
    border-right: 1px solid #22324D;
}
section[data-testid="stSidebar"] .stRadio label,
section[data-testid="stSidebar"] .stSelectbox label {
    color: #94A3B8 !important;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.6875rem;
    letter-spacing: 0.02em;
    text-transform: uppercase;
}

/* Panels */
.reson-panel {
    background-color: #111827;
    border: 1px solid #22324D;
    border-radius: 8px;
    padding: 0.75rem 1rem;
    margin-bottom: 1rem;
}
.reson-card {
    background-color: #161F30;
    border: 1px solid #22324D;
    border-radius: 8px;
    padding: 0.75rem;
}
.reson-kicker {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.6875rem;
    letter-spacing: 0.02em;
    text-transform: uppercase;
    color: #94A3B8;
    margin-bottom: 0.25rem;
}
.reson-value {
    font-family: 'JetBrains Mono', 'Consolas', monospace;
    font-variant-numeric: tabular-nums;
    font-size: 1.5rem;
    font-weight: 600;
    color: #F8FAFC;
    line-height: 2rem;
}
.reson-sub {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.75rem;
    color: #94A3B8;
}
.reson-title {
    font-size: 1.5rem;
    font-weight: 600;
    letter-spacing: -0.02em;
    color: #F8FAFC;
    margin-bottom: 0.25rem;
}
.reson-desc {
    font-size: 0.875rem;
    color: #94A3B8;
    margin-bottom: 1rem;
}
.reson-mono {
    font-family: 'JetBrains Mono', 'Consolas', monospace;
    font-variant-numeric: tabular-nums;
}

/* Severity / status chips */
.reson-chip {
    display: inline-block;
    height: 20px;
    line-height: 18px;
    padding: 0 6px;
    border-radius: 4px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.6875rem;
    letter-spacing: 0.02em;
    border: 1px solid;
    white-space: nowrap;
}
.chip-critical { background: rgba(239,68,68,0.12); color: #EF4444; border-color: rgba(239,68,68,0.35); }
.chip-high { background: rgba(245,158,11,0.12); color: #F59E0B; border-color: rgba(245,158,11,0.35); }
.chip-medium { background: rgba(245,158,11,0.12); color: #F59E0B; border-color: rgba(245,158,11,0.35); }
.chip-low { background: rgba(16,185,129,0.12); color: #10B981; border-color: rgba(16,185,129,0.35); }
.chip-info { background: rgba(0,229,255,0.12); color: #00E5FF; border-color: rgba(0,229,255,0.35); }
.chip-corr { background: rgba(139,92,246,0.12); color: #8B5CF6; border-color: rgba(139,92,246,0.35); }
.chip-neutral { background: #161F30; color: #94A3B8; border-color: #22324D; }

/* Evidence timeline rail */
.reson-timeline {
    border-left: 1px solid #22324D;
    margin-left: 6px;
    padding-left: 1rem;
}
.reson-timeline-item {
    position: relative;
    margin-bottom: 0.75rem;
}
.reson-timeline-item::before {
    content: '';
    position: absolute;
    left: calc(-1rem - 4px);
    top: 4px;
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: #00E5FF;
}
.reson-timeline-item.corr::before { background: #8B5CF6; }
.reson-timeline-item.temp::before { background: #94A3B8; }

/* Tables */
.stDataFrame { border: 1px solid #22324D; border-radius: 8px; }

/* Primary buttons echo Intelligence Primary */
.stButton button[kind="primary"] {
    background-color: #00E5FF;
    color: #0B0F17;
    border: none;
    border-radius: 4px;
    font-weight: 600;
}
.stButton button[kind="secondary"] {
    background-color: #161F30;
    color: #F8FAFC;
    border: 1px solid #22324D;
    border-radius: 4px;
}

/* Factor bars */
.reson-factor { margin-bottom: 0.5rem; }
.reson-factor-name {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.75rem;
    color: #94A3B8;
    display: flex;
    justify-content: space-between;
}
.reson-factor-track {
    height: 6px;
    border-radius: 2px;
    background: #0B0F17;
    border: 1px solid #22324D;
    margin-top: 2px;
}
.reson-factor-fill {
    height: 100%;
    border-radius: 2px;
    background: #00E5FF;
}

/* Top context bar */
.reson-topbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    background: #0B0F17;
    border-bottom: 1px solid #22324D;
    padding: 0.4rem 0 0.6rem 0;
    margin-bottom: 1rem;
    flex-wrap: wrap;
}
.reson-topbar-left, .reson-topbar-right {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    flex-wrap: wrap;
}
.reson-brand {
    font-size: 0.95rem;
    font-weight: 700;
    letter-spacing: -0.02em;
    color: #F8FAFC;
}
.reson-brand .reson-mark { color: #00E5FF; }
.reson-env {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.72rem;
    color: #94A3B8;
    border: 1px solid #22324D;
    border-radius: 4px;
    padding: 2px 8px;
    background: #161F30;
}
.reson-live {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.72rem;
    color: #10B981;
}
.reson-live::before {
    content: '';
    display: inline-block;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: #10B981;
    margin-right: 5px;
    vertical-align: middle;
}
.reson-live.sim { color: #F59E0B; }
.reson-live.sim::before { background: #F59E0B; }
.reson-live.off { color: #EF4444; }
.reson-live.off::before { background: #EF4444; }

/* Panel headers */
.reson-panel-title {
    font-size: 0.95rem;
    font-weight: 600;
    letter-spacing: -0.01em;
    color: #F8FAFC;
}
.reson-panel-sub {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.72rem;
    color: #94A3B8;
    margin-bottom: 0.35rem;
}

/* Bordered Streamlit containers rendered as Obsidian telemetry panels */
div[data-testid="stVerticalBlockBorderWrapper"] {
    background: #111827;
    border: 1px solid #22324D;
    border-radius: 8px;
}

/* Metric card internals */
.reson-card-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
}
.reson-badge {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.625rem;
    letter-spacing: 0.02em;
    padding: 1px 6px;
    border-radius: 4px;
    border: 1px solid #22324D;
    color: #94A3B8;
    white-space: nowrap;
}
.reson-trend { font-family: 'JetBrains Mono', monospace; font-size: 0.72rem; }
.reson-trend.up { color: #EF4444; }
.reson-trend.ok { color: #10B981; }
.reson-trend.flat { color: #94A3B8; }
.reson-spark { display: block; margin-top: 0.35rem; }

/* Severity-accented registry rows / cards */
.reson-accent-critical { border-left: 3px solid #EF4444; }
.reson-accent-high { border-left: 3px solid #F59E0B; }
.reson-accent-medium { border-left: 3px solid #F59E0B; }
.reson-accent-low { border-left: 3px solid #10B981; }

/* Service mesh health rows */
.reson-mesh-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
    padding: 0.4rem 0;
    border-bottom: 1px solid #1a2438;
    font-size: 0.8rem;
}
.reson-mesh-row:last-child { border-bottom: none; }
.reson-mesh-name { font-family: 'JetBrains Mono', monospace; color: #F8FAFC; }
.reson-mesh-meta { font-family: 'JetBrains Mono', monospace; font-size: 0.7rem; color: #94A3B8; text-align: right; }

/* Behavior shift stream */
.reson-stream-item { padding: 0.4rem 0; border-bottom: 1px solid #1a2438; }
.reson-stream-item:last-child { border-bottom: none; }

/* Evidence node flow */
.reson-flow { display: flex; flex-wrap: wrap; align-items: stretch; gap: 0.4rem; }
.reson-flow-node {
    background: #161F30;
    border: 1px solid #22324D;
    border-radius: 6px;
    padding: 0.5rem 0.65rem;
    min-width: 150px;
    flex: 1 1 150px;
}
.reson-flow-node.root { border-color: #EF4444; }
.reson-flow-node.corr { border-color: #8B5CF6; }
.reson-flow-node.trigger { border-color: #00E5FF; }
.reson-flow-arrow { align-self: center; color: #475569; font-family: 'JetBrains Mono', monospace; }
</style>
"""

_SEVERITY_CHIP = {
    "critical": "chip-critical",
    "high": "chip-high",
    "medium": "chip-medium",
    "low": "chip-low",
}

_STATUS_CHIP = {
    "open": "chip-critical",
    "investigating": "chip-high",
    "resolved": "chip-low",
}


def chip(text: str, style: str) -> str:
    """Return an HTML chip span for the given text and chip style."""
    return f'<span class="reson-chip {style}">{text}</span>'


def severity_chip(severity: object) -> str:
    """Return a severity chip for an IncidentSeverity value or string."""
    value = getattr(severity, "value", severity)
    label = str(value).upper()
    return chip(label, _SEVERITY_CHIP.get(str(value).lower(), "chip-neutral"))


def status_chip(status: object) -> str:
    """Return a lifecycle-status chip for an IncidentStatus value or string."""
    value = getattr(status, "value", status)
    label = str(value).upper().replace("_", " ")
    return chip(label, _STATUS_CHIP.get(str(value).lower(), "chip-neutral"))


def short_id(identifier: object) -> str:
    """Return the first 8 characters of a UUID for compact display."""
    return str(identifier)[:8]


def factor_bar(name: str, value: float) -> str:
    """Return an HTML factor bar for a [0, 1] similarity/correlation factor."""
    clamped = max(0.0, min(1.0, float(value)))
    return (
        '<div class="reson-factor">'
        f'<div class="reson-factor-name"><span>{name}</span>'
        f"<span>{clamped:.3f}</span></div>"
        '<div class="reson-factor-track">'
        f'<div class="reson-factor-fill" style="width:{clamped * 100:.1f}%"></div>'
        "</div></div>"
    )


def accent_class(severity: object) -> str:
    """Return the severity accent CSS class for a card or row."""
    value = str(getattr(severity, "value", severity)).lower()
    return {
        "critical": "reson-accent-critical",
        "high": "reson-accent-high",
        "medium": "reson-accent-medium",
        "low": "reson-accent-low",
    }.get(value, "")


def sparkline(values, color: str = "#00E5FF", width: int = 132, height: int = 28) -> str:
    """Return an inline SVG sparkline for a short numeric sequence.

    Renders only the supplied values; a flat or empty series yields a
    monochrome baseline. Never invents points.
    """
    points = [float(v) for v in values]
    if not points:
        return ""
    if len(points) == 1:
        points = points * 2
    lo, hi = min(points), max(points)
    span = hi - lo
    step = width / (len(points) - 1)
    coords = []
    for index, value in enumerate(points):
        ratio = 0.5 if span == 0 else (value - lo) / span
        x = index * step
        y = height - ratio * height
        coords.append(f"{x:.1f},{y:.1f}")
    polyline = " ".join(coords)
    return (
        f'<svg class="reson-spark" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" preserveAspectRatio="none" '
        'role="img" aria-label="sparkline">'
        f'<polyline fill="none" stroke="{color}" stroke-width="1.5" '
        f'points="{polyline}" /></svg>'
    )
