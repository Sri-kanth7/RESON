"""RESON Streamlit entry point: shared shell, navigation, backend status."""

import os

import streamlit as st

from app import theme as th
from app.components import ui
from app.pages import (
    behavior,
    history,
    incidents,
    overview,
    processes,
    settings,
    system_events,
    system_resources,
)
from app.services import backend as svc
from src.common.timeutil import (
    COMMON_TIMEZONES,
    resolve_timezone,
    server_timezone_name,
    timezone_label,
)
from src.data.collector import CollectorState
from src.data.collector_factory import create_collector
from src.data.monitoring import MonitoringMode

NAV_OPTIONS = (
    "Overview",
    "System Resources",
    "Processes",
    "System Events",
    "Incidents",
    "Behavior Analytics",
    "Historical Explorer",
    "Settings",
)

#: Monitoring modes offered in the sidebar, in display order.
MODE_OPTIONS = (
    (MonitoringMode.LOCAL.value, "Local monitoring (this computer)"),
    (MonitoringMode.DEMONSTRATION.value, "Demonstration (simulated data)"),
    (MonitoringMode.HOSTED.value, "Hosted monitoring (remote collector)"),
    (MonitoringMode.IMPORTED.value, "Imported history"),
)

st.set_page_config(
    page_title="RESON · Incident Intelligence",
    page_icon="◉",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(th.OBSIDIAN_CSS, unsafe_allow_html=True)


def _default_backend() -> str:
    """Return the backend preselected in the sidebar.

    Supabase is the production default, but it requires credentials. When
    they are absent the sidebar preselects **sqlite**, which works with no
    credentials and no network, instead of preselecting a backend that would
    immediately fail. This is an initial selection only: the user can always
    change the backend, and nothing is ever silently substituted at runtime.
    """
    if os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SECRET_KEY"):
        return "supabase"
    return "sqlite"


def _default_timezone() -> str:
    """Return the timezone the dashboard displays timestamps in by default.

    The host's own zone is used when the platform exposes one, otherwise
    UTC. Only the *presentation* changes: every value stays stored in UTC.
    """
    return server_timezone_name() or "UTC"


def _timezone_choices() -> list[str]:
    """Return the selectable display timezones, host zone first."""
    choices: list[str] = []
    host_zone = server_timezone_name()
    if host_zone:
        choices.append(host_zone)
    for name in COMMON_TIMEZONES:
        if name not in choices:
            choices.append(name)
    return choices


def _timezone_label(name: str) -> str:
    """Return the sidebar label for a timezone choice."""
    if name == server_timezone_name() and name != "UTC":
        return f"Local time ({name})"
    if name == "UTC":
        return "UTC (Coordinated Universal Time)"
    return name


def _timezone_sidebar() -> str:
    """Render the display-timezone control and return the chosen zone.

    Timestamps are always *stored* in UTC; this control only changes the
    timezone they are *rendered* in, and the current choice is shown in the
    sidebar so a reader always knows which clock they are looking at.
    """
    st.sidebar.markdown(
        '<div class="reson-kicker">Display</div>',
        unsafe_allow_html=True,
    )
    choices = _timezone_choices()
    current = st.session_state.get("reson_timezone")
    if current not in choices:
        current = choices[0]
    selected = st.sidebar.selectbox(
        "Display times in",
        choices,
        index=choices.index(current),
        format_func=_timezone_label,
        help=(
            "All timestamps are stored in UTC. This setting changes only how "
            "they are rendered, including daylight-saving adjustments."
        ),
    )
    custom = st.sidebar.text_input(
        "Other IANA time zone",
        value="",
        placeholder="e.g. Europe/Lisbon",
        help=(
            "Optional. Any IANA zone name is accepted and takes precedence "
            "over the list above."
        ),
    ).strip()
    if custom:
        resolved = resolve_timezone(custom)
        # An unrecognised name resolves to UTC; only accept it when the user
        # typed something that is not silently downgraded.
        if timezone_label(resolved) == "UTC" and custom.upper() != "UTC":
            st.sidebar.error(
                f"'{custom}' is not a known time zone. Showing UTC instead."
            )
        else:
            st.session_state["reson_timezone"] = custom
            return custom
    st.session_state["reson_timezone"] = selected
    return selected


def _init_state() -> None:
    st.session_state.setdefault("reson_backend", _default_backend())
    st.session_state.setdefault("reson_app", None)
    st.session_state.setdefault("reson_app_error", None)
    st.session_state.setdefault("reson_demo", None)
    st.session_state.setdefault("reson_demo_error", None)
    st.session_state.setdefault("reson_collector", None)
    st.session_state.setdefault("reson_mode", MonitoringMode.LOCAL.value)
    st.session_state.setdefault("reson_timezone", _default_timezone())


def _stop_collector() -> None:
    """Stop any running real-telemetry collector without touching storage."""

    collector = st.session_state.get("reson_collector")
    if collector is None:
        return
    try:
        collector.stop(timeout=5.0)
    except Exception:
        pass
    st.session_state["reson_collector"] = None


def _ensure_application(backend: str) -> None:
    """Rebuild the application when the selected backend changes.

    Switching storage stops any running collector: the new backend never
    inherits the old backend's history, and an active observation loop must
    not outlive the backend it writes into.
    """
    current = st.session_state.get("reson_app")
    if (
        current is not None
        and current.config.storage_backend == backend
    ):
        return
    _stop_collector()
    st.session_state["reson_app"] = None
    st.session_state["reson_app_error"] = None
    st.session_state["reson_demo"] = None
    st.session_state["reson_demo_error"] = None
    try:
        st.session_state["reson_app"] = svc.build_application(backend)
    except RuntimeError as exc:
        st.session_state["reson_app_error"] = str(exc)


def _sidebar() -> str:
    st.sidebar.markdown(
        '<div style="font-size:1.1rem;font-weight:700;letter-spacing:-0.02em">'
        '<span style="color:#00E5FF">◉</span> RESON</div>'
        '<div class="reson-kicker">Core intelligence</div>',
        unsafe_allow_html=True,
    )
    backend = st.sidebar.selectbox(
        "Storage backend",
        ("supabase", "sqlite", "memory"),
        index=("supabase", "sqlite", "memory").index(
            st.session_state["reson_backend"]
            if st.session_state["reson_backend"] in ("supabase", "sqlite", "memory")
            else "supabase"
        ),
        help=(
            "Supabase is the production default (persistent cloud storage, "
            "requires SUPABASE_URL and SUPABASE_SECRET_KEY). SQLite stores "
            "persistent data on your local machine with no credentials. "
            "Memory is temporary, non-persistent storage for offline use."
        ),
    )
    if backend != st.session_state["reson_backend"]:
        st.session_state["reson_backend"] = backend
        st.rerun()

    if not (
        os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SECRET_KEY")
    ):
        st.sidebar.caption(
            "Supabase credentials are not configured, so **sqlite** is "
            "preselected. Switch backends at any time; nothing is "
            "substituted silently."
        )

    mode_keys = [key for key, _ in MODE_OPTIONS]
    mode_labels = {key: label for key, label in MODE_OPTIONS}
    current_mode = st.session_state.get("reson_mode", MonitoringMode.LOCAL.value)
    if current_mode not in mode_keys:
        current_mode = MonitoringMode.LOCAL.value
    selected_label = st.sidebar.selectbox(
        "Monitoring mode",
        [mode_labels[key] for key in mode_keys],
        index=mode_keys.index(current_mode),
        help=(
            "Local monitoring shows real telemetry from a collector running on "
            "this computer. Demonstration shows clearly labeled synthetic "
            "data. Hosted monitoring shows telemetry received from a "
            "separately running collector — opening this page never observes "
            "your computer."
        ),
    )
    mode = next(
        key for key in mode_keys if mode_labels[key] == selected_label
    )
    if mode != st.session_state["reson_mode"]:
        st.session_state["reson_mode"] = mode
        st.rerun()

    app = st.session_state.get("reson_app")
    if app is not None:
        health = svc.storage_health(app.storage)
        persistent = backend in ("supabase", "sqlite")
        persistence_label = (
            "persistent local file"
            if backend == "sqlite"
            else ("persistent cloud" if backend == "supabase" else "memory only (RAM)")
        )
        durability_label = "durable" if persistent else "not durable"
        db_path = ""
        if backend == "sqlite" and health.get("database_path"):
            db_path = f" · {health['database_path']}"
        st.sidebar.markdown(
            "<span class='reson-sub'>"
            f"Storage: {backend} · {persistence_label} ({durability_label})"
            f"{db_path} · "
            f"operational: {'yes' if health['operational'] else 'no'}"
            "</span>",
            unsafe_allow_html=True,
        )
        if not health["operational"]:
            st.sidebar.error(
                f"Storage read failed: {health['error']}. New data cannot be "
                "saved until the backend is operational."
            )
        provenance = health.get("provenance") or {}
        if provenance and not provenance.get("supported", True):
            st.sidebar.warning(
                "This backend cannot store telemetry provenance for every "
                "table. Records written there are reported as 'unknown' "
                "and never as live telemetry."
            )

    page = st.sidebar.radio("Navigate", NAV_OPTIONS)

    _timezone_sidebar()

    app = st.session_state.get("reson_app")
    if app is not None:
        collector = st.session_state.get("reson_collector")
        demo = st.session_state.get("reson_demo")
        context = svc.build_context(
            app.storage,
            collector,
            st.session_state["reson_mode"],
            demo=demo,
            backend_name=backend,
            display_tz=st.session_state.get("reson_timezone"),
        )
        st.session_state["reson_context"] = context

        if context.mode.simulated:
            tone = "sim"
            label = "Demonstration data"
        elif context.live:
            tone = ""
            label = "Live data"
        else:
            tone = "off"
            label = context.status.label
        st.sidebar.markdown(
            f'<div class="reson-live {tone}" style="margin-top:0.25rem">'
            f"{label}</div>",
            unsafe_allow_html=True,
        )

    st.sidebar.markdown("---")
    if app is not None:
        timeline = st.sidebar.radio(
            "Demonstration timeline",
            ("full", "quick"),
            format_func=lambda value: (
                "Full timeline (15 batches)"
                if value == "full"
                else "Quick timeline (5 batches)"
            ),
            help=(
                "The full timeline analyses the complete 15-batch simulated "
                "history. The quick timeline runs the identical pipeline over "
                "a much shorter window and finishes in a fraction of the "
                "time. Both produce simulated telemetry that is clearly "
                "labeled."
            ),
        )
        if st.sidebar.button(
            "Run Local Demonstration Analysis",
            type="primary",
            help="Deterministic simulated telemetry through the real pipeline.",
        ):
            st.session_state["reson_demo_error"] = None
            try:
                scenarios = svc.resolve_scenarios(profile=timeline)
                with st.spinner("Running ingestion and intelligence pipeline…"):
                    st.session_state["reson_demo"] = svc.run_demonstration(
                        app, scenarios=scenarios
                    )
                # Demonstration data is stored under the 'simulation' source.
                # Switch the dashboard into demonstration mode so the results
                # are shown in their own clearly labeled view rather than
                # being mixed with live telemetry.
                st.session_state["reson_mode"] = (
                    MonitoringMode.DEMONSTRATION.value
                )
            except Exception as exc:  # visible, never swallowed
                st.session_state["reson_demo_error"] = (
                    f"Demonstration analysis failed: {exc}"
                )
            st.rerun()
        demo_error = st.session_state.get("reson_demo_error")
        if demo_error:
            st.sidebar.error(demo_error)
        elif st.session_state.get("reson_demo") is not None:
            demo = st.session_state["reson_demo"]
            label = (
                "Quick simulated analysis ready"
                if getattr(demo, "timeline", "full") == "quick"
                else "Simulated analysis ready"
            )
            st.sidebar.success(
                f"{label}: {demo.records} records, "
                f"{len(demo.incident_result.researches)} incident(s)."
            )

    _observation_sidebar(app)

    st.sidebar.markdown("---")
    if app is not None:
        try:
            counts = svc.storage_counts(app.storage)
            stored = (
                counts["metrics"]
                + counts["logs"]
                + counts["events"]
                + counts["deployments"]
            )
            st.sidebar.markdown(
                f"<span class='reson-sub'>Backend: {backend} · "
                f"{stored:,} telemetry records · "
                f"{counts['incidents']} incidents"
                + (" · SIMULATED" if st.session_state.get("reson_demo") else "")
                + "</span>",
                unsafe_allow_html=True,
            )
        except Exception as exc:
            st.sidebar.error(f"Backend read failed: {exc}")
    else:
        st.sidebar.warning("Backend unavailable — see main panel.")
    return page


def _observation_sidebar(app) -> None:
    """Real host telemetry observation controls (start/stop/state)."""

    st.sidebar.markdown(
        '<div class="reson-kicker">Real host telemetry</div>',
        unsafe_allow_html=True,
    )
    collector = st.session_state.get("reson_collector")
    running = collector is not None and collector.is_running

    st.sidebar.markdown(
        "<span class='reson-sub'>"
        f"Source: {collector.source if collector else '—'} · "
        f"Host: {collector.host_id if collector else '—'}"
        "</span>",
        unsafe_allow_html=True,
    )

    start_disabled = running or app is None
    stop_disabled = not running
    col_start, col_stop = st.sidebar.columns(2)

    if col_start.button(
        "Start observation",
        disabled=start_disabled,
        help="Collect host and process metrics every 5 seconds and ingest "
        "them through the selected backend.",
    ):
        try:
            new_collector = create_collector()
            new_collector.start(
                interval_seconds=5.0,
                publish=svc.observation_publish(app.ingestion),
            )
            st.session_state["reson_collector"] = new_collector
        except Exception as exc:  # visible, never swallowed
            st.session_state["reson_collector_error"] = str(exc)
        st.rerun()

    if col_stop.button(
        "Stop observation",
        disabled=stop_disabled,
        help="Stop the collector and terminate its worker thread.",
    ):
        if collector is not None:
            try:
                collector.stop(timeout=5.0)
            except Exception as exc:  # visible, never swallowed
                st.session_state["reson_collector_error"] = str(exc)
            st.session_state["reson_collector"] = None
        st.rerun()

    if st.session_state.get("reson_collector_error"):
        st.sidebar.error(
            f"Collection error: {st.session_state['reson_collector_error']}"
        )

    if running and collector is not None:
        host = collector.last_host_measurement
        state_label = (
            f"{collector.state.value.upper()}"
            f" · {collector.last_error}" if collector.last_error
            else collector.state.value.upper()
        )
        tone = (
            "chip-critical"
            if collector.state == CollectorState.ERROR
            else "chip-low"
        )
        st.sidebar.markdown(
            f"<span class='reson-sub'>Collector: "
            f"{th.chip(state_label, tone)}</span>",
            unsafe_allow_html=True,
        )
        if host is not None:
            context = st.session_state.get("reson_context")
            display_tz = (
                context.display_tz
                if context is not None
                else resolve_timezone(st.session_state.get("reson_timezone"))
            )
            st.sidebar.markdown(
                "<span class='reson-sub'>"
                f"CPU {host.cpu_percent if host.cpu_percent is not None else '—'}% · "
                f"RAM {host.memory_percent if host.memory_percent is not None else '—'}% · "
                f"Last sample {svc.format_clock(host.timestamp, display_tz)} "
                f"({svc.timezone_label(display_tz)})"
                "</span>",
                unsafe_allow_html=True,
            )
        procs = collector.last_process_measurements
        st.sidebar.markdown(
            f"<span class='reson-sub'>"
            f"Processes observed: {len(procs)}"
            "</span>",
            unsafe_allow_html=True,
        )
    elif collector is not None and not running:
        st.sidebar.markdown(
            "<span class='reson-sub'>Collector: "
            f"{th.chip('STOPPED', 'chip-neutral')}</span>",
            unsafe_allow_html=True,
        )


def _backend_error_panel(message: str) -> None:
    st.markdown(
        '<div class="reson-kicker">Backend unavailable</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="reson-title">Select a working storage backend</div>',
        unsafe_allow_html=True,
    )
    st.error(message)
    st.markdown(
        "Supabase is the production default and needs `SUPABASE_URL` and "
        "`SUPABASE_SECRET_KEY`. SQLite stores persistent data on your local "
        "machine with no credentials. For fully offline local use, select "
        "**sqlite** or **memory** in the sidebar — memory is non-persistent "
        "(RAM only), and nothing falls back silently."
    )


def main() -> None:
    _init_state()
    _ensure_application(st.session_state["reson_backend"])
    page = _sidebar()

    app = st.session_state.get("reson_app")
    if app is None:
        ui.topbar(
            environment="n/a",
            data_source=st.session_state["reson_backend"],
            simulated=False,
            reachable=False,
        )
        _backend_error_panel(
            st.session_state.get("reson_app_error")
            or "Storage backend could not be constructed."
        )
        return

    demo = st.session_state.get("reson_demo")
    context = st.session_state.get("reson_context")
    if context is None:
        context = svc.build_context(
            app.storage,
            st.session_state.get("reson_collector"),
            st.session_state["reson_mode"],
            demo=demo,
            backend_name=st.session_state["reson_backend"],
            display_tz=st.session_state.get("reson_timezone"),
        )
        st.session_state["reson_context"] = context

    ui.topbar(
        environment=app.config.environment,
        data_source=context.source or "all",
        simulated=context.simulated,
        reachable=True,
        mode_label=context.mode.label,
        status_label=context.status.label,
        status_ok=context.live,
        timezone_label=context.timezone_name,
    )

    storage = app.storage
    collector = st.session_state.get("reson_collector")
    live = None
    if collector is not None:
        host_id = collector.host_id
        live = {
            "collector": collector,
            "readiness": svc.host_readiness(
                storage, host_id, source=collector.source
            ),
        }
        try:
            live["comparison"] = svc.compare_today_vs_prior_day(
                storage, host_id=host_id, source=collector.source
            )
        except Exception as exc:  # comparison is informational, never fatal
            live["comparison_error"] = str(exc)

    try:
        if page == "Overview":
            overview.render(context, live=live)
        elif page == "System Resources":
            system_resources.render(context)
        elif page == "Processes":
            processes.render(context)
        elif page == "System Events":
            system_events.render(context)
        elif page == "Incidents":
            incidents.render(context)
        elif page == "Behavior Analytics":
            behavior.render(context)
        elif page == "Historical Explorer":
            history.render(context)
        else:
            settings.render(context)
    except Exception as exc:
        _backend_error_panel(
            f"Backend read failed: {exc}. The selected "
            f"({st.session_state['reson_backend']}) backend is unreachable or "
            "misconfigured. Select memory mode for offline use, or verify "
            "Supabase credentials and connectivity, then rerun."
        )


main()
