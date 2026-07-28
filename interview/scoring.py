"""Interview scoring: request, parse, validate, and normalize.

The old implementation trusted ``json.loads`` on free-text model output and, on
failure, silently dumped the raw string into ``summary`` with an empty rubric.
Scoring was therefore unreliable *by construction*.

This module makes a well-formed result the only possible outcome:

1. Ask the provider with **JSON mode enabled** (schema enforcement, not prose).
2. Extract JSON defensively (code fences, leading prose, trailing commentary).
3. If that fails, make one **repair round-trip** feeding the bad output back.
4. Run every result through :func:`validate_result`, which coerces types, clamps
   ranges, fills missing fields, and accepts the legacy Capitalized key names.

:func:`validate_result` is pure and total — it always returns a dict matching
:data:`RESULT_KEYS`, so the dashboard can render without defensive checks.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from interview import llm
from interview.config import settings
from interview.prompts import SCORER_SYSTEM_PROMPT

#: Canonical rubric axes, in display order.
RUBRIC_KEYS: List[str] = [
    "clarity",
    "structure",
    "relevance",
    "technical_correctness",
    "depth_tradeoffs",
    "confidence_professionalism",
    "evidence_impact",
    "listening_followups",
]

RUBRIC_LABELS: Dict[str, str] = {
    "clarity": "Clarity",
    "structure": "Structure",
    "relevance": "Relevance",
    "technical_correctness": "Technical",
    "depth_tradeoffs": "Depth & Tradeoffs",
    "confidence_professionalism": "Confidence",
    "evidence_impact": "Evidence & Impact",
    "listening_followups": "Listening",
}

#: Short forms for the radar chart, whose angular labels have to fit inside a
#: 375 px-wide phone viewport without eating the plot area.
RUBRIC_SHORT_LABELS: Dict[str, str] = {
    "clarity": "Clarity",
    "structure": "Structure",
    "relevance": "Relevance",
    "technical_correctness": "Technical",
    "depth_tradeoffs": "Depth",
    "confidence_professionalism": "Confidence",
    "evidence_impact": "Evidence",
    "listening_followups": "Listening",
}

RESULT_KEYS = [
    "overall_score",
    "rubric",
    "summary",
    "strengths",
    "improvements",
    "question_notes",
    "advanced_stats",
]

#: Tolerated aliases so a model that ignores the schema still parses.
_KEY_ALIASES = {
    "overall score": "overall_score",
    "overallscore": "overall_score",
    "score": "overall_score",
    "rubric": "rubric",
    "summary": "summary",
    "strengths": "strengths",
    "improvements": "improvements",
    "areas for improvement": "improvements",
    "question notes": "question_notes",
    "questionnotes": "question_notes",
    "advanced stats": "advanced_stats",
    "advancedstats": "advanced_stats",
}

_RUBRIC_ALIASES = {
    "clarity": "clarity",
    "clarity conciseness": "clarity",
    "clarity & conciseness": "clarity",
    "structure": "structure",
    "relevance": "relevance",
    "relevance to question": "relevance",
    "technical correctness": "technical_correctness",
    "technical": "technical_correctness",
    "depth tradeoffs": "depth_tradeoffs",
    "depth & tradeoffs": "depth_tradeoffs",
    "depth": "depth_tradeoffs",
    "confidence professionalism": "confidence_professionalism",
    "confidence & professionalism": "confidence_professionalism",
    "confidence": "confidence_professionalism",
    "evidence impact": "evidence_impact",
    "evidence & impact": "evidence_impact",
    "evidence": "evidence_impact",
    "listening followups": "listening_followups",
    "listening & follow-up handling": "listening_followups",
    "listening": "listening_followups",
}


def _normalize_key(key: str) -> str:
    return re.sub(r"[^a-z0-9& ]+", " ", str(key).strip().lower()).strip()


# --------------------------------------------------------------------------
# JSON extraction
# --------------------------------------------------------------------------


def extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Pull the first complete JSON object out of a model response.

    Handles bare JSON, ```json fences, and prose wrapped around an object.
    Returns ``None`` when nothing parseable is found.
    """
    if not text:
        return None

    candidate = text.strip()

    # Strip markdown fences if present.
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", candidate, re.DOTALL)
    if fence:
        candidate = fence.group(1).strip()

    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else None
    except ValueError:
        pass

    # Brace matching, ignoring braces inside strings.
    start = candidate.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(candidate)):
        char = candidate[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(candidate[start : index + 1])
                    return parsed if isinstance(parsed, dict) else None
                except ValueError:
                    return None
    return None


# --------------------------------------------------------------------------
# Coercion helpers
# --------------------------------------------------------------------------


def _to_number(value: Any, low: float, high: float, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        match = re.search(r"-?\d+(?:\.\d+)?", value)
        if not match:
            return default
        number = float(match.group())
    else:
        return default
    return max(low, min(high, number))


def _to_str(value: Any, default: str = "") -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return default
    if isinstance(value, (list, tuple)):
        return " ".join(_to_str(v) for v in value).strip()
    if isinstance(value, dict):
        return " ".join(f"{k}: {_to_str(v)}" for k, v in value.items()).strip()
    return str(value)


def _to_str_list(value: Any, limit: int = 12) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = [p.strip(" -•\t") for p in value.split("\n") if p.strip()]
        return parts[:limit] if len(parts) > 1 else ([value.strip()] if value.strip() else [])
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            text = _to_str(item)
            if text:
                out.append(text)
        return out[:limit]
    return []


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def validate_result(raw: Any, *, expected_questions: int = 0) -> Dict[str, Any]:
    """Coerce arbitrary parsed output into the canonical schema.

    Total function: always returns a dict with exactly :data:`RESULT_KEYS`.
    ``_meta.repaired`` reports whether anything had to be fixed up, which the
    UI uses to decide whether to show a soft "approximate" notice.
    """
    repaired: List[str] = []

    if not isinstance(raw, dict):
        raw = {}
        repaired.append("root was not a JSON object")

    # Fold aliases into canonical keys.
    folded: Dict[str, Any] = {}
    for key, value in raw.items():
        canonical = _KEY_ALIASES.get(_normalize_key(key), _normalize_key(key).replace(" ", "_"))
        folded.setdefault(canonical, value)

    # --- rubric ---
    rubric_in = folded.get("rubric")
    if not isinstance(rubric_in, dict):
        rubric_in = {}
        repaired.append("rubric missing")

    folded_rubric: Dict[str, Any] = {}
    for key, value in rubric_in.items():
        normalized = _normalize_key(key)
        canonical = _RUBRIC_ALIASES.get(normalized, normalized.replace(" ", "_").replace("&_", ""))
        folded_rubric.setdefault(canonical, value)

    rubric: Dict[str, float] = {}
    for axis in RUBRIC_KEYS:
        if axis not in folded_rubric:
            repaired.append(f"rubric.{axis} missing")
        rubric[axis] = round(_to_number(folded_rubric.get(axis), 0, 10, 5.0), 1)

    # --- overall score ---
    if "overall_score" in folded and folded["overall_score"] is not None:
        overall = _to_number(folded["overall_score"], 0, 100, -1)
    else:
        overall = -1
    if overall < 0:
        # Derive from the rubric rather than showing nothing.
        overall = round(sum(rubric.values()) / len(rubric) * 10)
        repaired.append("overall_score derived from rubric")
    overall = int(round(overall))

    # --- question notes ---
    notes_in = folded.get("question_notes")
    if isinstance(notes_in, dict):
        notes_in = list(notes_in.values())
    if not isinstance(notes_in, list):
        notes_in = []
        if expected_questions:
            repaired.append("question_notes missing")

    notes: List[Dict[str, Any]] = []
    for item in notes_in:
        if not isinstance(item, dict):
            text = _to_str(item)
            if text:
                notes.append(
                    {"question": "", "answer_excerpt": "", "diagnosis": text, "fixes": []}
                )
            continue
        folded_note = {_normalize_key(k).replace(" ", "_"): v for k, v in item.items()}
        notes.append(
            {
                "question": _to_str(folded_note.get("question")),
                "answer_excerpt": _to_str(
                    folded_note.get("answer_excerpt") or folded_note.get("answer")
                ),
                "diagnosis": _to_str(folded_note.get("diagnosis")),
                "fixes": _to_str_list(folded_note.get("fixes")),
            }
        )

    # --- advanced stats ---
    stats_in = folded.get("advanced_stats")
    if not isinstance(stats_in, dict):
        stats_in = {}
    advanced_stats = {
        "voice": _to_str(stats_in.get("voice")),
        "face": _to_str(stats_in.get("face")),
        "other": _to_str(stats_in.get("other")),
    }

    summary = _to_str(folded.get("summary"))
    if not summary:
        repaired.append("summary missing")
        summary = "The panel did not return a written summary for this interview."

    return {
        "overall_score": overall,
        "rubric": rubric,
        "summary": summary,
        "strengths": _to_str_list(folded.get("strengths")),
        "improvements": _to_str_list(folded.get("improvements")),
        "question_notes": notes,
        "advanced_stats": advanced_stats,
        "_meta": {"repaired": repaired, "was_repaired": bool(repaired)},
    }


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

_REPAIR_SYSTEM = (
    "You convert malformed output into valid JSON. Return ONLY a single valid "
    "JSON object matching the schema the user describes. No prose, no code fences."
)


def _build_payload(
    profile: Dict[str, Any], transcript: List[Dict[str, Any]]
) -> str:
    return json.dumps(
        {
            "candidate_profile": profile,
            "transcript": transcript,
            "note": "voice/face stats are noisy browser-side heuristics; treat cautiously.",
        },
        ensure_ascii=False,
    )


def score_full_interview(
    *,
    profile: Dict[str, Any],
    qa_history: List[Dict[str, Any]],
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Score a completed interview.

    Raises :class:`interview.llm.LLMError` subclasses on provider failure so the
    caller can render a friendly message. Any *parsing* problem is handled here
    and never propagates.
    """
    transcript = qa_history or []
    payload = _build_payload(profile, transcript)
    model = model or settings.scoring_model

    raw = llm.generate(
        system=SCORER_SYSTEM_PROMPT,
        user=payload,
        model=model,
        provider=provider,
        api_key=api_key,
        temperature=0.2,
        json_mode=True,
        max_tokens=4096,
        fallback_model=settings.fallback_model,
    )

    parsed = extract_json_object(raw)

    if parsed is None:
        # One repair round-trip before falling back to validation defaults.
        try:
            repaired_raw = llm.generate(
                system=_REPAIR_SYSTEM,
                user=(
                    "Convert the following into a single valid JSON object with keys "
                    "overall_score (0-100 number), rubric (object with numeric keys "
                    f"{', '.join(RUBRIC_KEYS)}), summary (string), strengths (array of "
                    "strings), improvements (array of strings), question_notes (array of "
                    "objects with question, answer_excerpt, diagnosis, fixes), and "
                    "advanced_stats (object with voice, face, other strings).\n\n"
                    f"OUTPUT TO CONVERT:\n{raw[:6000]}"
                ),
                model=settings.fallback_model,
                provider=provider,
                api_key=api_key,
                temperature=0.0,
                json_mode=True,
                max_tokens=4096,
            )
            parsed = extract_json_object(repaired_raw)
        except llm.LLMError:
            parsed = None

    result = validate_result(parsed, expected_questions=len(transcript))

    # If the model produced no per-question notes, scaffold them from the
    # transcript so the dashboard still has something useful per question.
    if not result["question_notes"] and transcript:
        result["question_notes"] = [
            {
                "question": _to_str(item.get("q")),
                "answer_excerpt": _to_str(item.get("a"))[:280],
                "diagnosis": "Per-question feedback was unavailable for this answer.",
                "fixes": [],
            }
            for item in transcript
        ]

    result["_meta"]["model"] = model
    result["_meta"]["n_questions"] = len(transcript)
    return result
