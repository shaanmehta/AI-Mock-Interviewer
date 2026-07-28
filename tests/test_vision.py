"""Vision aggregation runs on untrusted browser input, so it must not crash."""

from __future__ import annotations

import json

import pytest

from interview.vision import (
    VisionAggregator,
    VisionStatus,
    disabled_face_stats,
    normalize_sample,
)


def _sample(**overrides):
    base = {
        "face": True,
        "score": 0.9,
        "cx": 0.5,
        "cy": 0.5,
        "centered": True,
        "eye_contact": True,
    }
    base.update(overrides)
    return base


# ---- normalize_sample -----------------------------------------------------


@pytest.mark.parametrize("raw", [None, "string", 42, [], {"cx": "nope"}])
def test_normalize_never_raises(raw):
    normalize_sample(raw)  # must not raise


def test_normalize_rejects_non_dict():
    assert normalize_sample("not a dict") is None


def test_normalize_clamps_coordinates():
    sample = normalize_sample(_sample(cx=99.0, cy=-5.0))
    assert sample.cx == 1.0
    assert sample.cy == 0.0


def test_normalize_derives_centered_when_absent():
    del_centered = _sample()
    del del_centered["centered"]
    assert normalize_sample(del_centered).centered is True

    off_centre = _sample(cx=0.95)
    del off_centre["centered"]
    assert normalize_sample(off_centre).centered is False


def test_flags_require_a_face():
    """No face means no eye contact and no centering, whatever the client says."""
    sample = normalize_sample(_sample(face=False, centered=True, eye_contact=True))
    assert sample.face is False
    assert sample.centered is False
    assert sample.eye_contact is False


# ---- VisionAggregator -----------------------------------------------------


def test_snapshot_and_reset_exists_and_clears():
    """The method app.py always called but which never existed."""
    aggregator = VisionAggregator()
    for _ in range(10):
        aggregator.update(_sample())

    snapshot = aggregator.snapshot_and_reset()
    assert snapshot["samples"] == 10
    assert snapshot["face_present_pct"] == 100.0
    assert snapshot["vision_enabled"] is True
    json.dumps(snapshot)

    # Cleared for the next question, but lifetime counter is preserved.
    assert len(aggregator) == 0
    assert aggregator.total_samples == 10
    assert aggregator.snapshot_and_reset()["samples"] == 0


def test_percentages_are_relative_to_frames_with_a_face():
    aggregator = VisionAggregator()
    aggregator.ingest_many(
        [_sample(centered=True)] * 3
        + [_sample(centered=False, eye_contact=False)]
        + [_sample(face=False)] * 4
    )
    snapshot = aggregator.snapshot_and_reset()
    assert snapshot["samples"] == 8
    assert snapshot["face_present_pct"] == 50.0   # 4 of 8 frames
    assert snapshot["centered_pct"] == 75.0       # 3 of the 4 with a face
    assert snapshot["eye_contact_pct"] == 75.0


def test_ingest_many_rejects_bad_payloads():
    aggregator = VisionAggregator()
    assert aggregator.ingest_many("not a list") == 0
    assert aggregator.ingest_many([_sample(), "junk", None, 5]) == 1
    assert len(aggregator) == 1


def test_empty_snapshot_is_still_serialisable():
    snapshot = VisionAggregator().snapshot_and_reset(status=VisionStatus.PENDING)
    assert snapshot["samples"] == 0
    assert snapshot["vision_enabled"] is False
    json.dumps(snapshot)


def test_steadiness_high_when_still_and_low_when_moving():
    still = VisionAggregator()
    still.ingest_many([_sample(cx=0.5, cy=0.5) for _ in range(10)])
    assert still.snapshot_and_reset()["framing_steadiness"] == 1.0

    moving = VisionAggregator()
    moving.ingest_many(
        [_sample(cx=0.1 if i % 2 else 0.9, cy=0.1 if i % 2 else 0.9) for i in range(10)]
    )
    assert moving.snapshot_and_reset()["framing_steadiness"] < 0.5


def test_aggregator_instances_are_independent():
    """Two concurrent visitors must never share state."""
    a, b = VisionAggregator(), VisionAggregator()
    a.ingest_many([_sample()] * 5)
    assert len(a) == 5
    assert len(b) == 0


def test_disabled_stats_shape():
    stats = disabled_face_stats(VisionStatus.PERMISSION_DENIED)
    assert stats["vision_enabled"] is False
    assert stats["status"] == "denied"
    json.dumps(stats)


def test_no_module_level_singleton():
    """The old `_global_vision` leaked state across concurrent sessions."""
    import interview.vision as vision

    assert not hasattr(vision, "_global_vision")
    assert not hasattr(vision, "analyze_face")
