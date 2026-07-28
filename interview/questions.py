"""Adaptive interview question generation."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from interview import llm
from interview.config import settings
from interview.prompts import INTERVIEWER_SYSTEM_PROMPT

#: Spoken-friendly cap. Long questions are unpleasant read aloud.
MAX_QUESTION_CHARS = 320

_FALLBACK_QUESTIONS = [
    "To start, walk me through your background and what drew you to this role.",
    "Tell me about a project you're proud of. What was your specific contribution?",
    "Describe a technical problem that took you longer than expected. How did you work through it?",
    "Tell me about a time you disagreed with a teammate. How did you resolve it?",
    "What's a tradeoff you made recently, and what would you do differently now?",
    "Where do you want to grow most in the next year, and why?",
    "Tell me about a time you received difficult feedback. What changed afterward?",
    "Looking back on your work so far, what's the biggest thing you've learned?",
]


def _build_user_message(
    profile: Dict[str, Any],
    qa_history: List[Dict[str, Any]],
    question_idx: int,
    n_questions: int,
) -> str:
    history_lines = []
    for i, item in enumerate(qa_history, start=1):
        question = str(item.get("q", "")).strip()
        answer = str(item.get("a", "")).strip()
        history_lines.append(f"Q{i}: {question}\nA{i}: {answer}")

    profile_block = {
        "job_field": profile.get("job_field", ""),
        "company_size": profile.get("company_size", ""),
        "interview_style": profile.get("interview_style", ""),
        "interviewer_personality": profile.get("personality", ""),
        "candidate_experience_level": profile.get("experience_level", ""),
        "question_index": question_idx,
        "total_questions": n_questions,
    }

    return (
        "CANDIDATE_PROFILE:\n"
        f"{json.dumps(profile_block, ensure_ascii=False, indent=2)}\n\n"
        "Q/A HISTORY (most recent last):\n"
        + ("\n\n".join(history_lines) if history_lines else "(none yet)")
        + "\n\nNow generate the NEXT single interview question."
    )


def _tidy(text: str) -> str:
    """Strip model scaffolding and keep the question spoken-friendly."""
    cleaned = (text or "").strip()

    # Models sometimes prefix with a label despite instructions.
    for prefix in ("question:", "interviewer:", "next question:"):
        if cleaned.lower().startswith(prefix):
            cleaned = cleaned[len(prefix) :].strip()

    cleaned = cleaned.strip().strip('"').strip("'").strip()
    cleaned = " ".join(cleaned.split())  # collapse newlines for TTS

    if len(cleaned) > MAX_QUESTION_CHARS:
        cleaned = cleaned[:MAX_QUESTION_CHARS].rsplit(" ", 1)[0].strip().rstrip(",;:") + "…"
    return cleaned


def fallback_question(question_idx: int) -> str:
    """A sane question to use when the provider is unreachable."""
    return _FALLBACK_QUESTIONS[question_idx % len(_FALLBACK_QUESTIONS)]


def generate_next_question(
    *,
    profile: Dict[str, Any],
    qa_history: List[Dict[str, Any]],
    question_idx: int,
    n_questions: int,
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> str:
    """Generate the next question.

    Raises :class:`interview.llm.LLMError` on provider failure; the caller
    decides whether to surface a retry affordance or use
    :func:`fallback_question`.
    """
    text = llm.generate(
        system=INTERVIEWER_SYSTEM_PROMPT,
        user=_build_user_message(profile, qa_history, question_idx, n_questions),
        model=model or profile.get("model_override") or settings.question_model,
        provider=provider,
        api_key=api_key,
        temperature=0.8,
        max_tokens=200,
        fallback_model=settings.fallback_model,
    )

    tidied = _tidy(text)
    return tidied or fallback_question(question_idx)
