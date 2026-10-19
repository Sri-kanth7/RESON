"""RESON Streamlit entry point: shared shell, navigation, backend status."""

import streamlit as st

from app import theme as th
from app.components import ui
from app.pages import behavior, history, incidents, overview
from app.services import backend as svc
from src.data.windows_collector import (
    SOURCE_WINDOWS_LOCAL,
    CollectorState,
    WindowsTelemetryCollector,
)

NAV_OPTIONS = (
    "Overview",
    "Incidents",
    "Behavior Analytics",
    "Historical Explorer",
)

st.set_page_config(
    page_title="RESON · Incident Intelligence",
    page_icon="◉",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(th.OBSIDIAN_CSS, unsafe_allow_html=True)


def _init_state() -> None:
    st.session_state.setdefault("reson_backend", "supabase")
    st.session_state.setdefault("reson_app", None)
    st.session_state.setdefault("reson_app_error", None)
    st.session_state.setdefault("reson_demo", None)
    st.session_state.setdefault("reson_demo_error", None)
    st.session_state.setdefault("reson_collector", None)


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
        ("supabase", "memory"),
        index=("supabase", "memory").index(st.session_state["reson_backend"]),
        help="Supabase is the production default. Memory runs fully offline.",
    )
    if backend != st.session_state["reson_backend"]:
        st.session_state["reson_backend"] = backend
        st.rerun()

    app = st.session_state.get("reson_app")
    if app is not None:
        health = svc.storage_health(app.storage)
        persistent = backend == "supabase"
        st.sidebar.markdown(
            "<span class='reson-sub'>"
            f"Mode: {backend} · "
            f"{'persistent history' if persistent else 'memory only (RAM)'} · "
            f"operational: {'yes' if health['operational'] else 'no'}"
            "</span>",
            unsafe_allow_html=True,
        )
        if not health["operational"]:
            st.sidebar.error(
                f"Storage read failed: {health['error']}. New data cannot be "
                "saved until the backend is operational."
            )

    page = st.sidebar.radio("Navigate", NAV_OPTIONS)

    app = st.session_state.get("reson_app")
    if app is not None:
        if st.session_state.get("reson_demo") is not None:
            st.sidebar.markdown(
                '<div class="reson-live sim" style="margin-top:0.25rem">'
                "Simulated telemetry in session</div>",
                unsafe_allow_html=True,
            )
        else:
            st.sidebar.markdown(
                '<div class="reson-live">Live data mode</div>',
                unsafe_allow_html=True,
            )

    st.sidebar.markdown("---")
    if app is not None:
        if st.sidebar.button(
            "Run Local Demonstration Analysis",
            type="primary",
            help="Deterministic simulated telemetry through the real pipeline.",
        ):
            st.session_state["reson_demo_error"] = None
            try:
                with st.spinner("Running ingestion and intelligence pipeline…"):
                    st.session_state["reson_demo"] = svc.run_demonstration(app)
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
            st.sidebar.success(
                f"Simulated analysis ready: {demo.records} records, "
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
    """Real Windows telemetry observation controls (start/stop/state)."""

    st.sidebar.markdown(
        '<div class="reson-kicker">Real Windows telemetry</div>',
        unsafe_allow_html=True,
    )
    collector = st.session_state.get("reson_collector")
    running = collector is not None and collector.is_running

    st.sidebar.markdown(
        "<span class='reson-sub'>"
        f"Source: {SOURCE_WINDOWS_LOCAL} · "
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
            new_collector = WindowsTelemetryCollector()
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
            st.sidebar.markdown(
                "<span class='reson-sub'>"
                f"CPU {host.cpu_percent if host.cpu_percent is not None else '—'}% · "
                f"RAM {host.memory_percent if host.memory_percent is not None else '—'}% · "
                f"Last sample {host.timestamp.strftime('%H:%M:%S UTC')}"
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
        "`SUPABASE_SECRET_KEY`. For fully offline local use, select **memory** "
        "in the sidebar — no credentials, no network, no silent fallback."
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
    ui.topbar(
        environment=app.config.environment,
        data_source=st.session_state["reson_backend"],
        simulated=demo is not None,
        reachable=True,
    )

    storage = app.storage
    collector = st.session_state.get("reson_collector")
    live = None
    if collector is not None:
        host_id = collector.host_id
        live = {
            "collector": collector,
            "readiness": svc.host_readiness(storage, host_id),
        }
        try:
            live["comparison"] = svc.compare_today_vs_prior_day(
                storage, host_id=host_id, source=SOURCE_WINDOWS_LOCAL
            )
        except Exception as exc:  # comparison is informational, never fatal
            live["comparison_error"] = str(exc)

    try:
        if page == "Overview":
            overview.render(
                storage,
                demo,
                st.session_state["reson_backend"],
                live=live,
            )
        elif page == "Incidents":
            incidents.render(storage, demo)
        elif page == "Behavior Analytics":
            behavior.render(storage, demo)
        else:
            history.render(storage, demo)
    except Exception as exc:
        _backend_error_panel(
            f"Backend read failed: {exc}. The selected "
            f"({st.session_state['reson_backend']}) backend is unreachable or "
            "misconfigured. Select memory mode for offline use, or verify "
            "Supabase credentials and connectivity, then rerun."
        )


main()
