"""Settings & Data Sources page: configuration, health, and capabilities.

This page reports what RESON is actually configured to do and what it can
actually collect on this machine. Nothing here changes a setting silently:
values that require a restart or an explicit action are labelled as such.
"""

import pandas as pd
import streamlit as st

from app.components import ui


def _provenance_capability(storage) -> dict:
    """Return the storage backend's telemetry-provenance capability.

    A backend that cannot store provenance must say so, so the page can
    report the limitation instead of implying that every record is
    attributable.
    """
    if storage is None:
        return {"supported": False, "tables": {}, "notes": ["No storage backend."]}
    reporter = getattr(storage, "provenance_capability", None)
    if reporter is None:
        return {"supported": True, "tables": {}, "notes": []}
    try:
        return reporter()
    except Exception as exc:  # capability probing must never break the page
        return {
            "supported": False,
            "tables": {},
            "notes": [f"Provenance capability could not be determined: {exc}"],
        }


def render(ctx) -> None:
    ui.page_header(
        "Settings / data sources",
        "Configuration &amp; Capability Inventory",
        "What RESON is connected to, what it can collect on this machine, "
        "and how much data it keeps. Every value shown is read from the live "
        "configuration or from the running collector — never a placeholder.",
    )

    ui.mode_banner(
        mode_label=ctx.mode.label,
        description=ctx.mode.description,
        available=ctx.mode.available,
        reason=ctx.mode.unavailable_reason,
        detail=ctx.mode.detail or None,
        simulated=ctx.simulated,
    )

    # ------------------------------------------------------------------
    # Monitoring mode + backend
    # ------------------------------------------------------------------
    left, right = st.columns(2)
    with left:
        with st.container(border=True):
            ui.panel_header(
                "Monitoring configuration",
                "Current session configuration",
            )
            st.json(
                {
                    "monitoring_mode": ctx.mode.mode.value,
                    "monitoring_mode_label": ctx.mode.label,
                    "telemetry_source_in_view": ctx.source,
                    "storage_backend": ctx.backend_name,
                    "simulated": ctx.simulated,
                    "derived_status": ctx.status.status.value,
                    "status_detail": ctx.status.detail,
                    "live": ctx.live,
                    "timestamps_stored_in": "UTC",
                    "timestamps_displayed_in": ctx.timezone_name,
                }
            )

    with right:
        with st.container(border=True):
            ui.panel_header(
                "Pipeline status",
                "Derived from actual collector health and stored data",
            )
            st.json(
                {
                    "status": ctx.status.status.value,
                    "label": ctx.status.label,
                    "last_observation": (
                        ctx.status.last_observation.isoformat()
                        if ctx.status.last_observation
                        else None
                    ),
                    "age_seconds": (
                        round(ctx.status.age_seconds, 1)
                        if ctx.status.age_seconds is not None
                        else None
                    ),
                    "stale": ctx.status.stale,
                }
            )

    # ------------------------------------------------------------------
    # Collector capabilities
    # ------------------------------------------------------------------
    collector = ctx.collector
    with st.container(border=True):
        ui.panel_header(
            "Collector capability inventory",
            (
                "Reported by the running platform collector"
                if collector is not None
                else "No collector is attached to this session"
            ),
        )
        if collector is None:
            st.info(
                "Start the collector from the sidebar, or run the standalone "
                "agent (`python -m src.data.agent`) to collect real telemetry "
                "independently of this dashboard."
            )
        else:
            health = collector.health()
            st.markdown(
                f'<div class="reson-sub">Collector: {type(collector).__name__} '
                f"· source: {health.source} · host: {health.host_id}</div>",
                unsafe_allow_html=True,
            )
            rows = [
                {
                    "Capability": name,
                    "Supported": "yes" if value else "no",
                }
                for name, value in sorted(health.capabilities.items())
            ]
            st.dataframe(rows, width="stretch", hide_index=True)
            st.caption(
                "A capability marked 'no' means the platform does not expose "
                "that data to an unprivileged process. RESON reports it as "
                "unavailable rather than inventing a value."
            )

    # ------------------------------------------------------------------
    # Data source health
    # ------------------------------------------------------------------
    with st.container(border=True):
        ui.panel_header(
            "Storage provenance capability",
            "Whether the selected backend can store telemetry provenance",
        )
        capability = _provenance_capability(ctx.storage)
        if capability.get("supported", True):
            st.success(
                "The selected backend stores the source and host of every "
                "record, so live, demonstration, imported and unknown data "
                "stay distinguishable."
            )
        else:
            st.warning(
                "The selected backend cannot store telemetry provenance for "
                "every table. Records written there cannot be attributed to "
                "a source and are reported as 'unknown' — never as live "
                "telemetry."
            )
            st.json(capability)
        notes = capability.get("notes") or []
        for note in notes:
            st.caption(note)

    with st.container(border=True):
        ui.panel_header(
            "Data source health",
            "Every telemetry source present in the selected backend",
        )
        if not ctx.sources:
            st.info("No telemetry is stored in the selected backend.")
        else:
            st.dataframe(
                [
                    {
                        "Source": item.source,
                        "Kind": "live host telemetry" if item.is_live else "non-live",
                        "Metrics": item.metrics,
                        "Logs": item.logs,
                        "Events": item.events,
                        "Deployments": item.deployments,
                        "Total": item.total,
                        "Hosts": ", ".join(item.hosts) or "—",
                        f"Last observed ({ctx.timezone_name})": (
                            ctx.format_timestamp(item.last_observed)
                            if item.last_observed
                            else "—"
                        ),
                    }
                    for item in ctx.sources
                ],
                width="stretch",
                hide_index=True,
            )
            st.caption(
                "Records with no source are reported as 'unknown'. RESON never "
                "infers that a record is real merely because it has a "
                "plausible timestamp or value."
            )

    # ------------------------------------------------------------------
    # Monitored hosts
    # ------------------------------------------------------------------
    with st.container(border=True):
        ui.panel_header(
            "Monitored hosts",
            "Hosts with telemetry in the current source",
        )
        if not ctx.hosts:
            st.info("No hosts are associated with the current source.")
        else:
            st.dataframe(
                [
                    {
                        "Host id": item.host_id,
                        "Source": item.source,
                        "Kind": "live" if item.is_live else "non-live",
                        "Metrics": item.metric_count,
                        f"Last observed ({ctx.timezone_name})": (
                            ctx.format_timestamp(item.last_observed)
                            if item.last_observed
                            else "—"
                        ),
                    }
                    for item in ctx.hosts
                ],
                width="stretch",
                hide_index=True,
            )
            st.caption(
                "Host identifiers are stable, non-secret, hashed machine "
                "identifiers (overridable with RESON_HOST_ID). They are never "
                "usernames, home paths, or IP addresses."
            )

    # ------------------------------------------------------------------
    # Collection configuration + retention
    # ------------------------------------------------------------------
    left, right = st.columns(2)
    with left:
        with st.container(border=True):
            ui.panel_header(
                "Collection configuration",
                "Applied by the standalone agent",
            )
            st.json(
                {
                    "default_interval_seconds": 5.0,
                    "minimum_interval_seconds": 1.0,
                    "default_retention_days": 30,
                    "agent_entry_point": "python -m src.data.agent",
                    "agent_options": [
                        "--backend",
                        "--interval",
                        "--retention-days",
                        "--events",
                        "--platform",
                        "--host-id",
                        "--once",
                    ],
                }
            )
            st.caption(
                "Retention is applied by the agent while it runs. Lower the "
                "interval only if the monitored machine can afford it; RESON "
                "enforces a one-second floor."
            )

    with right:
        with st.container(border=True):
            ui.panel_header(
                "Export",
                "Download the stored telemetry for this source",
            )
            storage = ctx.storage
            if storage is None:
                st.info("Storage is unavailable.")
            else:
                metrics = storage.get_metrics(source=ctx.source)
                if not metrics:
                    st.info("No metrics are stored for this source.")
                else:
                    frame = pd.DataFrame(
                        [
                            {
                                "timestamp": m.timestamp.isoformat(),
                                "service": m.service,
                                "environment": str(
                                    getattr(m.environment, "value", m.environment)
                                ),
                                "metric_name": m.metric_name,
                                "value": m.value,
                                "unit": m.unit,
                                "source": m.source or "unknown",
                                "host_id": m.host_id or "",
                            }
                            for m in metrics
                        ]
                    )
                    st.download_button(
                        "Download metrics as CSV",
                        data=frame.to_csv(index=False).encode("utf-8"),
                        file_name="reson_metrics.csv",
                        mime="text/csv",
                        key="reson-export-metrics",
                    )

    # ------------------------------------------------------------------
    # Privacy + hosted ingestion
    # ------------------------------------------------------------------
    with st.container(border=True):
        ui.panel_header(
            "Privacy and hosted ingestion",
            "What RESON collects, and what it does not",
        )
        st.markdown(
            "**Collected by default** — CPU and memory utilization, disk "
            "capacity and I/O counters, network interface counters (no "
            "payloads), process name/PID/CPU/RSS, battery status where "
            "present, and hardware sensors where the OS exposes them.\n\n"
            "**Never collected** — passwords, tokens, environment variable "
            "values, process command-line arguments, browser history, packet "
            "payloads, or unrelated personal files.\n\n"
            "**Event collection is opt-in** — the Windows collector reads an "
            "explicit allowlist of channels and event identifiers, bounded by "
            "a time window and a record limit, with deduplication and a "
            "checkpoint."
        )
        st.info(
            "Remote (hosted) ingestion is not enabled in this build. Opening "
            "a hosted RESON page never observes the visitor's computer. "
            "Hosted real monitoring requires the separate authenticated "
            "ingestion path described in docs/deployment.md; until it is "
            "configured, the hosted mode reports 'unavailable' rather than "
            "showing synthetic data as if it were live."
        )
        st.json(
            {
                "remote_ingestion_enabled": False,
                "transmits_data_by_default": False,
                "collector_secrets_in_frontend": False,
                "timestamps_stored_in": "UTC",
                "timestamps_displayed_in": ctx.timezone_name,
                "display_timezone_offset": ctx.zone_offset(),
            }
        )
