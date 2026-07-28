"""Custom browser components.

Two capabilities that used to cost money per request now run entirely in the
visitor's browser:

* **Text-to-speech** — the Web Speech ``SpeechSynthesis`` API replaces OpenAI
  TTS. One-way (``st.iframe``), so it never triggers a rerun.
* **Face analysis** — MediaPipe ``tasks-vision`` WASM replaces server-side
  OpenCV/MediaPipe. Bidirectional, so it posts aggregate samples back.

The face monitor implements the Streamlit component postMessage protocol by
hand, which means no npm toolchain and no build step in the repo.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

import streamlit as st
import streamlit.components.v1 as components

_FRONTEND = Path(__file__).resolve().parent / "frontend"

# Component *registration* is immutable process-wide config, not per-user
# state — it is safe to share across concurrent sessions.
_face_monitor_component = components.declare_component(
    "intereview_face_monitor", path=str(_FRONTEND / "face_monitor")
)


@lru_cache(maxsize=1)
def _tts_template() -> str:
    return (_FRONTEND / "tts" / "index.html").read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def _stt_check_template() -> str:
    return (_FRONTEND / "stt_check" / "index.html").read_text(encoding="utf-8")


def speech_support_notice(height: int = 92) -> None:
    """Warn, in-browser, when live transcription is unavailable.

    Feature-detects the Web Speech API rather than sniffing the User-Agent, and
    renders nothing at all when it is supported. This is the failure that
    silently produced empty transcripts (and therefore a meaningless score) for
    Safari and Firefox visitors.
    """
    st.iframe(_stt_check_template(), height=height)


def speak(text: str, *, autoplay: bool = False, height: int = 64) -> None:
    """Render the browser TTS bar for ``text``.

    Costs nothing and scales to unlimited concurrent users: the audio is
    synthesized on the visitor's device, so no server call happens at all.
    The on-screen question text remains the primary UX — audio is an
    enhancement, and the component degrades to an honest notice when the
    browser has no speech synthesis.
    """
    config = json.dumps({"text": text or "", "autoplay": bool(autoplay)})
    # The question text is LLM output, so it is untrusted. It is injected only
    # inside a <script type="application/json"> block — never as markup or as
    # executable JS. json.dumps escapes quotes and backslashes; escaping "</"
    # additionally makes a literal "</script>" impossible, which is the only
    # way to break out of that block.
    config = config.replace("</", "<\\/")
    st.iframe(_tts_template().replace("__TTS_CONFIG__", config), height=height)


def face_monitor(
    *, active: bool, question_idx: int, key: str = "face_monitor"
) -> Optional[Dict[str, Any]]:
    """Render the in-browser face analyzer and return its latest batch.

    Returns ``None`` until the component's first post. The returned dict has
    ``batch_id``, ``question_idx``, ``status``, ``detail`` and ``samples``.

    All detection happens on the visitor's device; the server only ever sees
    a few hundred bytes of aggregate JSON every couple of seconds.
    """
    return _face_monitor_component(
        active=bool(active), question_idx=int(question_idx), key=key, default=None
    )
