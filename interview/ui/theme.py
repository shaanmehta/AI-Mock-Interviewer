"""Design system.

A single stylesheet plus a small set of reusable primitives, replacing the ad
hoc inline HTML strings that were previously duplicated across ``app.py``.
Colours are driven by the ``.streamlit/config.toml`` theme so light and dark
both work, and every size uses relative units so the layout survives a phone
viewport.
"""

from __future__ import annotations

import html
from typing import Iterable, List, Optional, Tuple

import streamlit as st

BRAND = "#4f7cff"
BRAND_SOFT = "rgba(79, 124, 255, 0.14)"

_CSS = """
<style>
:root {
  --iv-brand: #4f7cff;
  --iv-brand-2: #6d92ff;
  --iv-surface: rgba(127, 140, 170, 0.07);
  --iv-surface-2: rgba(127, 140, 170, 0.12);
  --iv-border: rgba(127, 140, 170, 0.20);
  --iv-radius: 16px;
  --iv-shadow: 0 1px 2px rgba(0,0,0,.16), 0 8px 28px -12px rgba(0,0,0,.42);
}

/* Tighten Streamlit's default chrome without hiding useful affordances.
   The generous top padding keeps the stage progress bar clear of the floating
   "Settings »" pill, which overlays the top-left corner. */
.block-container { padding-top: 4.6rem; padding-bottom: 4.5rem; max-width: 1060px; }
#MainMenu, footer { visibility: hidden; }

/* ---- Typography ---- */
.iv-title {
  font-size: clamp(1.75rem, 4.4vw, 2.35rem); font-weight: 700;
  letter-spacing: -0.026em; margin: 0 0 .35rem; line-height: 1.15;
}
.iv-sub { font-size: .97rem; opacity: .68; margin: 0 0 1.7rem; line-height: 1.55; max-width: 62ch; }
.iv-section {
  font-size: 1.02rem; font-weight: 650; letter-spacing: -0.012em;
  margin: 1.9rem 0 .8rem; display: flex; align-items: center; gap: .5rem;
}
.iv-section::after {
  content: ""; flex: 1 1 auto; height: 1px;
  background: linear-gradient(90deg, var(--iv-border), transparent);
}

/* ---- Stage progress bar ---- */
.iv-prog { margin: 0 0 2rem; }
.iv-prog-labels {
  display: flex; justify-content: space-between; gap: .5rem;
  margin-bottom: .55rem; font-size: .76rem; letter-spacing: .045em;
  text-transform: uppercase; font-weight: 600;
}
.iv-prog-labels span { opacity: .38; transition: opacity .25s ease, color .25s ease; flex: 0 0 auto; }
.iv-prog-labels span.done { opacity: .62; }
.iv-prog-labels span.now { opacity: 1; color: var(--iv-brand); }
.iv-prog-track {
  height: 6px; border-radius: 999px; overflow: hidden;
  background: rgba(127, 140, 170, 0.18);
}
.iv-prog-fill {
  height: 100%; border-radius: 999px;
  background: linear-gradient(90deg, var(--iv-brand), var(--iv-brand-2));
  transition: width .45s cubic-bezier(.4, 0, .2, 1);
}

/* ---- Pills ---- */
.iv-pillrow { display: flex; flex-wrap: wrap; gap: .45rem; margin-bottom: 1.1rem; }
.iv-pill {
  display: inline-flex; align-items: baseline; gap: .4rem;
  padding: .36rem .78rem; border-radius: 999px;
  border: 1px solid var(--iv-border); background: var(--iv-surface);
  font-size: .82rem; white-space: nowrap;
}
.iv-pill b { font-weight: 500; opacity: .55; }
.iv-pill.brand {
  border-color: rgba(79,124,255,.38); background: rgba(79,124,255,.12);
}

/* ---- Cards ---- */
.iv-card {
  padding: 1.2rem 1.35rem; border-radius: var(--iv-radius);
  border: 1px solid var(--iv-border); background: var(--iv-surface);
}
.iv-question {
  padding: 1.45rem 1.6rem; border-radius: var(--iv-radius);
  border: 1px solid rgba(79,124,255,.26);
  background: linear-gradient(135deg, rgba(79,124,255,.115), rgba(79,124,255,.035));
  box-shadow: var(--iv-shadow);
  font-size: clamp(1.05rem, 2.4vw, 1.24rem); line-height: 1.5; font-weight: 500;
}
.iv-question .who {
  display: block; font-size: .68rem; text-transform: uppercase;
  letter-spacing: .13em; opacity: .5; margin-bottom: .5rem; font-weight: 700;
}

/* ---- Score hero ---- */
.iv-hero {
  display: flex; align-items: center; gap: 1.6rem; flex-wrap: wrap;
  padding: 1.6rem 1.8rem; border-radius: 20px;
  border: 1px solid var(--iv-border); background: var(--iv-surface);
  box-shadow: var(--iv-shadow);
}
.iv-score { font-size: clamp(2.9rem, 9vw, 4.1rem); font-weight: 800; line-height: 1; letter-spacing: -.035em; }
.iv-score span { font-size: .32em; font-weight: 600; opacity: .45; margin-left: .1em; }
.iv-verdict { font-size: 1.04rem; font-weight: 650; margin-bottom: .22rem; letter-spacing: -.01em; }
.iv-hero .meta { flex: 1 1 250px; min-width: 0; }
.iv-hero .desc { font-size: .89rem; opacity: .72; line-height: 1.55; }

/* ---- Status chip ---- */
.iv-status {
  display: inline-flex; align-items: center; gap: .48rem; font-size: .8rem;
  padding: .32rem .74rem; border-radius: 999px; border: 1px solid var(--iv-border);
  font-weight: 500;
}
.iv-status .dot { width: .5rem; height: .5rem; border-radius: 50%; background: #7a8499; flex: 0 0 auto; }
.iv-status.ok   { border-color: rgba(51,209,122,.40); background: rgba(51,209,122,.10); }
.iv-status.ok .dot { background: #33d17a; box-shadow: 0 0 0 3px rgba(51,209,122,.16); }
.iv-status.warn { border-color: rgba(240,173,78,.42); background: rgba(240,173,78,.10); }
.iv-status.warn .dot { background: #f0ad4e; }
.iv-status.bad  { border-color: rgba(239,95,95,.42); background: rgba(239,95,95,.10); }
.iv-status.bad .dot { background: #ef5f5f; }
.iv-status.off .dot { background: #7a8499; }

/* ---- Buttons ---- */
.stButton > button, .stDownloadButton > button {
  border-radius: 11px; font-weight: 600; letter-spacing: -.005em;
  transition: transform .07s ease, filter .16s ease, border-color .16s ease;
  min-height: 2.7rem;
}
.stButton > button:hover, .stDownloadButton > button:hover { filter: brightness(1.09); }
.stButton > button:active, .stDownloadButton > button:active { transform: translateY(1px); }
.stButton > button[kind="primary"] {
  background: linear-gradient(180deg, var(--iv-brand-2), var(--iv-brand));
  border: none; box-shadow: 0 2px 14px -4px rgba(79,124,255,.65);
}

/* Hint line under a primary action. */
.iv-btnhint {
  text-align: center; font-size: .79rem; opacity: .5;
  margin: .5rem 0 0; letter-spacing: .01em;
}

/* ---- Inputs ---- */
.stTextInput input, .stTextArea textarea, .stSelectbox div[data-baseweb="select"] > div {
  border-radius: 10px !important;
}
.stTextArea textarea { line-height: 1.55; }

/* ---- Expanders ---- */
[data-testid="stExpander"] details {
  border-radius: 12px; border: 1px solid var(--iv-border);
  background: var(--iv-surface); overflow: hidden;
}

/* ---- Sidebar: label the collapsed toggle so "»" isn't a mystery ---- */
[data-testid="stExpandSidebarButton"] {
  width: auto !important; padding: .3rem .7rem .3rem .5rem !important;
  border-radius: 999px !important;
  border: 1px solid var(--iv-border) !important;
  background: var(--iv-surface) !important;
  gap: .3rem;
}
[data-testid="stExpandSidebarButton"]::after {
  content: "Settings"; font-size: .82rem; font-weight: 600;
  letter-spacing: -.005em; white-space: nowrap; opacity: .85;
}
[data-testid="stExpandSidebarButton"]:hover { background: var(--iv-surface-2) !important; }
[data-testid="stSidebarContent"] { padding-top: .4rem; }

/* ---- Divider rhythm ---- */
hr { margin: 1.9rem 0 !important; border-color: var(--iv-border) !important; }

/* ---- Mobile ---- */
@media (max-width: 640px) {
  .block-container { padding-left: 1rem; padding-right: 1rem; padding-top: 4.2rem; }
  .iv-hero { gap: 1rem; padding: 1.25rem; }
  .iv-question { padding: 1.15rem 1.2rem; }
  .iv-prog-labels { font-size: .66rem; letter-spacing: .03em; }
  .iv-sub { margin-bottom: 1.3rem; }
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


def pills(items: Iterable[Tuple[str, str]], *, brand_first: bool = True) -> None:
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


def button_hint(text: str) -> None:
    """A small centered hint directly beneath a primary action."""
    st.markdown(f'<p class="iv-btnhint">{_esc(text)}</p>', unsafe_allow_html=True)


def progress(labels: List[str], current: int) -> None:
    """A single continuous progress bar spanning the flow.

    ``current`` is a 0-based stage index, so the first stage shows an empty bar
    and the last shows a full one. No percentage is displayed — the bar itself
    is the whole signal.
    """
    total = max(1, len(labels) - 1)
    fraction = max(0.0, min(1.0, current / total))

    label_html = "".join(
        f'<span class="{"now" if i == current else "done" if i < current else ""}">'
        f"{_esc(label)}</span>"
        for i, label in enumerate(labels)
    )

    st.markdown(
        f"""
        <div class="iv-prog">
          <div class="iv-prog-labels">{label_html}</div>
          <div class="iv-prog-track">
            <div class="iv-prog-fill" style="width:{fraction * 100:.4g}%"></div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def verdict_for(score: float) -> Tuple[str, str]:
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
