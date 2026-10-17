"""Historical Incident Explorer page: real similarity ranking."""

import streamlit as st

from app import theme as th
from app.components import ui
from app.services import backend as svc
from src.incidents.service import IncidentIntelligenceService

_FACTORS = (
    ("service_overlap", "Service overlap"),
    ("metric_similarity", "Metric similarity"),
    ("event_sequence_similarity", "Event sequence"),
    ("deployment_relationship", "Deployment relationship"),
    ("temporal_similarity", "Temporal similarity"),
    ("scenario_match", "Scenario match"),
)


def render(storage, demo) -> None:
    ui.page_header(
        "Historical incident repository &amp; pattern matching",
        "Historical Incident Explorer &amp; Pattern Matching",
        "Structural comparison against stored history using the existing "
        "similarity engine. Scores are deterministic structural comparisons — "
        "never probabilities.",
    )

    incidents = svc.ordered_incidents(storage)
    if not incidents:
        ui.empty_state(
            "No historical data",
            "Run **Run Local Demonstration Analysis** to persist incidents, "
            "then return here to rank them.",
        )
        return

    filter_left, filter_right = st.columns(2)
    with filter_left:
        sev_filter = st.selectbox(
            "Severity filter", ["all", "critical", "high", "medium", "low"]
        )
    with filter_right:
        status_filter = st.selectbox(
            "Status filter", ["all", "open", "investigating", "resolved"]
        )
    filtered = [
        i
        for i in incidents
        if (sev_filter == "all" or str(getattr(i.severity, "value", i.severity)) == sev_filter)
        and (
            status_filter == "all"
            or str(getattr(i.status, "value", i.status)) == status_filter
        )
    ]
    if not filtered:
        st.info("No incidents match the selected filters.")
        return

    options = {svc.incident_label(i): i for i in filtered}
    current = options[st.selectbox("Current incident", list(options.keys()))]

    with st.container(border=True):
        ui.panel_header(
            "Anchor / current context",
            "The incident rankings are computed against",
        )
        st.markdown(
            f"{th.severity_chip(current.severity)} "
            f"{th.status_chip(current.status)}",
            unsafe_allow_html=True,
        )
        st.markdown(f"**{current.title}**")
        st.markdown(
            f"<span class='reson-sub'>{current.id} · "
            f"{current.start_time.strftime('%Y-%m-%d %H:%M UTC')} · services: "
            f"{', '.join(current.affected_services) or '—'}</span>",
            unsafe_allow_html=True,
        )

    research = svc.research_for(demo, current.id)
    if research is None:
        with st.container(border=True):
            ui.panel_header(
                "Registry and timeline available — ranking unavailable",
                "Similarity ranking needs this incident's supporting signals",
            )
            st.markdown(
                "Signals exist only for analyses run in the current session. "
                "Re-run the demonstration analysis to enable ranking. The "
                "stored timeline below is complete and traceable."
            )
            evidence = storage.get_evidence(source_id=str(current.id))
            rows = [
                {
                    "Time (UTC)": e.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    "Target": th.short_id(e.target_id),
                    "Type": str(getattr(e.target_type, "value", e.target_type)),
                    "Strength": round(e.strength, 3),
                }
                for e in evidence
            ]
            st.dataframe(rows, width="stretch", hide_index=True)
        return

    service = IncidentIntelligenceService(storage=storage)
    candidates = service.historical_candidates(
        signal_context=demo.signal_context if demo is not None else None
    )
    matches = service.find_historical_matches(research, candidates)

    if not matches:
        st.info(
            "No comparable historical incidents. Similarity excludes the "
            "current incident itself by identity."
        )
        return

    st.markdown(
        f"**Ranked historical signatures** — {len(matches)} candidates "
        "(current incident excluded)"
    )
    left, right = st.columns([5, 7])
    with left:
        for match in matches:
            hist = match.historical_incident
            with st.container(border=True):
                st.markdown(
                    f"{th.severity_chip(hist.severity)} "
                    f"<span class='reson-mono' style='font-size:0.9rem;"
                    f"color:#00E5FF'>{match.similarity.overall_similarity:.3f}</span>",
                    unsafe_allow_html=True,
                )
                st.markdown(f"**{hist.title}**")
                st.markdown(
                    f"<span class='reson-sub'>{th.short_id(hist.id)} · "
                    f"{hist.start_time.strftime('%Y-%m-%d %H:%M UTC')} · "
                    f"{', '.join(hist.affected_services)}</span>",
                    unsafe_allow_html=True,
                )
                st.markdown(
                    f"<span style='font-size:0.8rem'>{match.explanation}</span>",
                    unsafe_allow_html=True,
                )
    with right:
        with st.container(border=True):
            ui.panel_header(
                "Explainable similarity factor matrix",
                f"Top match {th.short_id(matches[0].historical_incident_id)} · "
                f"overall {matches[0].similarity.overall_similarity:.3f}",
            )
            top = matches[0]
            bars = "".join(
                th.factor_bar(label, getattr(top.similarity, attr))
                for attr, label in _FACTORS
            )
            st.markdown(bars, unsafe_allow_html=True)
        with st.container(border=True):
            ui.panel_header(
                "Synchronized phase incident progression",
                "Current vs historical timeline (T0 aligned by sequence)",
            )
            col_c, col_h = st.columns(2)
            with col_c:
                st.markdown(
                    "<span class='reson-sub'>CURRENT</span>",
                    unsafe_allow_html=True,
                )
                for item in research.timeline:
                    st.markdown(
                        f"<div class='reson-mono' style='font-size:0.72rem'>"
                        f"{item.timestamp.strftime('%H:%M:%S')} · "
                        f"{th.short_id(item.target_id)}</div>",
                        unsafe_allow_html=True,
                    )
            with col_h:
                st.markdown(
                    "<span class='reson-sub'>HISTORICAL</span>",
                    unsafe_allow_html=True,
                )
                for item in top.historical_timeline:
                    st.markdown(
                        f"<div class='reson-mono' style='font-size:0.72rem'>"
                        f"{item.timestamp.strftime('%H:%M:%S')} · "
                        f"{th.short_id(item.target_id)}</div>",
                        unsafe_allow_html=True,
                    )
    st.caption(
        "Absent evidence scores 0.0 with full weight: no evidence of a factor "
        "is distinct from a mismatch. Ranking is deterministic."
    )
