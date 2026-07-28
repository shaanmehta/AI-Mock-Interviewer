"""InteReviewAI — spoken mock interviews with employer-style feedback.

Free to operate at any traffic level:

* questions + scoring  -> Groq / Gemini free tier (429s, never a bill)
* interviewer's voice   -> browser SpeechSynthesis (no server call)
* candidate's speech    -> recorded in-browser, transcribed by Groq Whisper
* face analytics        -> MediaPipe WASM in the visitor's browser

Flow: setup -> media choice -> per-question loop with timer -> scored report.
"""

from __future__ import annotations

import logging
import time

import streamlit as st

from interview import analytics, llm
from interview.config import settings
from interview.questions import generate_next_question
from interview.scoring import score_full_interview
from interview.session import (
    MAX_ANSWER_CHARS,
    NO_ANSWER_PLACEHOLDER,
    STEP_LABELS,
    answered_count,
    can_regenerate,
    can_start_interview,
    credentials,
    has_working_key,
    init_state,
    note_interview_started,
    note_regeneration,
    record_answer,
    reset_to_setup,
    start_new_interview,
    throttle,
    track,
    using_own_key,
)
from interview.transcribe import transcribe_audio
from interview.ui import admin, components, results, theme
from interview.vision import VisionStatus, disabled_face_stats

st.set_page_config(
    page_title="InteReviewAI",
    page_icon=theme.BLANK_ICON,  # blank tab icon: title only, no emoji
    layout="centered",
    initial_sidebar_state="collapsed",
)

theme.inject()
init_state()


# ==========================================================================
# Shared helpers
# ==========================================================================


def _friendly_error(exc: Exception) -> str:
    """Never leak a stack trace or raw provider text to a stranger."""
    if isinstance(exc, llm.LLMError):
        return exc.user_message
    return "Something went wrong on our side. Please try again in a moment."


def _ensure_question() -> bool:
    """Make sure ``current_question`` is populated. Returns True on success."""
    ss = st.session_state
    if ss.current_question:
        return True

    if not can_regenerate():
        ss.question_error = (
            "You've hit this session's question limit. Refresh the page to start fresh."
        )
        return False

    with st.spinner("The interviewer is thinking…"):
        try:
            throttle()
            note_regeneration()
            ss.current_question = generate_next_question(
                profile=ss.profile,
                qa_history=ss.qa,
                question_idx=ss.question_idx,
                n_questions=int(ss.profile.get("n_questions", 5)),
                **credentials(),
            )
            ss.question_error = None
            return True
        except Exception as exc:  # noqa: BLE001 - surfaced as a friendly message
            ss.question_error = _friendly_error(exc)
            track(analytics.PROVIDER_ERROR, stage="question", kind=type(exc).__name__)
            return False


# ==========================================================================
# Sidebar
# ==========================================================================


def render_sidebar() -> None:
    ss = st.session_state

    with st.sidebar:
        st.markdown("### Settings")

        ss.audio_enabled = st.toggle("Read questions aloud", value=ss.audio_enabled)

        if settings.allow_user_api_key:
            st.divider()
            st.markdown("#### Use your own API key")
            st.caption(
                "Optional. This site shares one free rate limit across everyone using "
                "it. Your own free key skips that queue entirely. It's kept in memory "
                "for this browser session only. Don't worry, it is never stored or "
                "logged!"
            )

            provider = st.selectbox(
                "Provider",
                options=list(llm.MODEL_CHOICES.keys()),
                index=list(llm.MODEL_CHOICES.keys()).index(ss.user_provider)
                if ss.user_provider in llm.MODEL_CHOICES
                else 0,
                key="user_provider",
            )
            st.text_input(
                "API key",
                type="password",
                key="user_api_key",
                placeholder="gsk_… (Groq)" if provider == "groq" else "AIza… (Gemini)",
            )

            link = (
                "https://console.groq.com/keys"
                if provider == "groq"
                else "https://aistudio.google.com/apikey"
            )
            st.caption(f"Get a free key: {link}")

            if using_own_key():
                st.success("Using your key — no shared rate limit.")

        st.divider()
        if not settings.has_shared_key and not using_own_key():
            st.warning(
                "This deployment has no shared API key configured, so you'll need to "
                "add your own above to run an interview.",
            )

        # A custom endpoint is a legitimate feature (local Ollama, a proxy), but
        # it must never be silent: a stub server returns canned questions and a
        # fixed score that are otherwise indistinguishable from real output.
        custom_endpoint = llm.custom_base_url()
        if custom_endpoint:
            st.warning(
                f"Using a custom AI endpoint (`{custom_endpoint}`) instead of Groq. "
                "Responses come from that server, not from the real model.",
            )
        st.caption(
            "Note: InteReviewAI gives practice feedback from an LLM. "
            "It is not a hiring decision."
        )


