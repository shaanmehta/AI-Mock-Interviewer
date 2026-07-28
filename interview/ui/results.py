"""The results dashboard.

Replaces the previous ``st.json(result)`` dump with an actual report: a score
hero, a rubric radar, expandable per-question feedback cards, and downloadable
exports.
"""

from __future__ import annotations

from typing import Any, Dict, List

import plotly.graph_objects as go
import streamlit as st

from interview.report import build_printable_html
from interview.scoring import RUBRIC_KEYS, RUBRIC_LABELS, RUBRIC_SHORT_LABELS
from interview.ui import theme


def _radar(rubric: Dict[str, float]) -> go.Figure:
    # Short labels + tight margins: on a 375 px phone the long forms consumed
    # the entire width and collapsed the plot area to nothing.
    labels = [RUBRIC_SHORT_LABELS[key] for key in RUBRIC_KEYS]
    values = [float(rubric.get(key, 0)) for key in RUBRIC_KEYS]

    figure = go.Figure()
    figure.add_trace(
        go.Scatterpolar(
            r=values + values[:1],          # close the polygon
            theta=labels + labels[:1],
            fill="toself",
            fillcolor="rgba(79, 124, 255, 0.22)",
            line=dict(color=theme.BRAND, width=2),
            hovertemplate="%{theta}: %{r}/10<extra></extra>",
            name="Score",
        )
    )
    figure.update_layout(
        polar=dict(
            radialaxis=dict(
                visible=True, range=[0, 10], tickvals=[2, 4, 6, 8, 10],
                tickfont=dict(size=9), angle=90, tickangle=0,
            ),
            angularaxis=dict(tickfont=dict(size=10)),
            bgcolor="rgba(0,0,0,0)",
            # Let Plotly reserve exactly the space the labels need instead of
            # fixed margins that don't survive a narrow viewport.
            domain=dict(x=[0, 1], y=[0, 1]),
        ),
        showlegend=False,
        margin=dict(l=10, r=10, t=34, b=34, autoexpand=True),
        height=330,
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(size=11),
    )
    return figure


def _bars(rubric: Dict[str, float]) -> go.Figure:
    labels = [RUBRIC_LABELS[key] for key in RUBRIC_KEYS]
    values = [float(rubric.get(key, 0)) for key in RUBRIC_KEYS]
    # Plotly draws the first horizontal bar at the *bottom*, so sort strongest
    # first to make the weakest axis land at the top, where the eye starts.
    order = sorted(range(len(values)), key=lambda i: values[i], reverse=True)
    labels = [labels[i] for i in order]
    values = [values[i] for i in order]

    colours = [
        "#ef5f5f" if v < 5 else "#f0ad4e" if v < 7 else "#33d17a" for v in values
    ]

    figure = go.Figure(
        go.Bar(
            x=values,
            y=labels,
            orientation="h",
            marker=dict(color=colours),
            text=[f"{v:g}" for v in values],
            textposition="outside",
            cliponaxis=False,
            hovertemplate="%{y}: %{x}/10<extra></extra>",
        )
    )
    figure.update_layout(
        xaxis=dict(range=[0, 11.2], showgrid=True, gridcolor="rgba(127,140,170,.18)", zeroline=False),
        yaxis=dict(showgrid=False, automargin=True),
        margin=dict(l=8, r=24, t=16, b=16, autoexpand=True),
        height=330,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(size=12),
        bargap=0.32,
    )
    return figure


def _face_line(face: Dict[str, Any]) -> str:
    """Human-readable one-liner for a question's face stats."""
    if not isinstance(face, dict) or not face.get("vision_enabled"):
        return ""
    if not face.get("samples"):
        return ""
    parts = []
    if face.get("face_present_pct") is not None:
        parts.append(f"visible {face['face_present_pct']:g}% of the time")
    if face.get("centered_pct") is not None:
        parts.append(f"centered {face['centered_pct']:g}%")
    if face.get("eye_contact_pct") is not None:
        parts.append(f"facing camera {face['eye_contact_pct']:g}%")
    return " · ".join(parts)


