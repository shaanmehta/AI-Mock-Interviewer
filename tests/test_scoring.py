"""Scoring must produce a valid, renderable result for *any* model output.

These cover the failure modes that made the original scorer unreliable:
invalid JSON in the prompt template, capitalized vs snake_case keys, and
missing fields silently producing an empty rubric.
"""

from __future__ import annotations

import json

import pytest

from interview.scoring import (
    RESULT_KEYS,
    RUBRIC_KEYS,
    extract_json_object,
    validate_result,
)


# ---- extract_json_object --------------------------------------------------


def test_extracts_bare_json():
    assert extract_json_object('{"a": 1}') == {"a": 1}


def test_extracts_from_code_fence():
    raw = 'Sure!\n```json\n{"overall_score": 80}\n```\nHope that helps.'
    assert extract_json_object(raw) == {"overall_score": 80}


def test_extracts_from_surrounding_prose():
    raw = 'Here is the result: {"overall_score": 72, "summary": "ok"} — done.'
    assert extract_json_object(raw)["overall_score"] == 72


def test_ignores_braces_inside_strings():
    raw = '{"summary": "they said {this} and \\"that\\"", "overall_score": 5}'
    parsed = extract_json_object(raw)
    assert parsed["overall_score"] == 5
    assert "{this}" in parsed["summary"]


@pytest.mark.parametrize("raw", ["", "no json at all", "{unclosed: ", None])
def test_returns_none_when_unparseable(raw):
    assert extract_json_object(raw) is None


# ---- validate_result ------------------------------------------------------


def _assert_wellformed(result):
    for key in RESULT_KEYS:
        assert key in result, f"missing {key}"
    assert isinstance(result["overall_score"], int)
    assert 0 <= result["overall_score"] <= 100
    assert set(result["rubric"]) == set(RUBRIC_KEYS)
    for axis, value in result["rubric"].items():
        assert 0 <= value <= 10, f"{axis} out of range: {value}"
    assert isinstance(result["summary"], str) and result["summary"]
    assert isinstance(result["strengths"], list)
    assert isinstance(result["improvements"], list)
    assert isinstance(result["question_notes"], list)
    assert set(result["advanced_stats"]) == {"voice", "face", "other"}
    # Must be JSON-serialisable for download/export.
    json.dumps(result)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        {},
        [],
        "a bare string",
        {"overall_score": "eighty-ish"},
        {"rubric": None},
        {"rubric": {"clarity": "high"}},
        {"overall_score": 9999, "rubric": {"clarity": -50}},
        {"question_notes": "not a list"},
        {"strengths": "one single string"},
        {"advanced_stats": ["wrong", "type"]},
    ],
)
def test_always_returns_valid_shape(raw):
    """The contract: never raise, always renderable."""
    _assert_wellformed(validate_result(raw))


def test_accepts_canonical_snake_case():
    raw = {
        "overall_score": 82,
        "rubric": {key: 8 for key in RUBRIC_KEYS},
        "summary": "Solid interview.",
        "strengths": ["Clear structure"],
        "improvements": ["More metrics"],
        "question_notes": [
            {
                "question": "Tell me about yourself",
                "answer_excerpt": "I build robots",
                "diagnosis": "Good but short",
                "fixes": ["Add a result"],
            }
        ],
        "advanced_stats": {"voice": "steady", "face": "centered", "other": ""},
    }
    result = validate_result(raw)
    _assert_wellformed(result)
    assert result["overall_score"] == 82
    assert result["_meta"]["was_repaired"] is False
    assert result["question_notes"][0]["fixes"] == ["Add a result"]


def test_accepts_legacy_capitalized_keys():
    """The old prompt asked for these; tolerate them rather than losing a report."""
    raw = {
        "Overall Score": 77,
        "Rubric": {
            "Clarity": 8,
            "Structure": 7,
            "Relevance": 8,
            "Technical Correctness": 6,
            "Depth Tradeoffs": 7,
            "Confidence Professionalism": 8,
            "Evidence Impact": 6,
            "Listening Followups": 7,
        },
        "Summary": "Reasonable performance.",
        "Strengths": ["Good energy"],
        "Improvements": ["Be specific"],
    }
    result = validate_result(raw)
    _assert_wellformed(result)
    assert result["overall_score"] == 77
    assert result["rubric"]["technical_correctness"] == 6
    assert result["rubric"]["listening_followups"] == 7
    assert result["strengths"] == ["Good energy"]


def test_derives_overall_score_from_rubric_when_absent():
    result = validate_result({"rubric": {key: 6 for key in RUBRIC_KEYS}})
    assert result["overall_score"] == 60
    assert any("derived" in note for note in result["_meta"]["repaired"])


def test_clamps_out_of_range_values():
    result = validate_result(
        {"overall_score": 250, "rubric": {key: 99 for key in RUBRIC_KEYS}}
    )
    assert result["overall_score"] == 100
    assert all(value == 10 for value in result["rubric"].values())


def test_numeric_strings_are_coerced():
    result = validate_result(
        {"overall_score": "Score: 68/100", "rubric": {"clarity": "7 out of 10"}}
    )
    assert result["overall_score"] == 68
    assert result["rubric"]["clarity"] == 7


def test_missing_rubric_axes_are_flagged_and_defaulted():
    result = validate_result({"rubric": {"clarity": 9}})
    assert result["rubric"]["clarity"] == 9
    assert result["rubric"]["structure"] == 5  # neutral default
    assert result["_meta"]["was_repaired"] is True