# ==========================================================================
# Stage 1 — setup
# ==========================================================================


def render_setup() -> None:
    theme.progress(STEP_LABELS, 0)
    theme.title(
        "InteReviewAI",
        "Practice a realistic spoken interview for any field, then get a scored, "
        "employer-style breakdown of how you did.",
    )

    # Without a provider there is no interview to run: every question and the
    # final report come from the model. Block here rather than letting someone
    # get three screens in and hit a wall.
    if not has_working_key():
        st.error(
            "This site can't run an interview right now — no AI provider is "
            "configured.",
        )
        st.markdown(
            "**You can start immediately with your own free key:**\n\n"
            "1. Open [console.groq.com/keys](https://console.groq.com/keys) and "
            "sign in with GitHub or Google.\n"
            "2. Create a key (it starts with `gsk_`). No credit card is needed.\n"
            "3. Paste it into **Settings -> Use your own API key** at the top left."
        )
        st.caption(
            "Your key stays in this browser session only — it is never stored or logged."
        )
        return

    with st.form("setup_form"):
        col_a, col_b = st.columns(2, gap="medium")
        with col_a:
            job_field = st.selectbox("Job field", options=settings.job_fields, index=0)
            company_size = st.selectbox(
                "Company size",
                ["Startup (1–10)", "Small (11–50)", "Mid (51–300)", "Large (301+)"],
            )
            experience_level = st.selectbox(
                "Your level",
                [
                    "Student / New Grad",
                    "Junior (1–2 yrs)",
                    "Intermediate (3–5 yrs)",
                    "Senior (6+ yrs)",
                ],
            )
        with col_b:
            interview_style = st.selectbox(
                "Interview style",
                [
                    "Balanced (Behavioral + Technical)",
                    "Behavioral-heavy",
                    "Technical-heavy",
                ],
            )
            personality = st.selectbox(
                "Interviewer personality",
                ["Friendly", "Neutral", "Fast-paced & high standards", "Skeptical (but fair)"],
                index=1,
            )
            n_questions = st.slider("Number of questions", 3, settings.max_questions, 5)

        submitted = st.form_submit_button(
            "Continue", use_container_width=True, type="primary"
        )

    if submitted:
        if not can_start_interview():
            st.error(
                "You've started a lot of interviews in this session. "
                "Refresh the page to continue.",
            )
            return

        start_new_interview(
            {
                "job_field": job_field,
                "company_size": company_size,
                "interview_style": interview_style,
                "personality": personality,
                "experience_level": experience_level,
                "n_questions": int(n_questions),
            }
        )
        note_interview_started()
        st.session_state.stage = "media"
        st.rerun()


# ==========================================================================
# Stage 2 — media choice
# ==========================================================================


def render_media_setup() -> None:
    ss = st.session_state

    theme.progress(STEP_LABELS, 1)
    theme.title(
        "Recording setup",
        "Choose how you'd like to be recorded. Both options are scored identically.",
    )

    # These settings must outlive this page, so the canonical value lives in a
    # plain (non-widget) session_state key and each widget seeds from it and
    # writes back. Binding the widget directly to the canonical key via `key=`
    # loses the value: Streamlit garbage-collects widget state once the widget
    # stops being rendered, which happens as soon as we leave this page.
    mode = st.radio(
        "Recording mode",
        options=["mic", "mic+cam"],
        format_func=lambda value: {
            "mic": "Microphone only",
            "mic+cam": "Microphone + camera",
        }[value],
        index=0 if ss.media_mode == "mic" else 1,
        horizontal=True,
    )
    ss.media_mode = mode

    if mode == "mic+cam":
        st.info(
            "Face analysis runs entirely in your browser — video frames never leave "
            "your device, and nothing is recorded or uploaded. Only a small summary "
            "(how often you were centered and facing the camera) is used.",
        )
    else:
        st.caption(
            "Microphone-only is a fully supported path. Camera analytics are a small "
            "bonus signal and never affect your score materially."
        )

    st.divider()

    col_timer, col_mode = st.columns(2, gap="large")
    with col_timer:
        ss.timer_seconds = st.select_slider(
            "Time limit per answer (seconds)",
            options=[30, 60, 90, 120],
            value=ss.timer_seconds,
        )

    with col_mode:
        st.markdown("**How your answer is captured**")
        st.caption(
            "Record your answer and it is transcribed for you. This works in every "
            "browser. You can also type your answer instead - typed answers are "
            "scored exactly the same."
        )

    st.divider()
    col_back, col_go = st.columns([1, 2])
    with col_back:
        if st.button("Back", use_container_width=True, key="media_back"):
            st.session_state.stage = "setup"
            st.rerun()
    with col_go:
        if st.button("Start interview", type="primary", use_container_width=True,
                     key="media_start"):
            ss.timer_start = None
            ss.timer_question_idx = None
            ss.timer_expired = False
            ss.vision_status = (
                VisionStatus.PENDING if mode == "mic+cam" else VisionStatus.DISABLED
            )
            track(
                analytics.INTERVIEW_STARTED,
                job_field=ss.profile.get("job_field"),
                experience_level=ss.profile.get("experience_level"),
                n_questions=ss.profile.get("n_questions"),
                media_mode=ss.media_mode,
                stt_mode=ss.stt_mode,
                timer_seconds=ss.timer_seconds,
                own_key=using_own_key(),
            )
            ss.stage = "question"
            st.rerun()