def render(
    result: Dict[str, Any],
    profile: Dict[str, Any],
    qa_history: List[Dict[str, Any]],
) -> None:
    """Render the full results dashboard."""
    score = result.get("overall_score", 0)

    theme.score_hero(score, result.get("summary", ""), len(qa_history))

    if result.get("_meta", {}).get("was_repaired"):
        st.caption(
            "Some of this report was reconstructed because the model's output was "
            "incomplete. Scores are approximate."
        )

    # ---- Rubric ---------------------------------------------------------
    theme.section("Rubric breakdown")
    # "Ranked" leads: it is the more actionable view and, unlike the radar, it
    # stays readable all the way down to a phone viewport.
    bars_tab, radar_tab = st.tabs(["Ranked", "Radar"])
    with bars_tab:
        st.plotly_chart(_bars(result.get("rubric", {})), use_container_width=True,
                        config={"displayModeBar": False, "responsive": True})
        st.caption("Sorted weakest first — the top of this chart is where to focus.")
    with radar_tab:
        st.plotly_chart(_radar(result.get("rubric", {})), use_container_width=True,
                        config={"displayModeBar": False, "responsive": True})

    # ---- Strengths / improvements ---------------------------------------
    strengths = result.get("strengths") or []
    improvements = result.get("improvements") or []
    if strengths or improvements:
        left, right = st.columns(2, gap="large")
        with left:
            theme.section("What worked")
            if strengths:
                for item in strengths:
                    st.markdown(f"- {item}")
            else:
                st.caption("No specific strengths were identified.")
        with right:
            theme.section("What to fix")
            if improvements:
                for item in improvements:
                    st.markdown(f"- {item}")
            else:
                st.caption("No specific improvements were identified.")

    # ---- Per-question ----------------------------------------------------
    notes = result.get("question_notes") or []
    if notes:
        theme.section("Question by question")
        for index, note in enumerate(notes):
            question = (note.get("question") or "").strip() or f"Question {index + 1}"
            preview = question if len(question) <= 74 else question[:74].rsplit(" ", 1)[0] + "…"

            with st.expander(f"**Q{index + 1}.**  {preview}"):
                if (note.get("question") or "").strip():
                    st.markdown(f"**Question:** {note['question']}")

                # Prefer the real transcript over the model's excerpt.
                answer = ""
                if index < len(qa_history):
                    answer = str(qa_history[index].get("a", "")).strip()
                answer = answer or (note.get("answer_excerpt") or "").strip()
                if answer:
                    st.markdown("**Your answer**")
                    st.markdown(f"> {answer}")

                if (note.get("diagnosis") or "").strip():
                    st.markdown("**Diagnosis**")
                    st.markdown(note["diagnosis"])

                fixes = note.get("fixes") or []
                if fixes:
                    st.markdown("**How to improve**")
                    for fix in fixes:
                        st.markdown(f"- {fix}")

                if index < len(qa_history):
                    voice = qa_history[index].get("voice") or {}
                    meta = []
                    if voice.get("words"):
                        meta.append(f"{voice['words']} words")
                    if voice.get("stt_engine"):
                        meta.append(f"via {voice['stt_engine']}")
                    face_line = _face_line(qa_history[index].get("face") or {})
                    if face_line:
                        meta.append(face_line)
                    if meta:
                        st.caption(" · ".join(meta))

    # ---- Delivery notes ---------------------------------------------------
    stats = result.get("advanced_stats") or {}
    if any(stats.values()):
        theme.section("Delivery notes")
        st.caption(
            "These come from noisy browser-side heuristics and are deliberately "
            "weighted lightly in your score."
        )
        for key, label in (("voice", "Voice"), ("face", "Camera"), ("other", "Other")):
            if stats.get(key):
                st.markdown(f"**{label}** — {stats[key]}")

    # ---- Download ----------------------------------------------------------
    theme.section("Take it with you")
    printable = build_printable_html(result, profile, qa_history)
    slug = (profile.get("job_field") or "interview").lower().replace(" ", "-")[:40]

    st.download_button(
        "Printable report (PDF)",
        data=printable,
        file_name=f"intereview-{slug}.html",
        mime="text/html",
        use_container_width=True,
    )
    st.caption("Open the downloaded file and use your browser's Print to PDF option.")
