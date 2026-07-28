"""Answers must survive the clock.

Answers were previously stored under the same session key that backed the
``st.text_area`` widget. Streamlit garbage-collects widget-backed keys whose
widget did not render in the last completed run, and the timer fragment can
discard a run part-way by requesting an app-level rerun. A transcription that
landed near the buzzer was therefore dropped, and the question was recorded as
unanswered - which is how a good interview scored 37/100.
"""

from __future__ import annotations

from streamlit.testing.v1 import AppTest

from interview.session import NO_ANSWER_PLACEHOLDER, answered_count


def _interview_at_question(question: str = "Tell me about a project.") -> AppTest:
    at = AppTest.from_file("app.py", default_timeout=30)
    at.session_state["stage"] = "question"
    at.session_state["profile"] = {
        "job_field": "Accounting",
        "experience_level": "Student / New Grad",
        "n_questions": 2,
    }
    at.session_state["current_question"] = question
    at.session_state["question_idx"] = 0
    at.session_state["qa"] = []
    at.session_state["media_mode"] = "mic"
    return at


def test_answer_key_is_not_the_text_area_widget_key():
    """The regression guard: canonical storage must not be widget-backed."""
    at = _interview_at_question()
    at.session_state["answer_0"] = "A carefully considered answer."
    at.run()

    widget_keys = {w.key for w in at.text_area}
    assert "answer_0" not in widget_keys, (
        "answer_0 is bound to the text area again; Streamlit will garbage-collect "
        "it when a run is discarded and the answer will be lost"
    )
    assert at.session_state["answer_0"] == "A carefully considered answer."


def test_canonical_answer_is_submitted_not_the_widget_value():
    at = _interview_at_question()
    at.session_state["answer_0"] = "My real answer about reconciliations."
    at.run()

    [b for b in at.button if b.key == "submit_answer_0"][0].click().run()

    qa = at.session_state["qa"]
    assert len(qa) == 1
    assert qa[0]["a"] == "My real answer about reconciliations."
    assert qa[0]["answered"] is True
    assert answered_count(qa) == 1


def test_auto_submit_keeps_a_late_transcription():
    """Timer expiry must record a captured answer, not the no-answer placeholder."""
    at = _interview_at_question()
    at.session_state["answer_0"] = "Transcribed just before the buzzer."
    at.session_state["timer_expired"] = True
    at.run()

    qa = at.session_state["qa"]
    assert len(qa) == 1
    assert qa[0]["a"] == "Transcribed just before the buzzer."
    assert qa[0]["a"] != NO_ANSWER_PLACEHOLDER
    assert answered_count(qa) == 1


def test_auto_submit_with_nothing_captured_is_flagged_unanswered():
    at = _interview_at_question()
    at.session_state["timer_expired"] = True
    at.run()

    qa = at.session_state["qa"]
    assert len(qa) == 1
    assert qa[0]["a"] == NO_ANSWER_PLACEHOLDER
    assert qa[0]["answered"] is False
    assert answered_count(qa) == 0


def test_typing_is_mirrored_into_the_canonical_store():
    at = _interview_at_question()
    at.run()
    box = at.text_area[0]
    box.set_value("Typed rather than spoken.").run()
    assert at.session_state["answer_0"] == "Typed rather than spoken."


def test_answers_do_not_leak_between_questions():
    at = _interview_at_question()
    at.session_state["answer_0"] = "Answer to question one."
    at.run()
    [b for b in at.button if b.key == "submit_answer_0"][0].click().run()

    assert at.session_state["question_idx"] == 1
    # Streamlit's SafeSessionState has no .get(), so probe membership directly.
    assert at.session_state["answer_0"] == "Answer to question one."
    assert not str(at.session_state["answer_1"] or "").strip()


# ---- Grace period ---------------------------------------------------------
# Stopping a recording on the buzzer still needs a round trip to transcribe it.
# Expiring the question at exactly 0:00 threw that answer away.


def _timer_phase(elapsed: float, *, transcribing: bool = False, limit: int = 60):
    """Run the timer fragment against a fake clock; return (label, expired)."""
    from unittest.mock import patch

    import streamlit as st

    import app as app_module

    class _State(dict):
        def __getattr__(self, key):
            return self[key]

        def __setattr__(self, key, value):
            self[key] = value

    state = _State(
        timer_start=0.0, timer_seconds=limit,
        timer_expired=False, transcribing=transcribing,
    )
    shown: dict = {}
    with patch.object(app_module.time, "time", return_value=elapsed), \
            patch.object(st, "session_state", state), \
            patch.object(st, "progress", lambda v, text="": shown.update(text=text)), \
            patch.object(st, "rerun", lambda **kw: None):
        app_module.render_timer.__wrapped__()
    return shown.get("text", ""), state["timer_expired"]


def test_countdown_runs_then_enters_a_grace_window():
    assert "0:30 left" in _timer_phase(30)[0]
    assert _timer_phase(30)[1] is False

    label, expired = _timer_phase(60)
    assert "saving your answer (5s)" in label
    assert expired is False, "expired at 0:00 with no grace"


def test_question_expires_only_after_the_grace_window():
    assert _timer_phase(64.9)[1] is False
    assert _timer_phase(65.1)[1] is True


def test_an_in_flight_transcription_is_never_cut_off():
    """The exact failure: stop recording near the buzzer, answer lost."""
    assert _timer_phase(70, transcribing=True)[1] is False
    assert _timer_phase(600, transcribing=True)[1] is False