# ==========================================================================
# Stage 3 — the interview loop
# ==========================================================================


@st.fragment
def render_camera_panel() -> None:
    """Browser-side face analysis.

    Lives in a fragment so the component's periodic posts rerun only this
    panel — the mic recorder and the transcript box are never disturbed.
    """
    ss = st.session_state

    payload = components.face_monitor(
        active=True, question_idx=int(ss.question_idx), key="face_monitor"
    )

    if isinstance(payload, dict):
        raw_status = str(payload.get("status", "pending"))
        try:
            ss.vision_status = VisionStatus(raw_status)
        except ValueError:
            ss.vision_status = VisionStatus.ERROR

        batch_id = int(payload.get("batch_id", -1))
        if batch_id > int(ss.vision_last_batch):
            ss.vision_last_batch = batch_id
            ss.vision.ingest_many(payload.get("samples"))

    status: VisionStatus = ss.vision_status
    tone = {
        VisionStatus.RUNNING: "ok",
        VisionStatus.PENDING: "warn",
        VisionStatus.DISABLED: "off",
    }.get(status, "bad")
    theme.status_chip(status.label, tone)

    if status in (VisionStatus.UNSUPPORTED, VisionStatus.PERMISSION_DENIED, VisionStatus.ERROR):
        st.caption(
            "No problem — your answers are scored on what you say, not how you look. "
            "The rest of the interview works exactly the same."
        )


@st.fragment(run_every=1)
def render_timer() -> None:
    """One-second countdown that reruns only itself."""
    ss = st.session_state
    if ss.timer_start is None or ss.timer_expired:
        return

    remaining = max(0.0, float(ss.timer_seconds) - (time.time() - float(ss.timer_start)))
    fraction = remaining / float(ss.timer_seconds) if ss.timer_seconds else 0.0

    minutes, seconds = divmod(int(remaining), 60)
    st.progress(
        max(0.0, min(1.0, fraction)),
        text=f"{minutes}:{seconds:02d} left on this answer",
    )

    if remaining <= 0:
        ss.timer_expired = True
        st.rerun(scope="app")


def _current_answer_key() -> str:
    return f"answer_text_{st.session_state.question_idx}"


def submit_current_answer(*, auto: bool = False) -> None:
    """Record the answer, advance, and prepare the next question."""
    ss = st.session_state
    answer = (ss.get(_current_answer_key(), "") or "").strip()

    if not answer:
        if not auto:
            st.error(
                "No answer captured yet. Record your response, or type it into the box.",
            )
            return
        answer = NO_ANSWER_PLACEHOLDER

    # Face stats for this question, then clear for the next one. This is the
    # snapshot_and_reset() that app.py always called but never existed.
    try:
        if ss.media_mode == "mic+cam":
            face_stats = ss.vision.snapshot_and_reset(status=ss.vision_status)
        else:
            face_stats = disabled_face_stats(VisionStatus.DISABLED)
    except Exception:  # noqa: BLE001 - a metrics blip must not lose an answer
        face_stats = disabled_face_stats(VisionStatus.ERROR)

    record_answer(ss.current_question, answer, face_stats)
    track(
        analytics.QUESTION_ANSWERED,
        index=int(ss.question_idx),
        words=len(answer.split()),
        auto_submitted=auto,
    )

    ss.question_idx += 1
    ss.current_question = ""
    ss.question_error = None
    ss.timer_expired = False
    ss.timer_start = None
    ss.timer_question_idx = None

    if ss.question_idx >= int(ss.profile.get("n_questions", 5)):
        ss.stage = "finished"

    st.rerun()


