"""Design system.

A single stylesheet plus a small set of reusable primitives, replacing the ad
hoc inline HTML strings that were previously duplicated across ``app.py``.
Colours are driven by the ``.streamlit/config.toml`` theme so light and dark
both work, and every size uses relative units so the layout survives a phone
viewport.
"""

from __future__ import annotations

import html
from typing import Iterable, Optional

import streamlit as st

BRAND = "#4f7cff"
BRAND_SOFT = "rgba(79, 124, 255, 0.14)"

_CSS = """
<style>
:root {
  --iv-brand: #4f7cff;
  --iv-surface: rgba(127, 140, 170, 0.08);
  --iv-border: rgba(127, 140, 170, 0.22);
  --iv-radius: 14px;
}

/* Tighten Streamlit's default chrome without hiding useful affordances. */
.block-container { padding-top: 2.4rem; padding-bottom: 4rem; max-width: 1100px; }
#MainMenu, footer { visibility: hidden; }

/* ---- Typography ---- */
.iv-title { font-size: clamp(1.6rem, 4vw, 2.2rem); font-weight: 700; letter-spacing: -0.02em; margin: 0 0 .2rem; }
.iv-sub { font-size: .96rem; opacity: .72; margin: 0 0 1.4rem; line-height: 1.5; }
.iv-section { font-size: 1.05rem; font-weight: 650; letter-spacing: -0.01em; margin: 1.6rem 0 .7rem; display: flex; align-items: center; gap: .5rem; }

/* ---- Pills ---- */
.iv-pillrow { display: flex; flex-wrap: wrap; gap: .45rem; margin-bottom: .3rem; }
.iv-pill {
  display: inline-flex; align-items: center; gap: .38rem;
  padding: .34rem .72rem; border-radius: 999px;
  border: 1px solid var(--iv-border); background: var(--iv-surface);
  font-size: .82rem; white-space: nowrap;
}
.iv-pill b { font-weight: 650; opacity: .62; font-weight: 500; }
.iv-pill.brand { border-color: rgba(79,124,255,.4); background: rgba(79,124,255,.13); }

/* ---- Cards ---- */
.iv-card {
  padding: 1.15rem 1.3rem; border-radius: var(--iv-radius);
  border: 1px solid var(--iv-border); background: var(--iv-surface);
}
.iv-question {
  padding: 1.35rem 1.5rem; border-radius: var(--iv-radius);
  border: 1px solid rgba(79,124,255,.30);
  background: linear-gradient(135deg, rgba(79,124,255,.13), rgba(79,124,255,.05));
  font-size: clamp(1.02rem, 2.4vw, 1.2rem); line-height: 1.5; font-weight: 500;
}
.iv-question .who {
  display: block; font-size: .72rem; text-transform: uppercase;
  letter-spacing: .09em; opacity: .6; margin-bottom: .45rem; font-weight: 700;
}

/* ---- Score hero ---- */
.iv-hero { display: flex; align-items: center; gap: 1.5rem; flex-wrap: wrap;
  padding: 1.5rem 1.7rem; border-radius: 18px;
  border: 1px solid var(--iv-border); background: var(--iv-surface); }
.iv-score { font-size: clamp(2.8rem, 9vw, 4rem); font-weight: 800; line-height: 1; letter-spacing: -.03em; }
.iv-score span { font-size: .34em; font-weight: 600; opacity: .5; margin-left: .12em; }
.iv-verdict { font-size: 1.02rem; font-weight: 650; margin-bottom: .18rem; }
.iv-hero .meta { flex: 1 1 240px; min-width: 0; }
.iv-hero .desc { font-size: .88rem; opacity: .72; line-height: 1.5; }

/* ---- Status chip ---- */
.iv-status { display: inline-flex; align-items: center; gap: .45rem; font-size: .8rem;
  padding: .3rem .7rem; border-radius: 999px; border: 1px solid var(--iv-border); }
.iv-status .dot { width: .5rem; height: .5rem; border-radius: 50%; background: #7a8499; }
.iv-status.ok    { border-color: rgba(51,209,122,.42); background: rgba(51,209,122,.11); }
.iv-status.ok .dot { background: #33d17a; }
.iv-status.warn  { border-color: rgba(240,173,78,.45); background: rgba(240,173,78,.11); }
.iv-status.warn .dot { background: #f0ad4e; }
.iv-status.bad   { border-color: rgba(239,95,95,.45); background: rgba(239,95,95,.11); }
.iv-status.bad .dot { background: #ef5f5f; }
.iv-status.off .dot { background: #7a8499; }

/* ---- Step rail ---- */
.iv-steps { display: flex; gap: .4rem; margin-bottom: 1.5rem; flex-wrap: wrap; }
.iv-step { flex: 1 1 0; min-width: 72px; height: .28rem; border-radius: 999px;
  background: rgba(127,140,170,.22); position: relative; }
.iv-step.done { background: var(--iv-brand); }
.iv-step.now  { background: linear-gradient(90deg, var(--iv-brand) 55%, rgba(127,140,170,.22) 55%); }
.iv-step .lbl { position: absolute; top: .55rem; left: 0; font-size: .7rem;
  opacity: .58; white-space: nowrap; }

/* ---- Buttons: make primary read as the single obvious action ---- */
.stButton > button { border-radius: 10px; font-weight: 600; }

/* ---- Mobile ---- */
@media (max-width: 640px) {
  .block-container { padding-left: 1rem; padding-right: 1rem; padding-top: 1.6rem; }
  .iv-hero { gap: 1rem; padding: 1.2rem; }
  .iv-step .lbl { display: none; }
  .iv-question { padding: 1.1rem 1.15rem; }
}
</style>
"""


