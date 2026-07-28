"""InteReviewAI — spoken mock interviews with employer-style feedback.

Free to operate at any traffic level:

* questions + scoring  -> Groq / Gemini free tier (429s, never a bill)
* interviewer's voice   -> browser SpeechSynthesis (no server call)
* candidate's speech    -> browser SpeechRecognition, Groq Whisper fallback
* face analytics        -> MediaPipe WASM in the visitor's browser

Flow: setup -> media choice -> per-question loop with timer -> scored report.
"""

from __future__ import annotations

import time

import streamlit as st

from interview import llm
from interview.config import settings
from interview.questions import fallback_question, generate_next_question
from interview.scoring import score_full_interview
from interview.session import (
    STEP_LABELS,
    can_regenerate,
    can_start_interview,
    credentials,
    init_state,
    note_interview_started,
    note_regeneration,
    record_answer,
    reset_to_setup,
    start_new_interview,
    throttle,
    using_own_key,
)
from interview.transcribe import transcribe_audio
from interview.ui import components, results, theme
from interview.vision import VisionStatus, disabled_face_stats

st.set_page_config(
    page_title="InteReviewAI — Mock Interview Practice",
    page_icon="🎤",
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


def _browser_is_safari() -> bool:
    """Best-effort UA sniff to pick a sensible default STT mode.

    Safari and some Firefox builds lack usable live SpeechRecognition. This
    only chooses a *default*; the user can always switch modes by hand.
    """
    try:
        agent = str(st.context.headers.get("User-Agent", ""))
    except Exception:
        return False
    return ("Safari" in agent and "Chrome" not in agent and "Chromium" not in agent) or (
        "Firefox" in agent
    )


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
            return False


# ==========================================================================
# Sidebar
# ==========================================================================


def render_sidebar() -> None:
    ss = st.session_state

    with st.sidebar:
        st.markdown("### ⚙️ Settings")

        ss.audio_enabled = st.toggle("Read questions aloud", value=ss.audio_enabled)

        if settings.allow_user_api_key:
            st.divider()
            st.markdown("#### 🔑 Use your own API key")
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
                st.success("Using your key — no shared rate limit.", icon="✅")

        st.divider()
        if not settings.has_shared_key and not using_own_key():
            st.warning(
                "This deployment has no shared API key configured, so you'll need to "
                "add your own above to run an interview.",
                icon="⚠️",
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
            "Continue  →", use_container_width=True, type="primary"
        )
        theme.button_hint("Or press Enter to submit form")

    if submitted:
        if not can_start_interview():
            st.error(
                "You've started a lot of interviews in this session. "
                "Refresh the page to continue.",
                icon="🛑",
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
    if not ss.get("stt_default_applied"):
        ss.stt_mode = "upload" if _browser_is_safari() else "browser"
        ss.stt_default_applied = True

    mode = st.radio(
        "Recording mode",
        options=["mic", "mic+cam"],
        format_func=lambda value: {
            "mic": "🎙️  Microphone only",
            "mic+cam": "🎥  Microphone + camera",
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
            icon="🔒",
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
        stt_choice = st.radio(
            "Speech-to-text",
            options=["browser", "upload"],
            index=0 if ss.stt_mode == "browser" else 1,
            format_func=lambda value: {
                "browser": "Live transcription (Chrome/Edge)",
                "upload": "Record, then transcribe",
            }[value],
        )
        ss.stt_mode = stt_choice
        if stt_choice == "browser":
            st.caption(
                "Fastest option. Uses your browser's built-in speech recognition. "
                "Well supported in Chrome and Edge."
            )
        else:
            st.caption(
                "Works in every browser. Records your answer and then transcribes it. "
                "Slightly slower but more accurate."
            )
        if stt_choice == "browser" and _browser_is_safari():
            st.warning(
                "Your browser may not support live transcription. If the transcript "
                "stays empty, switch to “Record, then transcribe”.",
                icon="⚠️",
            )

    st.divider()
    col_back, col_go = st.columns([1, 2])
    with col_back:
        if st.button("←  Back", use_container_width=True):
            st.session_state.stage = "setup"
            st.rerun()
    with col_go:
        if st.button("Start interview  →", type="primary", use_container_width=True):
            ss.timer_start = None
            ss.timer_question_idx = None
            ss.timer_expired = False
            ss.vision_status = (
                VisionStatus.PENDING if mode == "mic+cam" else VisionStatus.DISABLED
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
        text=f"⏱️  {minutes}:{seconds:02d} left on this answer",
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
                icon="🎙️",
            )
            return
        answer = "(No answer was given before time ran out.)"

    # Face stats for this question, then clear for the next one. This is the
    # snapshot_and_reset() that app.py always called but never existed.
    if ss.media_mode == "mic+cam":
        face_stats = ss.vision.snapshot_and_reset(status=ss.vision_status)
    else:
        face_stats = disabled_face_stats(VisionStatus.DISABLED)

    record_answer(ss.current_question, answer, face_stats)

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
    """The capture panel: live transcription or record-then-transcribe."""
    ss = st.session_state
    answer_key = _current_answer_key()
    ss.setdefault(answer_key, "")

    nonce = int(ss.stt_nonce.get(q_idx, 0))

    if ss.stt_mode == "browser":
        from streamlit_mic_recorder import speech_to_text

        transcript = speech_to_text(
            language="en",
            start_prompt="🎙️  Start talking",
            stop_prompt="⏹️  Stop and transcribe",
            just_once=True,
            use_container_width=True,
            key=f"stt_q{q_idx}_{nonce}",
        )
        if transcript:
            existing = (ss[answer_key] or "").strip()
            ss[answer_key] = f"{existing} {transcript.strip()}".strip()
    else:
        from streamlit_mic_recorder import mic_recorder

        recording = mic_recorder(
            start_prompt="🎙️  Start recording",
            stop_prompt="⏹️  Stop and transcribe",
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
                            icon="🎙️",
                        )
                except Exception as exc:  # noqa: BLE001
                    st.warning(_friendly_error(exc), icon="⚠️")

    st.text_area(
        "Your answer",
        key=answer_key,
        height=180,
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
        st.error(ss.question_error, icon="⏳")
        col_retry, col_skip = st.columns(2)
        with col_retry:
            if st.button("↻  Try again", type="primary", use_container_width=True):
                st.rerun()
        with col_skip:
            if st.button("Use a standard question", use_container_width=True):
                ss.current_question = fallback_question(q_idx)
                ss.question_error = None
                st.rerun()
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
            theme.section("Your answer", "🎙️")
            _render_mic(q_idx)
        with col_cam:
            theme.section("Camera", "📷")
            render_camera_panel()
    else:
        theme.section("Your answer", "🎙️")
        _render_mic(q_idx)

    st.divider()

    is_last = q_idx + 1 >= n_questions
    if st.button(
        "Finish and get my report  →" if is_last else "Submit answer  →",
        type="primary",
        use_container_width=True,
    ):
        submit_current_answer()

    with st.expander("Leave this interview"):
        st.caption("Your progress will be discarded.")
        if st.button("End interview", use_container_width=True):
            reset_to_setup()
            st.rerun()


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

    if ss.final_result is None and ss.scoring_error is None:
        with st.spinner("The hiring panel is reviewing your answers…"):
            try:
                throttle()
                ss.final_result = score_full_interview(
                    profile=ss.profile, qa_history=ss.qa, **credentials()
                )
            except Exception as exc:  # noqa: BLE001
                ss.scoring_error = _friendly_error(exc)

    if ss.scoring_error:
        st.error(ss.scoring_error, icon="⏳")
        col_retry, col_restart = st.columns(2)
        with col_retry:
            if st.button("↻  Retry scoring", type="primary", use_container_width=True):
                ss.scoring_error = None
                st.rerun()
        with col_restart:
            if st.button("Start over", use_container_width=True):
                reset_to_setup()
                st.rerun()

        with st.expander("See your transcript anyway"):
            for index, item in enumerate(ss.qa, start=1):
                st.markdown(f"**Q{index}.** {item.get('q', '')}")
                st.markdown(f"> {item.get('a', '') or '_(no answer)_'}")
        return

    if ss.final_result:
        results.render(ss.final_result, ss.profile, ss.qa)

    st.divider()
    if st.button("↩︎  Start a new interview", use_container_width=True):
        reset_to_setup()
        st.rerun()


# ==========================================================================
# Router
# ==========================================================================

render_sidebar()

_STAGES = {
    "setup": render_setup,
    "media": render_media_setup,
    "question": render_question,
    "finished": render_finished,
}

_render = _STAGES.get(st.session_state.stage)
if _render is None:
    st.session_state.stage = "setup"
    st.rerun()
else:
    _render()