def _render_mic(q_idx: int) -> None:
    """Record the answer, then transcribe it with Groq Whisper.

    There is deliberately only one capture path. The library's ``speech_to_text``
    helper is *not* browser speech recognition despite its name: it uploads the
    audio to an undocumented Google demo endpoint from the **server**, over plain
    HTTP, and swallows every failure. On a shared cloud IP that endpoint is
    throttled almost immediately, which silently produced empty transcripts and
    therefore meaningless scores.
    """
    ss = st.session_state
    answer_key = _current_answer_key()
    ss.setdefault(answer_key, "")

    nonce = int(ss.stt_nonce.get(q_idx, 0))

    from streamlit_mic_recorder import mic_recorder

    recording = mic_recorder(
        start_prompt="Start recording",
        stop_prompt="Stop and transcribe",
        just_once=True,
        use_container_width=True,
        format="webm",
        key=f"rec_q{q_idx}_{nonce}",
    )
    if recording and recording.get("bytes"):
        with st.spinner("Transcribing your answer…"):
            try:
                throttle()
                text = transcribe_audio(recording["bytes"], **credentials())
                if text:
                    existing = (ss[answer_key] or "").strip()
                    ss[answer_key] = f"{existing} {text}".strip()
                else:
                    st.warning(
                        "That recording was too short to transcribe. Try again, or "
                        "type your answer below.",
                    )
            except Exception as exc:  # noqa: BLE001
                st.warning(_friendly_error(exc))

    st.text_area(
        "Your answer",
        key=answer_key,
        height=180,
        max_chars=MAX_ANSWER_CHARS,
        placeholder="Record above, or just type your answer here — both are scored the same.",
        label_visibility="collapsed",
    )

    captured = (ss.get(answer_key) or "").strip()
    if captured:
        st.caption(f"{len(captured.split())} words captured.")
    else:
        st.caption("Nothing captured yet.")


def render_question() -> None:
    ss = st.session_state
    q_idx = int(ss.question_idx)
    n_questions = int(ss.profile.get("n_questions", 5))

    # An expired timer submits before anything else renders.
    if ss.timer_expired:
        submit_current_answer(auto=True)
        return

    theme.progress(STEP_LABELS, 2)
    theme.pills(
        [
            ("Question", f"{q_idx + 1} of {n_questions}"),
            ("Field", ss.profile.get("job_field", "")),
            ("Level", ss.profile.get("experience_level", "")),
        ]
    )

    if not _ensure_question():
        st.error(ss.question_error)
        col_retry, col_leave = st.columns(2)
        with col_retry:
            if st.button("Try again", type="primary", use_container_width=True,
                         key="retry_question"):
                st.rerun()
        with col_leave:
            st.button("End interview", use_container_width=True,
                      key="end_interview_error", on_click=reset_to_setup)
        return

    # Start this question's clock once.
    if ss.timer_question_idx != q_idx:
        ss.timer_question_idx = q_idx
        ss.timer_start = time.time()
        ss.timer_expired = False

    theme.question_card(ss.current_question)
    components.speak(ss.current_question, autoplay=bool(ss.audio_enabled))
    render_timer()

    st.divider()

    if ss.media_mode == "mic+cam":
        col_answer, col_cam = st.columns([1.35, 1], gap="large")
        with col_answer:
            theme.section("Your answer")
            _render_mic(q_idx)
        with col_cam:
            theme.section("Camera")
            render_camera_panel()
    else:
        theme.section("Your answer")
        _render_mic(q_idx)

    st.divider()

    is_last = q_idx + 1 >= n_questions
    # Explicit key: the label changes on the last question, which would
    # otherwise change the widget's generated id and drop the click.
    if st.button(
        "Finish and get my report" if is_last else "Submit answer",
        type="primary",
        use_container_width=True,
        key=f"submit_answer_{q_idx}",
    ):
        submit_current_answer()

    with st.expander("Leave this interview"):
        st.caption("Your progress will be discarded.")
        st.button("End interview", use_container_width=True,
                  key="end_interview", on_click=reset_to_setup)


# ==========================================================================
# Stage 4 — results
# ==========================================================================


