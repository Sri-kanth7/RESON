"""RESON Streamlit entry point: shared shell, navigation, backend status."""

import streamlit as st

from app import theme as th
from app.components import ui
from app.pages import behavior, history, incidents, overview
from app.services import backend as svc

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


def _ensure_application(backend: str) -> None:
    """Rebuild the application when the selected backend changes."""
    current = st.session_state.get("reson_app")
    if (
        current is not None
        and current.config.storage_backend == backend
    ):
        return
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
    try:
        if page == "Overview":
            overview.render(storage, demo, st.session_state["reson_backend"])
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
