"""Incident Investigation page: evidence chains from real backend records."""

import streamlit as st

from app import theme as th
from app.components import ui
from app.services import backend as svc
from src.data.schemas import SOURCE_UNKNOWN, classify_source


def _type_chip(target_type: object) -> str:
    value = str(getattr(target_type, "value", target_type)).lower()
    style = {
        "metric": "chip-info",
        "log": "chip-high",
        "event": "chip-corr",
        "deployment": "chip-neutral",
    }.get(value, "chip-neutral")
    return th.chip(value.upper(), style)


def _rel_class(relationship: object) -> str:
    value = str(getattr(relationship, "value", relationship)).lower()
    if "event" in value:
        return ""
    if "temporal" in value:
        return "temp"
    return "corr"


def render(ctx) -> None:
    ui.page_header(
        "Incidents / deep evidence investigation",
        "Incident Investigation",
        "Correlated evidence only — association is not causation. Every entry "
        "traces to stored evidence and source records.",
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
    demo = ctx.demo
    incidents = svc.ordered_incidents(storage, source=ctx.source)
    unattributed = len(storage.get_incidents(source=SOURCE_UNKNOWN)) if storage else 0
    if not incidents:
        ui.empty_state(
            "No incidents stored",
            "Run **Run Local Demonstration Analysis** to detect incidents, "
            "or select a backend that already holds incident records.",
        )
        if unattributed:
            st.info(
                f"{unattributed} stored incident(s) predate provenance "
                "recording and are not shown in this mode. Their source "
                "cannot be established, so they are never presented as "
                "belonging to the current data source."
            )
        return

    options = {svc.incident_label(i, ctx.display_tz): i for i in incidents}
    selected_label = st.selectbox("Incident", list(options.keys()))
    incident = options[selected_label]

    incident_source = classify_source(incident.source)
    with st.container(border=True):
        st.markdown(
            f"{th.severity_chip(incident.severity)} "
            f"{th.status_chip(incident.status)} "
            f"{th.chip(f'source: {incident_source}', 'chip-neutral')} "
            f"<span class='reson-mono' style='font-size:0.75rem;color:#94A3B8'>"
            f"{incident.id}</span>",
            unsafe_allow_html=True,
        )
        st.markdown(f"### {incident.title}")
        end = (
            ctx.format_timestamp(incident.end_time)
            if incident.end_time is not None
            else "ongoing (no recovery evidence yet)"
        )
        st.markdown(
            f"<span class='reson-sub'>Start: "
            f"{ctx.format_timestamp(incident.start_time)} · End: "
            f"{end} · Services: {', '.join(incident.affected_services) or '—'}"
            "</span>",
            unsafe_allow_html=True,
        )

        research = svc.research_for(demo, incident.id)
        signals = research.signals if research is not None else []
        triggers = sum(1 for s in signals if s.trigger)

        if (
            str(getattr(incident.status, "value", incident.status)).lower()
            == "open"
        ):
            if st.button("Mark investigating", key=f"inv-{incident.id}"):
                updated = svc.mark_investigating(storage, incident)
                st.success(
                    f"Incident {th.short_id(updated.id)} is now investigating."
                )
                st.rerun()

    explanation = svc.severity_explanation(demo, incident.id)
    with st.container(border=True):
        ui.panel_header(
            "Severity rationale",
            "Recorded at detection time by the deterministic severity policy",
        )
        if explanation:
            st.markdown(explanation)
        else:
            st.markdown(
                "No recorded rationale for this incident in the current "
                "session. Severity explanations are produced at detection time "
                "and are not part of the persisted record — re-run the analysis "
                "to regenerate them. Severity, status, and timeline below are "
                "stored facts."
            )

    evidence = storage.get_evidence(
        source_id=str(incident.id), source=ctx.source
    )
    with st.container(border=True):
        ui.panel_header(
            "Evidence chain",
            f"Incident → {len(evidence)} linked evidence records "
            + (f"(session: {len(signals)} signals, {triggers} triggers)"
               if signals else ""),
        )
        if not evidence:
            st.info("No evidence links stored for this incident.")
        else:
            nodes = [("Incident", "trigger event", th.short_id(incident.id), "trigger")]
            for item in evidence[:6]:
                rel = str(
                    getattr(item.relationship_type, "value", item.relationship_type)
                )
                nodes.append(
                    (
                        str(getattr(item.target_type, "value", item.target_type)).upper(),
                        f"{rel} · {item.strength:.3f}",
                        th.short_id(item.target_id),
                        "corr" if "correl" in rel.lower() else "",
                    )
                )
            ui.flow(nodes)
            if len(evidence) > 6:
                st.caption(
                    f"Showing 6 of {len(evidence)} evidence links; the full "
                    "timeline is below."
                )

    tab_timeline, tab_signals, tab_correlation, tab_history = st.tabs(
        ["Timeline", "Signals", "Correlation", "Historical matches"]
    )

    with tab_timeline:
        if not evidence:
            st.info("No timeline entries stored for this incident.")
        else:
            items = ["<div class='reson-timeline'>"]
            for item in evidence:
                rel = _rel_class(item.relationship_type)
                rel_name = str(
                    getattr(item.relationship_type, "value", item.relationship_type)
                )
                items.append(
                    f"<div class='reson-timeline-item {rel}'>"
                    f"<div class='reson-mono' style='font-size:0.75rem'>"
                    f"{ctx.format_timestamp(item.timestamp)}</div>"
                    f"<div>{_type_chip(item.target_type)} "
                    f"{th.chip(f'source: {classify_source(item.source)}', 'chip-neutral')} "
                    f"<span class='reson-mono' style='font-size:0.75rem'>"
                    f"{th.short_id(item.target_id)}</span></div>"
                    f"<div style='font-size:0.8rem;color:#94A3B8'>"
                    f"{rel_name} · strength {item.strength:.3f}</div>"
                    f"<div style='font-size:0.8rem'>{item.explanation}</div>"
                    "</div>"
                )
            items.append("</div>")
            st.markdown("\n".join(items), unsafe_allow_html=True)

    with tab_signals:
        if not signals:
            st.info(
                "Signal details are available only for incidents analyzed in "
                "this session. Stored timelines above remain fully traceable "
                "via their referenced record identifiers."
            )
        else:
            st.markdown(
                f"**{len(signals)} supporting signals** ({triggers} triggers)"
            )
            rows = [
                {
                    f"Time ({ctx.timezone_name})": ctx.format_clock(s.timestamp),
                    "Type": str(getattr(s.source_type, "value", s.source_type)),
                    "Service": s.service,
                    "Trigger": "yes" if s.trigger else "no",
                    "Strength": round(s.strength, 3),
                    "Description": s.description,
                    "Source records": ", ".join(
                        th.short_id(r) for r in s.source_record_ids
                    ),
                }
                for s in sorted(signals, key=lambda s: s.timestamp)
            ]
            st.dataframe(rows, width="stretch", hide_index=True)

    with tab_correlation:
        result = demo.incident_result if demo is not None else None
        candidates = list(result.correlation_candidates) if result else []
        accepted_here = list(result.correlation_evidence) if result else []
        if not candidates:
            st.info(
                "Per-pair correlation factors are available only for analyses "
                "run in this session."
            )
        else:
            accepted = sum(1 for c in candidates if c.accepted)
            st.markdown(
                f"**{len(accepted_here)} accepted edges** from "
                f"**{len(candidates)} evaluated pairs** "
                f"({accepted} accepted overall in this window). Rejected pairs "
                "show exactly which time-bounded factors were missing."
            )
            if accepted_here:
                edge_nodes = []
                for evidence_item in accepted_here[:5]:
                    edge_nodes.append(
                        (
                            "Signal",
                            "→ signal",
                            th.short_id(evidence_item.target_id),
                            "corr",
                        )
                    )
                st.markdown("**Accepted correlation edges**")
                ui.flow(edge_nodes)
            rows = [
                {
                    "Source": th.short_id(c.source.source_id),
                    "Target": th.short_id(c.target.source_id),
                    "Relationship": str(
                        getattr(c.relationship_type, "value", c.relationship_type)
                    ),
                    "Accepted": "yes" if c.accepted else "no",
                    "Strength": round(c.strength, 3),
                    "Temporal": round(c.temporal_proximity, 2),
                    "Svc": round(c.same_service, 2),
                    "Env": round(c.same_environment, 2),
                    "Metric": round(c.metric_overlap, 2),
                    "Deploy": round(c.deployment_proximity, 2),
                    "Event": round(c.event_link, 2),
                }
                for c in candidates
            ]
            st.dataframe(rows, width="stretch", hide_index=True)

    with tab_history:
        matches = svc.historical_matches_for(demo, storage, incident.id, top_k=3)
        if not matches:
            st.info(
                "Historical matching needs an in-session analysis of this "
                "incident plus stored history. Open the Historical Explorer "
                "for the full ranking, or run the demonstration analysis."
            )
        else:
            for match in matches:
                hist = match.historical_incident
                st.markdown(
                    f"**{hist.title}** "
                    f"<span class='reson-mono' style='font-size:0.75rem'>"
                    f"overall {match.similarity.overall_similarity:.3f}</span>",
                    unsafe_allow_html=True,
                )
                st.markdown(
                    th.factor_bar(
                        "service_overlap", match.similarity.service_overlap
                    )
                    + th.factor_bar(
                        "metric_similarity", match.similarity.metric_similarity
                    )
                    + th.factor_bar(
                        "event_sequence",
                        match.similarity.event_sequence_similarity,
                    )
                    + th.factor_bar(
                        "deployment",
                        match.similarity.deployment_relationship,
                    )
                    + th.factor_bar(
                        "temporal", match.similarity.temporal_similarity
                    )
                    + th.factor_bar(
                        "scenario", match.similarity.scenario_match
                    ),
                    unsafe_allow_html=True,
                )
            st.caption(
                "Structural comparison, not a probability. Absent evidence "
                "scores 0.0 by design."
            )