def render_finished() -> None:
    ss = st.session_state

    theme.progress(STEP_LABELS, 3)
    theme.title(
        "Your interview report",
        f"{ss.profile.get('job_field', 'Interview')} · "
        f"{ss.profile.get('experience_level', '')}",
    )

    # If nothing was captured, scoring an empty transcript produces a very low
    # score that looks like a verdict on the candidate but is really a capture
    # failure. Say what actually happened instead, and don't spend quota on it.
    if answered_count(ss.qa) == 0 and ss.qa:
        st.error(
            "We couldn't hear any of your answers, so there's nothing to score.",
        )
        st.markdown(
            "This almost always means the microphone wasn't captured. Common causes:\n\n"
            "- **Microphone permission was blocked.** Check the address bar for a "
            "blocked-microphone icon and allow access.\n"
            "- **Live transcription isn't supported in this browser.** It works in "
            "Chrome and Edge, but not Safari or Firefox. On the recording screen, "
            "choose **Record, then transcribe** instead, which works everywhere.\n"
            "- **You can always type your answers** into the answer box; typed "
            "answers are scored exactly the same."
        )
        track(analytics.INTERVIEW_NO_ANSWERS, n_questions=len(ss.qa),
              stt_mode=ss.stt_mode, media_mode=ss.media_mode)
        st.divider()
        st.button("Try again", type="primary", use_container_width=True,
                  key="retry_no_answers", on_click=reset_to_setup)
        return

    if ss.final_result is None and ss.scoring_error is None:
        with st.spinner("The hiring panel is reviewing your answers…"):
            try:
                throttle()
                ss.final_result = score_full_interview(
                    profile=ss.profile, qa_history=ss.qa, **credentials()
                )
                track(
                    analytics.INTERVIEW_COMPLETED,
                    job_field=ss.profile.get("job_field"),
                    experience_level=ss.profile.get("experience_level"),
                    n_questions=len(ss.qa),
                    overall_score=ss.final_result.get("overall_score"),
                    media_mode=ss.media_mode,
                    repaired=ss.final_result.get("_meta", {}).get("was_repaired"),
                )
            except Exception as exc:  # noqa: BLE001
                ss.scoring_error = _friendly_error(exc)
                track(analytics.SCORING_FAILED, kind=type(exc).__name__)

    if ss.scoring_error:
        st.error(ss.scoring_error)
        col_retry, col_restart = st.columns(2)
        with col_retry:
            if st.button("Retry scoring", type="primary", use_container_width=True,
                         key="retry_scoring"):
                ss.scoring_error = None
                st.rerun()
        with col_restart:
            st.button("Start over", use_container_width=True,
                      key="start_over", on_click=reset_to_setup)

        with st.expander("See your transcript anyway"):
            for index, item in enumerate(ss.qa, start=1):
                st.markdown(f"**Q{index}.** {item.get('q', '')}")
                st.markdown(f"> {item.get('a', '') or '_(no answer)_'}")
        return

    if ss.final_result:
        results.render(ss.final_result, ss.profile, ss.qa)

    st.divider()
    st.button("Start a new interview", use_container_width=True,
              key="new_interview", on_click=reset_to_setup)


# ==========================================================================
# Router
# ==========================================================================

_STAGES = {
    "setup": render_setup,
    "media": render_media_setup,
    "question": render_question,
    "finished": render_finished,
}


def _is_admin_request() -> bool:
    try:
        return "admin" in st.query_params
    except Exception:  # noqa: BLE001
        return False


def main() -> None:
    if _is_admin_request():
        admin.render()
        return

    render_sidebar()

    render_stage = _STAGES.get(st.session_state.stage)
    if render_stage is None:
        st.session_state.stage = "setup"
        st.rerun()
    else:
        render_stage()


# A last-resort boundary. Individual operations already degrade gracefully;
# this catches anything unforeseen so a stranger sees a recoverable message
# instead of a broken page. `st.rerun()` raises control-flow exceptions that
# must be allowed through untouched.
try:
    main()
except Exception as exc:  # noqa: BLE001
    if type(exc).__name__ in {"RerunException", "StopException"}:
        raise
    logging.getLogger("intereview").exception("unhandled error in main()")
    track(analytics.PROVIDER_ERROR, stage="unhandled", kind=type(exc).__name__)
    st.error(
        "Something went wrong on our side. Your progress is safe — please try again.",
    )
    st.button("Reload the app", type="primary",
              key="reload_app", on_click=reset_to_setup)
