"""Provider-agnostic LLM access.

Design goals
------------
* **No vendor SDK types in any signature.** Call sites (`questions.py`,
  `scoring.py`) speak only in `str` / `dict`, so swapping providers touches
  this file alone.
* **Never billable.** Both supported providers expose a no-credit-card free
  tier that returns HTTP 429 when exhausted rather than charging overage.
* **Shared-quota aware.** Free-tier limits are per *organization*, so every
  visitor to a deployment shares one bucket. Throttling is therefore an
  expected steady state, not an exceptional case: we retry with backoff,
  honour ``Retry-After``, downgrade to a cheaper model, and finally surface a
  calm user-facing message.
* **Stateless.** Providers hold only an API key and are constructed per call,
  so nothing leaks between concurrent Streamlit sessions.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol

import requests

from interview.config import get_secret, settings

# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class LLMError(Exception):
    """Base error. ``user_message`` is safe to render to strangers."""

    user_message = (
        "The interviewer had trouble responding. Please try again in a moment."
    )

    def __init__(self, message: str, user_message: Optional[str] = None) -> None:
        super().__init__(message)
        if user_message:
            self.user_message = user_message


class RateLimitError(LLMError):
    user_message = (
        "The interviewer is catching its breath — this free service shares a "
        "rate limit across everyone using it right now. Wait a few seconds and "
        "retry. (Tip: add your own free API key in the sidebar to skip the queue.)"
    )


class AuthError(LLMError):
    user_message = (
        "The AI provider rejected the API key. If you entered your own key in "
        "the sidebar, double-check it; otherwise this deployment needs its key "
        "configured."
    )


class ProviderUnavailable(LLMError):
    user_message = (
        "This deployment has no AI provider configured yet. Add a free Groq API "
        "key in the sidebar to start an interview."
    )


# --------------------------------------------------------------------------
# Provider protocol
# --------------------------------------------------------------------------


class LLMProvider(Protocol):
    """The entire surface a provider must implement."""

    name: str

    def generate(
        self,
        *,
        system: str,
        user: str,
        model: str,
        temperature: float = 0.7,
        json_mode: bool = False,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Return the model's raw text response."""
        ...

    def transcribe(self, *, audio_bytes: bytes, filename: str, model: str) -> str:
        """Transcribe audio. Raise :class:`LLMError` if unsupported."""
        ...


@dataclass(frozen=True)
class _RetryPolicy:
    max_retries: int
    timeout: int

    def sleep_for(self, attempt: int, retry_after: Optional[float]) -> float:
        if retry_after is not None:
            # Honour the server, but never block a web request for too long.
            return min(retry_after, 12.0)
        # Exponential backoff with jitter: ~0.8s, ~1.9s, ~4.2s
        return min(0.8 * (2**attempt) + random.uniform(0, 0.5), 12.0)


def _parse_retry_after(response: requests.Response) -> Optional[float]:
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Groq — OpenAI-compatible REST
# --------------------------------------------------------------------------


