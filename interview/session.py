"""Per-user session state and soft abuse guards.

Every mutable value the app touches is created here and stored in
``st.session_state``, which Streamlit scopes to a single browser session. There
are no module-level mutable globals anywhere in this package — the previous
``vision._global_vision`` singleton was shared by every concurrent visitor in
the same server process and has been removed.

The guards below are deliberately soft. This service cannot be billed, but a
single script hammering it can still burn the shared free-tier quota and make
the site unusable for everyone else.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import streamlit as st

from interview import analytics
from interview.config import settings
from interview.vision import VisionAggregator, VisionStatus

STAGES = ["setup", "media", "question", "finished"]
STEP_LABELS = ["Setup", "Recording", "Interview", "Results"]

#: Minimum seconds between provider calls from one session.
MIN_CALL_INTERVAL = 1.5

#: Hard cap on a single answer. Prevents a pasted novel from blowing the free
#: tier's token limit (which would fail the whole interview) or ballooning
#: session memory on a 512 MB instance.
MAX_ANSWER_CHARS = 6000


def init_state() -> None:
    """Create every per-session key exactly once."""
    ss = st.session_state

    ss.setdefault("stage", "setup")
    ss.setdefault("profile", {})
    ss.setdefault("question_idx", 0)
    ss.setdefault("current_question", "")
    ss.setdefault("qa", [])
    ss.setdefault("question_error", None)
    ss.setdefault("final_result", None)
    ss.setdefault("scoring_error", None)

    # Media / capture
    ss.setdefault("media_mode", "mic")
    ss.setdefault("stt_nonce", {})
    ss.setdefault("stt_mode", "record_transcribe")  # only capture path
    ss.setdefault("audio_enabled", True)
    ss.setdefault("voice_gender", "female")
    ss.setdefault("transcribing", False)

    # Vision — per user, never shared
    ss.setdefault("vision", VisionAggregator())
    ss.setdefault("vision_status", VisionStatus.DISABLED)
    ss.setdefault("vision_last_batch", -1)

    # Timer
    ss.setdefault("timer_seconds", 60)
    ss.setdefault("timer_start", None)
    ss.setdefault("timer_question_idx", None)
    ss.setdefault("timer_expired", False)

    # BYO key
    ss.setdefault("user_api_key", "")
    ss.setdefault("user_provider", settings.provider)

    # Guards
    ss.setdefault("interviews_started", 0)
    ss.setdefault("regenerations", 0)
    ss.setdefault("last_call_at", 0.0)

    # Analytics — a random id per browser session, not tied to a person.
    if "analytics_id" not in ss:
        ss.analytics_id = analytics.new_session_id()
        track(analytics.SITE_OPENED)


# --------------------------------------------------------------------------
# Analytics
# --------------------------------------------------------------------------


def track(event: str, **props: Any) -> None:
    """Record an analytics event for this session. Never raises."""
    try:
        analytics.track(st.session_state.get("analytics_id", "unknown"), event, **props)
    except Exception:  # noqa: BLE001 - analytics must never break a page
        pass


# --------------------------------------------------------------------------
# Credentials
# --------------------------------------------------------------------------


def has_working_key() -> bool:
    """True when this session can actually reach a provider.

    Either the deployment ships a real shared key, or the visitor supplied one.
    """
    return bool(settings.has_shared_key or using_own_key())


def credentials() -> Dict[str, Any]:
    """Provider + key for this session, preferring the visitor's own key."""
    ss = st.session_state
    key = (ss.get("user_api_key") or "").strip()
    return {
        "provider": ss.get("user_provider") or settings.provider,
        "api_key": key or None,
    }


def using_own_key() -> bool:
    return bool((st.session_state.get("user_api_key") or "").strip())


# --------------------------------------------------------------------------
# Soft guards
# --------------------------------------------------------------------------


def throttle() -> None:
    """Space out provider calls from a single session."""
    ss = st.session_state
    if using_own_key():
        return  # their quota, their problem
    elapsed = time.monotonic() - float(ss.get("last_call_at", 0.0))
    if 0 < elapsed < MIN_CALL_INTERVAL:
        time.sleep(MIN_CALL_INTERVAL - elapsed)
    ss.last_call_at = time.monotonic()


def can_start_interview() -> bool:
    if using_own_key():
        return True
    return int(st.session_state.get("interviews_started", 0)) < settings.max_interviews_per_session


def note_interview_started() -> None:
    st.session_state.interviews_started = int(st.session_state.get("interviews_started", 0)) + 1


def can_regenerate() -> bool:
    if using_own_key():
        return True
    return int(st.session_state.get("regenerations", 0)) < settings.max_regenerations_per_session


def note_regeneration() -> None:
    st.session_state.regenerations = int(st.session_state.get("regenerations", 0)) + 1


# --------------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------------


def start_new_interview(profile: Dict[str, Any]) -> None:
    """Reset everything that belongs to a single interview run."""
    ss = st.session_state

    ss.profile = profile
    ss.question_idx = 0
    ss.current_question = ""
    ss.qa = []
    ss.question_error = None
    ss.final_result = None
    ss.scoring_error = None

    ss.stt_nonce = {}
    ss.vision = VisionAggregator()
    ss.vision_status = VisionStatus.DISABLED
    ss.vision_last_batch = -1

    ss.timer_start = None
    ss.timer_question_idx = None
    ss.timer_expired = False

    ss.transcribing = False

    # Clear per-question answer buffers from any previous run.
    stale = [
        k for k in list(ss.keys())
        if str(k).startswith(("answer_", "answer_rev_", "answer_box_"))
    ]
    for key in stale:
        del ss[key]


def reset_to_setup() -> None:
    st.session_state.stage = "setup"
    start_new_interview({})


#: Recorded when the clock runs out with nothing captured. Flagged explicitly
#: rather than stored as an ordinary answer: a transcript full of these means
#: capture failed, not that the candidate performed badly.
NO_ANSWER_PLACEHOLDER = "(No answer was given before time ran out.)"


def record_answer(
    question: str, answer: str, face_stats: Dict[str, Any]
) -> None:
    """Append one completed Q/A to the transcript."""
    answer = (answer or "")[:MAX_ANSWER_CHARS]
    words = len(answer.split())
    st.session_state.qa.append(
        {
            "q": (question or "")[:1000],
            "a": answer,
            "answered": answer.strip() != NO_ANSWER_PLACEHOLDER and bool(answer.strip()),
            "voice": {
                "stt_engine": st.session_state.get("stt_mode", "record_transcribe"),
                "words": words,
                "chars": len(answer),
            },
            "face": face_stats,
        }
    )


def transcript() -> List[Dict[str, Any]]:
    return list(st.session_state.get("qa", []))


def answered_count(qa_history: Optional[List[Dict[str, Any]]] = None) -> int:
    """How many questions actually captured a real answer."""
    rows = qa_history if qa_history is not None else st.session_state.get("qa", [])
    total = 0
    for item in rows:
        if not isinstance(item, dict):
            continue
        if item.get("answered") is not None:
            if item["answered"]:
                total += 1
            continue
        # Tolerate transcripts recorded before "answered" existed.
        text = str(item.get("a", "")).strip()
        if text and text != NO_ANSWER_PLACEHOLDER:
            total += 1
    return total
