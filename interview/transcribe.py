"""Fallback speech-to-text.

The primary transcription path is the browser's own Web Speech API (via
``streamlit_mic_recorder.speech_to_text``), which costs nothing and never
touches a server. It is, however, absent on Safari and some Firefox builds.

This module is the fallback for those browsers: raw recorded audio is uploaded
once and transcribed by Groq's free Whisper endpoint. It replaces the old
``engine.transcribe_audio_bytes``, which was OpenAI-billed dead code that
``app.py`` never called.

Free-tier ceiling for ``whisper-large-v3-turbo`` is 20 requests/minute and
2,000 requests/day, shared across the deployment — same 429-not-billed
behaviour as the chat models.
"""

from __future__ import annotations

from typing import Optional

from interview import llm
from interview.config import settings

#: Below this, the clip is almost certainly silence or a misclick.
MIN_AUDIO_BYTES = 2048


class TranscriptionUnavailable(llm.LLMError):
    user_message = (
        "Automatic transcription isn't available right now. You can type your "
        "answer in the box instead — it's scored exactly the same way."
    )


def transcribe_audio(
    audio_bytes: bytes,
    *,
    filename: str = "answer.webm",
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> str:
    """Transcribe recorded audio, returning ``""`` for clips that are too short.

    Raises :class:`interview.llm.LLMError` subclasses on provider failure so the
    caller can render a friendly message rather than a stack trace.
    """
    if not audio_bytes or len(audio_bytes) < MIN_AUDIO_BYTES:
        return ""

    if len(audio_bytes) > settings.max_stt_upload_bytes:
        raise TranscriptionUnavailable(
            f"audio upload too large: {len(audio_bytes)} bytes",
            "That recording is too long to transcribe. Please keep answers under "
            "about two minutes, or type your answer instead.",
        )

    client = llm.get_provider(provider=provider, api_key=api_key)
    return client.transcribe(
        audio_bytes=audio_bytes,
        filename=filename,
        model=model or settings.stt_model,
    ).strip()