class GroqProvider:
    """Groq free tier. OpenAI-compatible, so no vendor SDK is required."""

    name = "groq"
    #: Override to point at any OpenAI-compatible endpoint (a local Ollama, a
    #: proxy, or a stub server during QA).
    DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"

    def __init__(self, api_key: str, base_url: Optional[str] = None) -> None:
        self._api_key = api_key
        self.base_url = (
            base_url or get_secret("GROQ_BASE_URL") or self.DEFAULT_BASE_URL
        ).rstrip("/")
        self._retry = _RetryPolicy(settings.max_retries, settings.request_timeout)

    @property
    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def _post(self, path: str, **kwargs: Any) -> requests.Response:
        """POST with retry on 429 / 5xx. Raises typed errors."""
        last_exc: Optional[Exception] = None

        for attempt in range(self._retry.max_retries):
            try:
                response = requests.post(
                    f"{self.base_url}{path}",
                    headers=self._headers,
                    timeout=self._retry.timeout,
                    **kwargs,
                )
            except requests.Timeout as exc:
                last_exc = exc
                time.sleep(self._retry.sleep_for(attempt, None))
                continue
            except requests.RequestException as exc:
                raise LLMError(
                    f"network error talking to Groq: {exc}",
                    "Could not reach the AI provider. Check your connection and retry.",
                ) from exc

            if response.status_code == 429:
                if attempt < self._retry.max_retries - 1:
                    time.sleep(self._retry.sleep_for(attempt, _parse_retry_after(response)))
                    continue
                raise RateLimitError("groq rate limit exhausted after retries")

            if response.status_code in (401, 403):
                raise AuthError(f"groq auth failed: {response.status_code}")

            if response.status_code >= 500:
                if attempt < self._retry.max_retries - 1:
                    time.sleep(self._retry.sleep_for(attempt, None))
                    continue
                raise LLMError(f"groq server error: {response.status_code}")

            if not response.ok:
                # Surface the provider's own message to logs, never to the user.
                raise LLMError(f"groq error {response.status_code}: {response.text[:400]}")

            return response

        raise LLMError(f"groq request failed after retries: {last_exc}")

    def generate(
        self,
        *,
        system: str,
        user: str,
        model: str,
        temperature: float = 0.7,
        json_mode: bool = False,
        max_tokens: Optional[int] = None,
    ) -> str:
        payload: Dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if json_mode:
            # Provider-enforced JSON. Far more reliable than asking in prose.
            payload["response_format"] = {"type": "json_object"}

        response = self._post("/chat/completions", json=payload)
        try:
            return (response.json()["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError(f"unexpected Groq response shape: {exc}") from exc

    def transcribe(self, *, audio_bytes: bytes, filename: str, model: str) -> str:
        response = self._post(
            "/audio/transcriptions",
            files={"file": (filename, audio_bytes, "application/octet-stream")},
            data={"model": model, "response_format": "text"},
        )
        text = response.text.strip()
        # response_format=text returns a bare body, but be tolerant of JSON.
        if text.startswith("{"):
            try:
                return str(json.loads(text).get("text", "")).strip()
            except ValueError:
                return text
        return text


# --------------------------------------------------------------------------
# Gemini — redundancy behind the same interface
# --------------------------------------------------------------------------


class GeminiProvider:
    """Google AI Studio free tier. Used when Groq's shared quota is a bottleneck."""

    name = "gemini"
    base_url = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._retry = _RetryPolicy(settings.max_retries, settings.request_timeout)

    def generate(
        self,
        *,
        system: str,
        user: str,
        model: str,
        temperature: float = 0.7,
        json_mode: bool = False,
        max_tokens: Optional[int] = None,
    ) -> str:
        generation_config: Dict[str, Any] = {"temperature": temperature}
        if max_tokens:
            generation_config["maxOutputTokens"] = max_tokens
        if json_mode:
            generation_config["responseMimeType"] = "application/json"

        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": generation_config,
        }

        for attempt in range(self._retry.max_retries):
            try:
                response = requests.post(
                    f"{self.base_url}/models/{model}:generateContent",
                    headers={"x-goog-api-key": self._api_key},
                    json=payload,
                    timeout=self._retry.timeout,
                )
            except requests.Timeout:
                time.sleep(self._retry.sleep_for(attempt, None))
                continue
            except requests.RequestException as exc:
                raise LLMError(
                    f"network error talking to Gemini: {exc}",
                    "Could not reach the AI provider. Check your connection and retry.",
                ) from exc

            if response.status_code == 429:
                if attempt < self._retry.max_retries - 1:
                    time.sleep(self._retry.sleep_for(attempt, _parse_retry_after(response)))
                    continue
                raise RateLimitError("gemini rate limit exhausted after retries")

            if response.status_code in (401, 403):
                raise AuthError(f"gemini auth failed: {response.status_code}")

            if response.status_code >= 500:
                if attempt < self._retry.max_retries - 1:
                    time.sleep(self._retry.sleep_for(attempt, None))
                    continue
                raise LLMError(f"gemini server error: {response.status_code}")

            if not response.ok:
                raise LLMError(f"gemini error {response.status_code}: {response.text[:400]}")

            try:
                parts = response.json()["candidates"][0]["content"]["parts"]
                return "".join(p.get("text", "") for p in parts).strip()
            except (KeyError, IndexError, ValueError) as exc:
                raise LLMError(f"unexpected Gemini response shape: {exc}") from exc

        raise LLMError("gemini request failed after retries")

    def transcribe(self, *, audio_bytes: bytes, filename: str, model: str) -> str:
        raise LLMError(
            "gemini provider does not implement audio transcription here",
            "Audio fallback transcription isn't available on this provider. "
            "Please type your answer instead.",
        )


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------

_PROVIDERS = {"groq": GroqProvider, "gemini": GeminiProvider}

_KEY_ENV = {"groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY"}

#: Models a visitor-supplied key may select, per provider.
MODEL_CHOICES = {
    "groq": ["llama-3.1-8b-instant", "llama-3.3-70b-versatile"],
    "gemini": ["gemini-2.0-flash", "gemini-2.5-flash"],
}


#: Placeholder values that look like a configured key but are not one.
_PLACEHOLDER_KEYS = {
    "paste_your_real_groq_key_here",
    "gsk_replace_me",
    "aiza_replace_me",
    "your_key_here",
    "changeme",
}


def shared_api_key_for(provider: str) -> Optional[str]:
    """The deployment-wide key, if the owner configured a real one.

    Placeholder values from ``.env.example`` are treated as *absent* so the UI
    surfaces "no key configured" instead of failing later with a confusing
    auth error.
    """
    env_name = _KEY_ENV.get(provider)
    if not env_name:
        return None
    key = get_secret(env_name)
    if not key or key.strip().lower() in _PLACEHOLDER_KEYS:
        return None
    return key


def custom_base_url() -> Optional[str]:
    """A non-default endpoint, if one is configured.

    Returned so the UI can say so out loud. A stub or proxy pointed at by
    ``GROQ_BASE_URL`` otherwise looks identical to the real provider, which is
    exactly how canned answers can masquerade as genuine model output.
    """
    override = get_secret("GROQ_BASE_URL")
    if override and override.rstrip("/") != GroqProvider.DEFAULT_BASE_URL:
        return override.rstrip("/")
    return None


def get_provider(
    *, provider: Optional[str] = None, api_key: Optional[str] = None
) -> LLMProvider:
    """Build a provider.

    ``api_key`` is the visitor's own key when supplied; otherwise the shared
    deployment key is used. A fresh instance is returned every call so no
    credential is ever cached across sessions.
    """
    name = (provider or settings.provider).lower()
    cls = _PROVIDERS.get(name)
    if cls is None:
        raise ProviderUnavailable(f"unknown provider {name!r}")

    key = (api_key or "").strip() or shared_api_key_for(name)
    if not key:
        raise ProviderUnavailable(f"no API key available for provider {name!r}")

    return cls(key)


def generate(
    *,
    system: str,
    user: str,
    model: str,
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
    temperature: float = 0.7,
    json_mode: bool = False,
    max_tokens: Optional[int] = None,
    fallback_model: Optional[str] = None,
) -> str:
    """Single entry point used by the rest of the app.

    On rate-limit exhaustion, retry once against ``fallback_model`` — on Groq
    the small model has a ~14x larger daily budget than the large one, so this
    keeps interviews alive when the shared quota on the big model is spent.
    """
    client = get_provider(provider=provider, api_key=api_key)
    try:
        return client.generate(
            system=system,
            user=user,
            model=model,
            temperature=temperature,
            json_mode=json_mode,
            max_tokens=max_tokens,
        )
    except RateLimitError:
        if not fallback_model or fallback_model == model:
            raise
        return client.generate(
            system=system,
            user=user,
            model=fallback_model,
            temperature=temperature,
            json_mode=json_mode,
            max_tokens=max_tokens,
        )


def available_providers() -> List[str]:
    """Providers this deployment has a shared key for."""
    return [name for name in _PROVIDERS if shared_api_key_for(name)]
