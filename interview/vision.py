"""Face analytics aggregation.

**All face detection now runs in the visitor's own browser** (MediaPipe
``tasks-vision`` WASM, see ``interview/ui/frontend/face_monitor``). The server
receives only small aggregate JSON samples. Nothing here imports OpenCV or
MediaPipe, and the server does zero per-frame CPU work — which is the only way
this feature scales to arbitrary concurrent strangers on a free host.

What changed from the previous version
--------------------------------------
* ``snapshot_and_reset()`` now exists. ``app.py`` called it on every answer
  submission, but it was never implemented, so face analytics silently failed
  for every user and always produced ``{"error": "snapshot failed"}``.
* ``vision_available`` is no longer hardcoded ``True``. Availability is a real,
  per-browser runtime fact reported by the component handshake, tracked in
  :class:`VisionStatus`, and shown honestly in the UI.
* The module-level ``_global_vision`` singleton and ``analyze_face()`` helper
  are gone. A mutable module global is shared by every concurrent visitor in a
  Streamlit process; all per-user state now lives in ``st.session_state``.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

#: Distance (normalized) from frame centre still counted as "centered".
CENTER_TOLERANCE = 0.18

#: Hard cap on samples retained per question. The browser component is polite,
#: but the payload is untrusted: a hostile or buggy client could otherwise post
#: unbounded batches and grow one session's memory without limit.
MAX_SAMPLES_PER_QUESTION = 2000

#: Cap on how many samples a single batch may contribute.
MAX_BATCH_SIZE = 500


class VisionStatus(str, Enum):
    """Truthful, per-session state of the browser-side face analyzer."""

    DISABLED = "disabled"          # user chose microphone-only
    PENDING = "pending"            # component mounted, awaiting handshake
    UNSUPPORTED = "unsupported"    # browser lacks WASM/getUserMedia support
    PERMISSION_DENIED = "denied"   # user declined camera access
    ERROR = "error"                # model/CDN load failed
    RUNNING = "running"            # actively producing samples

    @property
    def is_working(self) -> bool:
        return self is VisionStatus.RUNNING

    @property
    def label(self) -> str:
        return {
            VisionStatus.DISABLED: "Camera off",
            VisionStatus.PENDING: "Starting camera…",
            VisionStatus.UNSUPPORTED: "Not supported in this browser",
            VisionStatus.PERMISSION_DENIED: "Camera permission denied",
            VisionStatus.ERROR: "Face analysis failed to load",
            VisionStatus.RUNNING: "Face analysis active",
        }[self]


@dataclass
class FaceSample:
    """One browser-side observation. Field names mirror the JS payload."""

    face: bool = False
    score: float = 0.0
    cx: float = 0.5
    cy: float = 0.5
    centered: bool = False
    eye_contact: bool = False


def _clamp(value: Any, low: float, high: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return max(low, min(high, number))


def normalize_sample(raw: Any) -> Optional[FaceSample]:
    """Validate one untrusted sample from the browser.

    Returns ``None`` when the payload is unusable, so malformed client data can
    never corrupt the aggregate.
    """
    if not isinstance(raw, dict):
        return None

    face = bool(raw.get("face"))
    cx = _clamp(raw.get("cx"), 0.0, 1.0, 0.5)
    cy = _clamp(raw.get("cy"), 0.0, 1.0, 0.5)

    centered = raw.get("centered")
    if not isinstance(centered, bool):
        centered = abs(cx - 0.5) <= CENTER_TOLERANCE and abs(cy - 0.5) <= CENTER_TOLERANCE

    return FaceSample(
        face=face,
        score=_clamp(raw.get("score"), 0.0, 1.0, 0.0),
        cx=cx,
        cy=cy,
        centered=bool(face and centered),
        eye_contact=bool(face and raw.get("eye_contact")),
    )


class VisionAggregator:
    """Accumulates browser-reported samples for the current question.

    One instance per user, stored in ``st.session_state``. Never shared.
    """

    def __init__(self) -> None:
        self._samples: List[FaceSample] = []
        self._total_samples = 0

    def __len__(self) -> int:
        return len(self._samples)

    @property
    def total_samples(self) -> int:
        """Samples seen across the whole interview, across all snapshots."""
        return self._total_samples

    def update(self, raw: Any) -> bool:
        """Ingest one raw sample. Returns True when it was accepted.

        Once the per-question cap is reached the oldest sample is dropped, so
        memory stays bounded while the aggregate still reflects recent framing.
        """
        sample = normalize_sample(raw)
        if sample is None:
            return False
        self._samples.append(sample)
        if len(self._samples) > MAX_SAMPLES_PER_QUESTION:
            del self._samples[0]
        self._total_samples += 1
        return True

    def ingest_many(self, raws: Any) -> int:
        """Ingest a batch of samples, returning how many were accepted."""
        if not isinstance(raws, (list, tuple)):
            return 0
        return sum(1 for raw in raws[:MAX_BATCH_SIZE] if self.update(raw))

    def summary_dict(self, *, status: VisionStatus = VisionStatus.RUNNING) -> Dict[str, Any]:
        """Aggregate metrics for the samples collected so far."""
        count = len(self._samples)
        if count == 0:
            return {
                "vision_enabled": status.is_working,
                "status": status.value,
                "samples": 0,
                "note": "No face-analysis samples were captured for this answer.",
            }

        with_face = [s for s in self._samples if s.face]
        face_pct = round(100.0 * len(with_face) / count, 1)

        def pct(predicate) -> float:
            if not with_face:
                return 0.0
            return round(100.0 * sum(1 for s in with_face if predicate(s)) / len(with_face), 1)

        avg_confidence = (
            round(statistics.fmean(s.score for s in with_face), 3) if with_face else 0.0
        )

        # Steadiness: how little the face centre wandered. 1.0 = rock steady.
        if len(with_face) >= 2:
            spread = statistics.pstdev([s.cx for s in with_face]) + statistics.pstdev(
                [s.cy for s in with_face]
            )
            steadiness = round(max(0.0, 1.0 - spread * 4.0), 3)
        else:
            steadiness = 0.0

        return {
            "vision_enabled": True,
            "status": status.value,
            "samples": count,
            "face_present_pct": face_pct,
            "centered_pct": pct(lambda s: s.centered),
            "eye_contact_pct": pct(lambda s: s.eye_contact),
            "avg_confidence": avg_confidence,
            "framing_steadiness": steadiness,
            "note": "Browser-side heuristics from MediaPipe FaceLandmarker; noisy, treat as directional only.",
        }

    def snapshot_and_reset(
        self, *, status: VisionStatus = VisionStatus.RUNNING
    ) -> Dict[str, Any]:
        """Return this question's aggregate and clear state for the next one.

        This is the method ``app.py`` always expected but which never existed.
        """
        snapshot = self.summary_dict(status=status)
        self._samples.clear()
        return snapshot

    def reset(self) -> None:
        self._samples.clear()
        self._total_samples = 0


def disabled_face_stats(status: VisionStatus = VisionStatus.DISABLED) -> Dict[str, Any]:
    """The face payload for answers recorded without working face analysis."""
    return {
        "vision_enabled": False,
        "status": status.value,
        "samples": 0,
        "note": f"Face analysis not active for this answer ({status.label}).",
    }
