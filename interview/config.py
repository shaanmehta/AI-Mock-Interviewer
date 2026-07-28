"""Configuration and secret resolution.

Secrets are resolved in priority order:

1. ``st.secrets``      — how hosted platforms (Streamlit Cloud, HF Spaces) inject them.
2. ``os.environ``      — how Render / Railway / Fly / Docker inject them.
3. ``.env`` at repo root — local development only (git-ignored).

Nothing here ever reads a hard-coded key, and nothing here constructs a vendor
SDK client: providers are resolved in :mod:`interview.llm`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent


def ensure_env_loaded() -> None:
    """Load a local .env if present. No-op in production hosts."""
    env_path = REPO_ROOT / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=False)
    else:
        load_dotenv(override=False)


ensure_env_loaded()


def get_secret(name: str, default: Optional[str] = None) -> Optional[str]:
    """Resolve a secret from st.secrets, then the environment, then default.

    ``st.secrets`` raises if no secrets file exists at all, and touching it
    outside a script run emits a warning, so every access is defensive.
    """
    try:  # pragma: no cover - depends on host
        import streamlit as st

        if name in st.secrets:
            value = str(st.secrets[name]).strip()
            if value:
                return value
    except Exception:
        pass

    value = os.getenv(name)
    if value and value.strip():
        return value.strip()
    return default


def _bool_secret(name: str, default: bool) -> bool:
    raw = get_secret(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int_secret(name: str, default: int) -> int:
    raw = get_secret(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# --------------------------------------------------------------------------
# Job fields
# --------------------------------------------------------------------------
def _read_job_fields() -> List[str]:
    return [
        "Accounting", "Advanced Computing", "Advertising", "Aeronautical Engineering", "Aerospace",
        "Agriculture", "Animation", "Applications Design and Data Analysis", "Applied Computing",
        "Applied Electronics", "Applied Informatics", "Applied Mathematics", "Applied Science",
        "Applied Statistics", "Architecture", "Art", "Artificial Intelligence", "Automation",
        "Automotive Engineering", "Big Data", "Algorithms", "Biochemistry", "BioComputational Physics",
        "Bio-Electrical Engineering", "Bioengineering", "Bioinformatics", "Biology",
        "Biomedical Applications", "Biomedical Engineering", "Business Administration",
        "Business Analytics", "Business Management", "Catalysis Science", "Chartered Accountant",
        "Chemical Engineering", "Chemistry", "Civil Engineering", "Cognitive Science",
        "Communications", "Complexity Science & Engineering", "Computational Fluid Dynamics",
        "Computational Mathematics", "Computational Science & Engineering", "Computer & Info Science",
        "Computer Application", "Computer Engineering", "Computer Games",
        "Computer Information Systems", "Computer Networking", "Computer Science",
        "Computer Science & Engineering", "Computer System Design",
        "Computer Vision and Machine Learning", "Control & Instrumentation",
        "Control Science and Engineering", "Data Science", "Deep Learning", "Design Technology",
        "Ecommerce", "Economics", "Education", "Electrical & Computer Engineering",
        "Electrical and Electronics Engineering", "Electrical Engineering",
        "Electrical Engineering and Computer Science", "Electronic Engineering", "Electronics",
        "Electronics & Communication", "Embedded System Design",
        "Energy and Environmental Systems Engineering", "English", "Entrepreneurship", "Finance",
        "Game Design", "Geomatics Engineering", "Graphic Design", "High Performance Computing",
        "History", "HumanComputer Interaction", "Humanities", "Human Resources", "Images",
        "Industrial Arts", "Industrial Engineering", "Information systems & Technologies",
        "Information Technology", "Innovation and Research Results Transfer",
        "Interactive Telecommunications Program", "Interdisciplinary Studies", "Law",
        "Logic and Methodology of Science", "Machine Learning", "Manufacturing Engineering",
        "Materials Science", "Mathematics", "Mechanical Engineering", "Mechatronics Engineering",
        "Medicine", "Microbiology", "Nanotechnology", "Neuroscience", "Operations Research",
        "Philosophy", "Physics", "Political Science", "Product Management", "Project Management",
        "Psychology", "Robotics", "Software Engineering", "Statistics", "Systems Engineering",
        "UX / UI Design",
    ]


@dataclass(frozen=True)
class Settings:
    """Immutable, process-wide configuration.

    Contains no per-user mutable state — everything here is read-only and safe
    to share across concurrent Streamlit sessions.
    """

    # Provider selection
    provider: str
    question_model: str
    scoring_model: str
    fallback_model: str
    stt_model: str

    # Behaviour
    allow_user_api_key: bool
    request_timeout: int
    max_retries: int

    # Abuse guards (per browser session)
    max_questions: int
    max_interviews_per_session: int
    max_regenerations_per_session: int
    max_stt_upload_bytes: int

    job_fields: List[str] = field(default_factory=_read_job_fields)

    @property
    def has_shared_key(self) -> bool:
        """True when the deployment ships its own key for anonymous visitors."""
        from interview.llm import shared_api_key_for

        return bool(shared_api_key_for(self.provider))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        provider=(get_secret("LLM_PROVIDER", "groq") or "groq").lower(),
        question_model=get_secret("QUESTION_MODEL", "llama-3.1-8b-instant"),
        scoring_model=get_secret("SCORING_MODEL", "llama-3.3-70b-versatile"),
        fallback_model=get_secret("FALLBACK_MODEL", "llama-3.1-8b-instant"),
        stt_model=get_secret("STT_MODEL", "whisper-large-v3-turbo"),
        allow_user_api_key=_bool_secret("ALLOW_USER_API_KEY", True),
        request_timeout=_int_secret("REQUEST_TIMEOUT", 45),
        max_retries=_int_secret("MAX_RETRIES", 3),
        max_questions=_int_secret("MAX_QUESTIONS", 8),
        max_interviews_per_session=_int_secret("MAX_INTERVIEWS_PER_SESSION", 12),
        max_regenerations_per_session=_int_secret("MAX_REGENERATIONS_PER_SESSION", 20),
        max_stt_upload_bytes=_int_secret("MAX_STT_UPLOAD_BYTES", 8 * 1024 * 1024),
    )


settings = get_settings()
