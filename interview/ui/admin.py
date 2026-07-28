"""Owner-only analytics dashboard.

Reached at ``?admin=1`` and gated by the ``ANALYTICS_PASSWORD`` secret. When
that secret is unset the dashboard refuses to open at all, so a deployment can
never accidentally expose usage data publicly.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import streamlit as st

from interview import analytics
from interview.config import get_secret
from interview.ui import theme


def _check_password() -> bool:
    """Gate the dashboard. Returns True only once the password matches."""
    expected = get_secret("ANALYTICS_PASSWORD")

    if not expected:
        st.error(
            "Analytics are disabled: no `ANALYTICS_PASSWORD` is configured for "
            "this deployment.",
        )
        st.caption(
            "Set ANALYTICS_PASSWORD in your host's environment to enable this page."
        )
        return False

    if st.session_state.get("admin_authed"):
        return True

    theme.title("Analytics", "Private dashboard — not visible to visitors.")
    with st.form("admin_login"):
        supplied = st.text_input("Password", type="password")
        if st.form_submit_button("Sign in", type="primary", use_container_width=True):
            # Constant-time-ish compare; the value is a shared secret, not a hash.
            if supplied and supplied == expected:
                st.session_state.admin_authed = True
                st.rerun()
            else:
                st.error("Incorrect password.")
    return False


def _bar_counts(title: str, counts: Dict[str, int]) -> None:
    if not counts:
        st.caption(f"No {title.lower()} recorded yet.")
        return
    st.bar_chart(counts, horizontal=True, height=max(140, 34 * len(counts)))


def render() -> None:
    """Render the admin page. Assumes ``?admin`` was present in the URL."""
    if not _check_password():
        return

    theme.title("Analytics", "Private dashboard — not visible to visitors.")

    col_refresh, col_backend = st.columns([1, 2])
    with col_refresh:
        if st.button("Refresh", use_container_width=True):
            st.rerun()
    with col_backend:
        backend = analytics.backend_name()
        if backend == "supabase":
            theme.status_chip("Storage: Supabase (durable)", "ok")
        elif backend == "sqlite":
            theme.status_chip("Storage: SQLite (resets on restart)", "warn")
        else:
            theme.status_chip(f"Storage: {backend} (resets on restart)", "bad")

    rows: List[Dict[str, Any]] = analytics.fetch(limit=20000)
    stats = analytics.summarize(rows)

    if not rows:
        st.info(
            "No events recorded yet. Open the site in another browser to "
            "generate some.",
        )
        return

    # ---- Funnel -----------------------------------------------------------
    theme.section("Funnel")
    col_a, col_b, col_c = st.columns(3)
    col_a.metric("Opened the site", stats["opened"])
    col_b.metric(
        "Started an interview",
        stats["started"],
        delta=f"{stats['start_rate']}% of visitors",
        delta_color="off",
    )
    col_c.metric(
        "Finished an interview",
        stats["completed"],
        delta=f"{stats['completion_rate']}% of starters",
        delta_color="off",
    )

    col_d, col_e, col_f = st.columns(3)
    col_d.metric("Questions answered", stats["answers"])
    col_e.metric(
        "Average score",
        stats["avg_score"] if stats["avg_score"] is not None else "—",
    )
    col_f.metric("Errors surfaced", stats["errors"])

    # ---- Breakdowns -------------------------------------------------------
    theme.section("What people are practising")
    _bar_counts("Job fields", stats["by_field"])

    col_media, col_stt = st.columns(2)
    with col_media:
        st.caption("Recording mode")
        _bar_counts("Recording modes", stats["by_media"])
    with col_stt:
        st.caption("Speech-to-text mode")
        _bar_counts("Speech-to-text modes", stats["by_stt"])

    if stats["scores"]:
        theme.section("Score distribution")
        buckets: Dict[str, int] = {}
        for score in stats["scores"]:
            low = int(score // 10) * 10
            buckets[f"{low}-{low + 9}"] = buckets.get(f"{low}-{low + 9}", 0) + 1
        st.bar_chart(dict(sorted(buckets.items())), height=220)

    # ---- Raw events -------------------------------------------------------
    theme.section("Recent events")
    recent = list(reversed(rows))[:200]
    st.dataframe(
        [
            {
                "time": r.get("ts", ""),
                "event": r.get("event", ""),
                "session": str(r.get("session_id", ""))[:8],
                "details": (r.get("props") or "{}")[:120],
            }
            for r in recent
        ],
        use_container_width=True,
        hide_index=True,
        height=380,
    )

    st.download_button(
        "Export all events (JSON)",
        data=json.dumps(rows, indent=2),
        file_name="intereview-analytics.json",
        mime="application/json",
        use_container_width=True,
    )

    st.divider()
    if st.button("Sign out", use_container_width=True):
        st.session_state.admin_authed = False
        st.rerun()
