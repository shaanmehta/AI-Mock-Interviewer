"""Adaptive interview question generation."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from interview import llm
from interview.config import settings
from interview.prompts import INTERVIEWER_SYSTEM_PROMPT

#: Spoken-friendly cap. Long questions are unpleasant read aloud.
MAX_QUESTION_CHARS = 320

#: Hard cap on how much prior transcript is replayed to the model. Without it a
#: long interview can exceed the free tier's per-request token limit and start
#: failing partway through.
MAX_HISTORY_CHARS = 6000


def _build_user_message(
    profile: Dict[str, Any],
    qa_history: List[Dict[str, Any]],
    question_idx: int,
    n_questions: int,
) -> str:
    # Build newest-first, then reverse, so truncation drops the *oldest* turns.
    history_lines: List[str] = []
    budget = MAX_HISTORY_CHARS
    for i in range(len(qa_history), 0, -1):
        item = qa_history[i - 1]
        question = str(item.get("q", "")).strip()
        answer = str(item.get("a", "")).strip()
        line = f"Q{i}: {question}\nA{i}: {answer}"
        if len(line) > budget:
            break
        budget -= len(line)
        history_lines.append(line)
    history_lines.reverse()

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

    Raises :class:`interview.llm.LLMError` on provider failure, including when
    the model returns nothing usable. There is deliberately no canned-question
    fallback: a real interview is either adaptive or it is not worth running.
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
    if not tidied:
        raise llm.LLMError(
            "model returned an empty question",
            "The interviewer didn't have a question ready. Please try again.",
        )
    return tidied
