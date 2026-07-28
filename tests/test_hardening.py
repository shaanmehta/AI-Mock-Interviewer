"""Guards against inputs that could crash or exhaust a public deployment."""

from __future__ import annotations

import json

import pytest

from interview import vision
from interview.questions import MAX_HISTORY_CHARS, _build_user_message
from interview.scoring import MAX_ANSWER_CHARS_FOR_SCORING, _build_payload
from interview.vision import MAX_BATCH_SIZE, MAX_SAMPLES_PER_QUESTION, VisionAggregator


def _sample():
    return {"face": True, "score": 0.9, "cx": 0.5, "cy": 0.5,
            "centered": True, "eye_contact": True}


# ---- Memory bounds --------------------------------------------------------


def test_vision_samples_are_capped_per_question():
    agg = VisionAggregator()
    for _ in range(MAX_SAMPLES_PER_QUESTION + 500):
        agg.update(_sample())
    assert len(agg) == MAX_SAMPLES_PER_QUESTION


def test_a_single_batch_cannot_flood_the_aggregator():
    agg = VisionAggregator()
    accepted = agg.ingest_many([_sample()] * 10000)
    assert accepted == MAX_BATCH_SIZE


def test_hostile_batch_of_garbage_is_ignored_not_fatal():
    agg = VisionAggregator()
    assert agg.ingest_many([None, "x", 1, [], {}, {"cx": "NaN"}]) >= 0
    agg.snapshot_and_reset()  # must not raise


def test_snapshot_is_json_serialisable_under_extreme_values():
    agg = VisionAggregator()
    agg.ingest_many([
        {"face": True, "score": float("nan"), "cx": float("inf"), "cy": -99},
        {"face": True, "score": 1e308, "cx": 0.5, "cy": 0.5},
    ])
    json.dumps(agg.snapshot_and_reset())


# ---- Token / payload bounds ----------------------------------------------


def test_question_history_is_truncated_to_a_budget():
    history = [{"q": "q" * 500, "a": "a" * 5000} for _ in range(40)]
    message = _build_user_message({"job_field": "Robotics"}, history, 40, 40)
    assert len(message) < MAX_HISTORY_CHARS + 3000


def test_history_truncation_keeps_the_most_recent_turns():
    history = [{"q": f"Q{i}", "a": "a" * 2000} for i in range(10)]
    message = _build_user_message({}, history, 10, 10)
    # The newest turn must survive; the oldest must be dropped.
    assert "Q10:" in message or "Q9" in message
    assert "Q1:" not in message


def test_scoring_payload_truncates_enormous_answers():
    transcript = [{"q": "q", "a": "x" * 100000} for _ in range(8)]
    payload = _build_payload({"job_field": "Robotics"}, transcript)
    parsed = json.loads(payload)
    for item in parsed["transcript"]:
        assert len(item["a"]) <= MAX_ANSWER_CHARS_FOR_SCORING


def test_scoring_payload_survives_unserialisable_stats():
    class Weird:
        pass

    transcript = [{"q": "q", "a": "a", "voice": {"x": Weird()}, "face": None}]
    json.loads(_build_payload({}, transcript))  # must not raise


# ---- No canned questions --------------------------------------------------


def test_no_fallback_question_bank_remains():
    """A real interview is adaptive or not worth running."""
    import interview.questions as questions

    assert not hasattr(questions, "fallback_question")
    assert not hasattr(questions, "_FALLBACK_QUESTIONS")


def test_empty_model_output_raises_rather_than_faking_a_question():
    from unittest.mock import patch

    from interview import llm
    from interview.questions import generate_next_question

    with patch("interview.llm.generate", return_value="   "):
        with pytest.raises(llm.LLMError):
            generate_next_question(
                profile={}, qa_history=[], question_idx=0, n_questions=5,
                api_key="k",
            )


def test_center_tolerance_still_governs_derived_centering():
    off = dict(_sample(), cx=0.5 + vision.CENTER_TOLERANCE + 0.05)
    del off["centered"]
    assert vision.normalize_sample(off).centered is False
