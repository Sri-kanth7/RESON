"""System Events page: real collected events with source and severity.

Events are shown for the telemetry source selected by the current monitoring
mode. Only structured fields are displayed; RESON does not ingest every
operating-system log, and sensitive log content is never collected.
"""

from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from app.components import ui
from app.services import backend as svc

#: Severity ordering used for filtering and display.
SEVERITY_ORDER = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG")


def _severity_of(event) -> str:
    """Return the normalized severity of an event."""
    metadata = getattr(event, "metadata", {}) or {}
    level = metadata.get("level")
    if level:
        return str(level).upper()
    event_type = str(
        getattr(event.event_type, "value", event.event_type)
    ).upper()
    if event_type in ("SERVICE_FAILURE", "DATABASE_PRESSURE"):
        return "ERROR"
    if event_type == "NETWORK_DEGRADATION":
        return "WARNING"
    if event_type == "RESTART":
        return "WARNING"
    return "INFO"


def render(ctx) -> None:
    ui.page_header(
        "System events / collected log records",
        "System Event Feed",
        "Events actually collected from this machine, with their source, "
        "severity, and observation time. RESON reads an explicit allowlist "
        "of channels and event identifiers — never every log on the system.",
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
        window_label = st.selectbox(
            "Event window",
            ("Last hour", "Last 24 hours", "Last 7 days", "All history"),
            index=1,
        )
    with right:
        limit = st.number_input(
            "Max events",
            min_value=10,
            max_value=1000,
            value=200,
            step=10,
        )

    minutes = {
        "Last hour": 60,
        "Last 24 hours": 1440,
        "Last 7 days": 10080,
        "All history": None,
    }[window_label]
    since = (
        datetime.now(timezone.utc) - timedelta(minutes=minutes)
        if minutes is not None
        else None
    )

    events = svc.recent_events(storage, source=source, limit=int(limit))
    if since is not None:
        events = [event for event in events if event.timestamp >= since]

    severity_filter = st.multiselect(
        "Severity",
        SEVERITY_ORDER,
        default=[],
        help="Leave empty to show every severity.",
    )
    type_filter = st.multiselect(
        "Event type",
        sorted(
            {
                str(getattr(e.event_type, "value", e.event_type))
                for e in events
            }
        ),
        default=[],
        help="Leave empty to show every event type.",
    )

    filtered = []
    for event in events:
        if severity_filter and _severity_of(event) not in severity_filter:
            continue
        event_type = str(getattr(event.event_type, "value", event.event_type))
        if type_filter and event_type not in type_filter:
            continue
        filtered.append(event)

    with st.container(border=True):
        ui.panel_header(
            "Collected events",
            f"{len(filtered)} event(s) · {window_label} · "
            f"source: {source or 'all'}",
        )
        if not events:
            ui.empty_state(
                "No events collected",
                "No events are stored for the selected source and window. "
                "Event collection is opt-in: start the collector agent with "
                "`--events` on Windows to read the allowlisted system and "
                "application channels.",
            )
            return
        if not filtered:
            st.info("No events match the selected filters.")
            return

        frame = pd.DataFrame(
            [
                {
                    f"Time ({ctx.timezone_name})": ctx.format_timestamp(
                        event.timestamp
                    ),
                    "Severity": _severity_of(event),
                    "Type": str(
                        getattr(event.event_type, "value", event.event_type)
                    ),
                    "Service": event.service,
                    "Host": getattr(event, "host_id", None) or "—",
                    "Source": getattr(event, "source", None) or "unknown",
                    "Description": event.description[:300],
                }
                for event in filtered
            ]
        )
        st.dataframe(
            frame,
            width="stretch",
            hide_index=True,
            column_config={
                "Description": st.column_config.TextColumn(
                    "Description", width="large"
                )
            },
        )

    with st.container(border=True):
        ui.panel_header(
            "Event detail",
            "Structured fields only — no raw log payloads are stored",
        )
        options = {
            f"{ctx.format_timestamp(event.timestamp)} · "
            f"{_severity_of(event)} · {event.description[:60]}": event
            for event in filtered[:200]
        }
        if not options:
            st.info("No event selected.")
            return
        selected_label = st.selectbox("Event", list(options.keys()))
        event = options[selected_label]
        metadata = getattr(event, "metadata", {}) or {}
        st.markdown(
            f'<div class="reson-sub">'
            f"Service: {event.service} · Host: "
            f"{getattr(event, 'host_id', None) or '—'} · Source: "
            f"{getattr(event, 'source', None) or 'unknown'}</div>",
            unsafe_allow_html=True,
        )
        st.markdown(f"**{event.description}**")
        st.json(
            {
                "timestamp": event.timestamp.isoformat(),
                "event_type": str(
                    getattr(event.event_type, "value", event.event_type)
                ),
                "severity": _severity_of(event),
                "service": event.service,
                "host_id": getattr(event, "host_id", None),
                "source": getattr(event, "source", None),
                "metadata": metadata,
            }
        )
