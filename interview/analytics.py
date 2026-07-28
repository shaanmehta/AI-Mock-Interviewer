"""Private, owner-only usage analytics.

Design rules
------------
* **It can never crash the app.** Every public function swallows every
  exception. Analytics failing is invisible to the visitor and never blocks a
  page render.
* **No personal data.** Session IDs are random per browser session, not tied to
  a person. Answers, transcripts and API keys are never recorded.
* **Pluggable storage**, because a $0 host has no persistent disk:

  - ``SupabaseSink`` — free hosted Postgres. Survives restarts and redeploys.
    Enabled by setting ``SUPABASE_URL`` + ``SUPABASE_KEY``. This is the one to
    use in production.
  - ``SqliteSink`` — a local file. Fine locally, but on Render's free tier the
    filesystem is wiped on every restart and sleep/wake cycle, so treat the
    numbers as "since last restart".
  - ``MemorySink`` — last resort, lost on restart.

The dashboard lives at ``?admin=1`` and is gated by ``ANALYTICS_PASSWORD``.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from interview.config import REPO_ROOT, get_secret

log = logging.getLogger("intereview.analytics")

# Event names.
SITE_OPENED = "site_opened"
INTERVIEW_STARTED = "interview_started"
INTERVIEW_COMPLETED = "interview_completed"
QUESTION_ANSWERED = "question_answered"
SCORING_FAILED = "scoring_failed"
INTERVIEW_NO_ANSWERS = "interview_no_answers"
PROVIDER_ERROR = "provider_error"

#: Cap so a hostile client cannot bloat storage through the props blob.
_MAX_PROPS_CHARS = 2000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_props(props: Optional[Dict[str, Any]]) -> str:
    """Serialise props defensively; never raise, never grow unbounded."""
    if not props:
        return "{}"
    try:
        text = json.dumps(props, ensure_ascii=False, default=str)
    except Exception:
        return "{}"
    return text[:_MAX_PROPS_CHARS]


# --------------------------------------------------------------------------
# Sinks
# --------------------------------------------------------------------------


class MemorySink:
    """In-process ring buffer. Lost on restart; used when nothing else works."""

    name = "memory"

    def __init__(self, limit: int = 5000) -> None:
        self._rows: List[Dict[str, Any]] = []
        self._limit = limit
        self._lock = threading.Lock()

    def write(self, row: Dict[str, Any]) -> None:
        with self._lock:
            self._rows.append(row)
            if len(self._rows) > self._limit:
                del self._rows[: len(self._rows) - self._limit]

    def read(self, limit: int = 5000) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._rows[-limit:])


class SqliteSink:
    """A local SQLite file.

    A fresh connection per call keeps this safe across Streamlit's threads
    without holding a long-lived handle.
    """

    name = "sqlite"

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=5)

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    event TEXT NOT NULL,
                    props TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_event ON events(event)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_session ON events(session_id)")

    def write(self, row: Dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO events (ts, session_id, event, props) VALUES (?, ?, ?, ?)",
                (row["ts"], row["session_id"], row["event"], row["props"]),
            )

    def read(self, limit: int = 5000) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT ts, session_id, event, props FROM events "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]


class SupabaseSink:
    """Free hosted Postgres via the PostgREST endpoint.

    Writes happen on a daemon thread so a slow or dead network can never delay
    a page render.
    """

    name = "supabase"

    def __init__(self, url: str, key: str, table: str = "events") -> None:
        self._endpoint = f"{url.rstrip('/')}/rest/v1/{table}"
        self._headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

    def write(self, row: Dict[str, Any]) -> None:
        def _send() -> None:
            try:
                requests.post(
                    self._endpoint,
                    headers={**self._headers, "Prefer": "return=minimal"},
                    json={
                        "ts": row["ts"],
                        "session_id": row["session_id"],
                        "event": row["event"],
                        "props": json.loads(row["props"] or "{}"),
                    },
                    timeout=5,
                )
            except Exception as exc:  # noqa: BLE001 - analytics must stay silent
                log.warning("supabase analytics write failed: %s", exc)

        threading.Thread(target=_send, daemon=True).start()

    def read(self, limit: int = 5000) -> List[Dict[str, Any]]:
        response = requests.get(
            self._endpoint,
            headers=self._headers,
            params={"select": "ts,session_id,event,props", "order": "ts.asc",
                    "limit": str(limit)},
            timeout=10,
        )
        response.raise_for_status()
        rows = response.json()
        for row in rows:
            if isinstance(row.get("props"), dict):
                row["props"] = json.dumps(row["props"])
        return rows


# --------------------------------------------------------------------------
# Sink selection
# --------------------------------------------------------------------------

_sink: Optional[Any] = None
_sink_lock = threading.Lock()


def _build_sink() -> Any:
    supabase_url = get_secret("SUPABASE_URL")
    supabase_key = get_secret("SUPABASE_KEY")
    if supabase_url and supabase_key:
        try:
            return SupabaseSink(supabase_url, supabase_key)
        except Exception as exc:  # noqa: BLE001
            log.warning("supabase sink unavailable, falling back: %s", exc)

    try:
        path = get_secret("ANALYTICS_DB_PATH") or str(REPO_ROOT / "analytics.db")
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        return SqliteSink(path)
    except Exception as exc:  # noqa: BLE001
        log.warning("sqlite sink unavailable, using memory: %s", exc)

    return MemorySink()


def sink() -> Any:
    """The process-wide sink.

    This is shared state, but it is *not* per-user state: it holds no visitor
    data, only a connection target, and every sink is internally locked.
    """
    global _sink
    if _sink is None:
        with _sink_lock:
            if _sink is None:
                _sink = _build_sink()
    return _sink


def backend_name() -> str:
    try:
        return sink().name
    except Exception:  # noqa: BLE001
        return "unavailable"


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def new_session_id() -> str:
    """A random, non-identifying id for one browser session."""
    return uuid.uuid4().hex


def track(session_id: str, event: str, **props: Any) -> None:
    """Record one event. Never raises, never blocks meaningfully."""
    try:
        sink().write(
            {
                "ts": _now(),
                "session_id": str(session_id or "unknown")[:64],
                "event": str(event)[:64],
                "props": _safe_props(props),
            }
        )
    except Exception as exc:  # noqa: BLE001 - analytics must never break a page
        log.warning("analytics write failed: %s", exc)


def fetch(limit: int = 5000) -> List[Dict[str, Any]]:
    """Read recent events for the dashboard. Returns [] on any failure."""
    try:
        return sink().read(limit=limit)
    except Exception as exc:  # noqa: BLE001
        log.warning("analytics read failed: %s", exc)
        return []


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Turn raw events into the headline funnel plus useful breakdowns."""
    opened: set = set()
    started: set = set()
    completed: set = set()
    answers = 0
    errors = 0
    scores: List[float] = []
    fields: Dict[str, int] = {}
    media: Dict[str, int] = {}
    stt: Dict[str, int] = {}

    for row in rows:
        event = row.get("event")
        session = row.get("session_id", "")
        try:
            props = json.loads(row.get("props") or "{}")
        except Exception:  # noqa: BLE001
            props = {}

        if event == SITE_OPENED:
            opened.add(session)
        elif event == INTERVIEW_STARTED:
            started.add(session)
            if props.get("job_field"):
                fields[props["job_field"]] = fields.get(props["job_field"], 0) + 1
            if props.get("media_mode"):
                media[props["media_mode"]] = media.get(props["media_mode"], 0) + 1
            if props.get("stt_mode"):
                stt[props["stt_mode"]] = stt.get(props["stt_mode"], 0) + 1
        elif event == INTERVIEW_COMPLETED:
            completed.add(session)
            score = props.get("overall_score")
            if isinstance(score, (int, float)):
                scores.append(float(score))
        elif event == QUESTION_ANSWERED:
            answers += 1
        elif event in (SCORING_FAILED, PROVIDER_ERROR):
            errors += 1

    def rate(numerator: int, denominator: int) -> float:
        return round(100.0 * numerator / denominator, 1) if denominator else 0.0

    return {
        "opened": len(opened),
        "started": len(started),
        "completed": len(completed),
        "answers": answers,
        "errors": errors,
        "start_rate": rate(len(started), len(opened)),
        "completion_rate": rate(len(completed), len(started)),
        "avg_score": round(sum(scores) / len(scores), 1) if scores else None,
        "scores": scores,
        "by_field": dict(sorted(fields.items(), key=lambda kv: -kv[1])),
        "by_media": media,
        "by_stt": stt,
        "total_events": len(rows),
    }
