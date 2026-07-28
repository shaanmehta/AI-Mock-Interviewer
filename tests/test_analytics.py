"""Analytics must be accurate, private, and incapable of breaking a page."""

from __future__ import annotations

import json
from unittest.mock import patch

from interview import analytics


def _rows(*specs):
    """Build event rows: (session, event, props)."""
    return [
        {"ts": f"2026-07-28T00:00:{i:02d}", "session_id": s, "event": e,
         "props": json.dumps(p or {})}
        for i, (s, e, p) in enumerate(specs)
    ]


# ---- Never crashes --------------------------------------------------------


def test_track_never_raises_when_sink_is_broken():
    class Exploding:
        name = "boom"

        def write(self, row):
            raise RuntimeError("disk on fire")

    with patch("interview.analytics.sink", return_value=Exploding()):
        analytics.track("session", analytics.SITE_OPENED)  # must not raise


def test_fetch_returns_empty_when_sink_is_broken():
    class Exploding:
        name = "boom"

        def read(self, limit=0):
            raise RuntimeError("network down")

    with patch("interview.analytics.sink", return_value=Exploding()):
        assert analytics.fetch() == []


def test_props_that_cannot_serialise_do_not_raise():
    class Unserialisable:
        def __repr__(self):
            raise ValueError("nope")

    sink = analytics.MemorySink()
    with patch("interview.analytics.sink", return_value=sink):
        analytics.track("s", "e", weird=Unserialisable())
    assert len(sink.read()) == 1


def test_props_are_length_capped():
    sink = analytics.MemorySink()
    with patch("interview.analytics.sink", return_value=sink):
        analytics.track("s", "e", blob="x" * 50000)
    assert len(sink.read()[0]["props"]) <= 2000


def test_ids_are_truncated_not_rejected():
    sink = analytics.MemorySink()
    with patch("interview.analytics.sink", return_value=sink):
        analytics.track("s" * 500, "e" * 500)
    row = sink.read()[0]
    assert len(row["session_id"]) <= 64
    assert len(row["event"]) <= 64


# ---- Funnel accuracy ------------------------------------------------------


def test_funnel_counts_unique_people_not_events():
    rows = _rows(
        ("a", analytics.SITE_OPENED, None),
        ("a", analytics.SITE_OPENED, None),   # same person, reloaded
        ("b", analytics.SITE_OPENED, None),
        ("a", analytics.INTERVIEW_STARTED, {"job_field": "Robotics"}),
        ("a", analytics.INTERVIEW_COMPLETED, {"overall_score": 80}),
    )
    stats = analytics.summarize(rows)
    assert stats["opened"] == 2
    assert stats["started"] == 1
    assert stats["completed"] == 1


def test_conversion_rates():
    rows = _rows(
        ("a", analytics.SITE_OPENED, None),
        ("b", analytics.SITE_OPENED, None),
        ("c", analytics.SITE_OPENED, None),
        ("d", analytics.SITE_OPENED, None),
        ("a", analytics.INTERVIEW_STARTED, None),
        ("b", analytics.INTERVIEW_STARTED, None),
        ("a", analytics.INTERVIEW_COMPLETED, {"overall_score": 70}),
    )
    stats = analytics.summarize(rows)
    assert stats["start_rate"] == 50.0        # 2 of 4 visitors
    assert stats["completion_rate"] == 50.0   # 1 of 2 starters
    assert stats["avg_score"] == 70.0


def test_summarize_handles_empty_and_malformed_rows():
    assert analytics.summarize([])["opened"] == 0

    broken = [
        {"event": analytics.SITE_OPENED, "session_id": "a", "props": "not json"},
        {"event": analytics.INTERVIEW_STARTED, "session_id": "a", "props": None},
        {},
    ]
    stats = analytics.summarize(broken)
    assert stats["opened"] == 1
    assert stats["started"] == 1


def test_breakdowns_are_collected():
    rows = _rows(
        ("a", analytics.INTERVIEW_STARTED, {"job_field": "Robotics", "media_mode": "mic"}),
        ("b", analytics.INTERVIEW_STARTED, {"job_field": "Robotics", "media_mode": "mic+cam"}),
        ("c", analytics.INTERVIEW_STARTED, {"job_field": "Finance", "media_mode": "mic"}),
    )
    stats = analytics.summarize(rows)
    assert stats["by_field"]["Robotics"] == 2
    assert stats["by_media"] == {"mic": 2, "mic+cam": 1}
    # Sorted most-common first.
    assert list(stats["by_field"]) == ["Robotics", "Finance"]


# ---- Storage --------------------------------------------------------------


def test_sqlite_sink_roundtrip(tmp_path):
    sink = analytics.SqliteSink(str(tmp_path / "a.db"))
    sink.write({"ts": "t", "session_id": "s", "event": analytics.SITE_OPENED,
                "props": "{}"})
    rows = sink.read()
    assert len(rows) == 1 and rows[0]["event"] == analytics.SITE_OPENED


def test_sqlite_sink_survives_reopen(tmp_path):
    path = str(tmp_path / "a.db")
    analytics.SqliteSink(path).write(
        {"ts": "t", "session_id": "s", "event": "e", "props": "{}"}
    )
    assert len(analytics.SqliteSink(path).read()) == 1


def test_memory_sink_is_bounded():
    sink = analytics.MemorySink(limit=10)
    for i in range(50):
        sink.write({"ts": "t", "session_id": str(i), "event": "e", "props": "{}"})
    assert len(sink.read()) == 10


def test_session_ids_are_unique_and_opaque():
    ids = {analytics.new_session_id() for _ in range(500)}
    assert len(ids) == 500
    assert all(len(i) == 32 and i.isalnum() for i in ids)