def inject() -> None:
    """Inject the stylesheet once per script run."""
    st.markdown(_CSS, unsafe_allow_html=True)


def _esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def title(text: str, subtitle: Optional[str] = None) -> None:
    st.markdown(f'<h1 class="iv-title">{_esc(text)}</h1>', unsafe_allow_html=True)
    if subtitle:
        st.markdown(f'<p class="iv-sub">{_esc(subtitle)}</p>', unsafe_allow_html=True)


def section(text: str, icon: str = "") -> None:
    icon_html = f"<span>{_esc(icon)}</span>" if icon else ""
    st.markdown(
        f'<div class="iv-section">{icon_html}<span>{_esc(text)}</span></div>',
        unsafe_allow_html=True,
    )


def pills(items: Iterable[tuple[str, str]], *, brand_first: bool = True) -> None:
    """Render a row of label/value pills."""
    parts = []
    for index, (label, value) in enumerate(items):
        css = "iv-pill brand" if (brand_first and index == 0) else "iv-pill"
        parts.append(f'<span class="{css}"><b>{_esc(label)}</b> {_esc(value)}</span>')
    st.markdown(f'<div class="iv-pillrow">{"".join(parts)}</div>', unsafe_allow_html=True)


def question_card(text: str) -> None:
    st.markdown(
        f'<div class="iv-question"><span class="who">Interviewer</span>{_esc(text)}</div>',
        unsafe_allow_html=True,
    )


def status_chip(label: str, tone: str = "off") -> None:
    """``tone`` is one of ok / warn / bad / off."""
    st.markdown(
        f'<span class="iv-status {_esc(tone)}"><span class="dot"></span>{_esc(label)}</span>',
        unsafe_allow_html=True,
    )


def steps(labels: list[str], current: int) -> None:
    """A slim progress rail across the top of the flow."""
    parts = []
    for index, label in enumerate(labels):
        css = "done" if index < current else ("now" if index == current else "")
        parts.append(f'<div class="iv-step {css}"><span class="lbl">{_esc(label)}</span></div>')
    st.markdown(f'<div class="iv-steps">{"".join(parts)}</div>', unsafe_allow_html=True)


def verdict_for(score: float) -> tuple[str, str]:
    """Map an overall score to a hiring verdict and a colour."""
    if score >= 90:
        return "Exceptional — strong hire", "#33d17a"
    if score >= 75:
        return "Good — hire or hire-leaning", "#5ec27a"
    if score >= 60:
        return "Mixed — maybe", "#f0ad4e"
    return "Not ready yet", "#ef5f5f"


def score_hero(score: float, summary: str, n_questions: int) -> None:
    label, colour = verdict_for(score)
    st.markdown(
        f"""
        <div class="iv-hero">
          <div class="iv-score" style="color:{colour}">{int(round(score))}<span>/100</span></div>
          <div class="meta">
            <div class="iv-verdict" style="color:{colour}">{_esc(label)}</div>
            <div class="desc">{_esc(summary)}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
