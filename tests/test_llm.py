"""Provider abstraction: retries, typed errors, and no leaked internals."""

from __future__ import annotations

from unittest.mock import patch

import pytest
import requests

from interview import llm


class _Response:
    def __init__(self, status_code=200, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = headers or {}

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _chat(content):
    return {"choices": [{"message": {"content": content}}]}


@pytest.fixture(autouse=True)
def _no_sleep():
    with patch("interview.llm.time.sleep"):
        yield


def test_generate_returns_content():
    with patch("requests.post", return_value=_Response(payload=_chat(" hello "))):
        assert GroqOK().generate(system="s", user="u", model="m") == "hello"


def GroqOK():
    return llm.GroqProvider("test-key")


def test_json_mode_sets_response_format():
    with patch("requests.post", return_value=_Response(payload=_chat("{}"))) as post:
        GroqOK().generate(system="s", user="u", model="m", json_mode=True)
    assert post.call_args.kwargs["json"]["response_format"] == {"type": "json_object"}


def test_json_mode_off_by_default():
    with patch("requests.post", return_value=_Response(payload=_chat("x"))) as post:
        GroqOK().generate(system="s", user="u", model="m")
    assert "response_format" not in post.call_args.kwargs["json"]


def test_retries_then_succeeds_on_429():
    responses = [_Response(429), _Response(payload=_chat("recovered"))]
    with patch("requests.post", side_effect=responses):
        assert GroqOK().generate(system="s", user="u", model="m") == "recovered"


def test_raises_rate_limit_after_exhausting_retries():
    with patch("requests.post", return_value=_Response(429, headers={"retry-after": "2"})):
        with pytest.raises(llm.RateLimitError) as excinfo:
            GroqOK().generate(system="s", user="u", model="m")
    # The message shown to strangers must be calm and actionable.
    assert "catching its breath" in excinfo.value.user_message


def test_auth_error_is_typed_and_not_retried():
    with patch("requests.post", return_value=_Response(401)) as post:
        with pytest.raises(llm.AuthError):
            GroqOK().generate(system="s", user="u", model="m")
    assert post.call_count == 1


def test_server_errors_are_retried():
    responses = [_Response(500), _Response(503), _Response(payload=_chat("ok"))]
    with patch("requests.post", side_effect=responses) as post:
        assert GroqOK().generate(system="s", user="u", model="m") == "ok"
    assert post.call_count == 3


def test_network_failure_is_wrapped_not_leaked():
    with patch("requests.post", side_effect=requests.ConnectionError("dns exploded")):
        with pytest.raises(llm.LLMError) as excinfo:
            GroqOK().generate(system="s", user="u", model="m")
    assert "dns exploded" not in excinfo.value.user_message


def test_provider_error_body_never_reaches_the_user_message():
    secret = "org_abc123 quota detail"
    with patch("requests.post", return_value=_Response(400, text=secret)):
        with pytest.raises(llm.LLMError) as excinfo:
            GroqOK().generate(system="s", user="u", model="m")
    assert secret not in excinfo.value.user_message


def test_generate_falls_back_to_cheaper_model_on_rate_limit():
    calls = []

    def fake_post(url, **kwargs):
        calls.append(kwargs["json"]["model"])
        return _Response(429) if kwargs["json"]["model"] == "big" else _Response(payload=_chat("small ok"))

    with patch("requests.post", side_effect=fake_post):
        out = llm.generate(
            system="s", user="u", model="big", api_key="k",
            provider="groq", fallback_model="small",
        )
    assert out == "small ok"
    assert "small" in calls


def test_missing_key_raises_provider_unavailable():
    with patch("interview.llm.shared_api_key_for", return_value=None):
        with pytest.raises(llm.ProviderUnavailable):
            llm.get_provider(provider="groq", api_key=None)


def test_unknown_provider_raises():
    with pytest.raises(llm.ProviderUnavailable):
        llm.get_provider(provider="not-a-provider", api_key="k")


def test_user_key_takes_priority_over_shared_key():
    with patch("interview.llm.shared_api_key_for", return_value="shared"):
        provider = llm.get_provider(provider="groq", api_key="mine")
    assert provider._api_key == "mine"


def test_each_call_builds_a_fresh_provider():
    """No cached client can leak one visitor's key to another."""
    with patch("interview.llm.shared_api_key_for", return_value="shared"):
        a = llm.get_provider(provider="groq")
        b = llm.get_provider(provider="groq")
    assert a is not b


def test_gemini_json_mode_sets_response_mime_type():
    payload = {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}
    with patch("requests.post", return_value=_Response(payload=payload)) as post:
        llm.GeminiProvider("k").generate(system="s", user="u", model="m", json_mode=True)
    config = post.call_args.kwargs["json"]["generationConfig"]
    assert config["responseMimeType"] == "application/json"


def test_gemini_concatenates_parts():
    payload = {"candidates": [{"content": {"parts": [{"text": "a"}, {"text": "b"}]}}]}
    with patch("requests.post", return_value=_Response(payload=payload)):
        assert llm.GeminiProvider("k").generate(system="s", user="u", model="m") == "ab"


def test_transcribe_returns_plain_text():
    with patch("requests.post", return_value=_Response(text=" spoken words ")):
        out = GroqOK().transcribe(audio_bytes=b"x" * 5000, filename="a.webm", model="w")
    assert out == "spoken words"


def test_transcribe_tolerates_json_body():
    with patch("requests.post", return_value=_Response(text='{"text": "hi there"}')):
        out = GroqOK().transcribe(audio_bytes=b"x" * 5000, filename="a.webm", model="w")
    assert out == "hi there"


# ---- Configuration guards -------------------------------------------------
# A stub or misconfigured endpoint once served canned questions and a fixed
# score that were indistinguishable from real model output. These guards make
# both situations visible instead of silent.


@pytest.mark.parametrize(
    "placeholder",
    ["PASTE_YOUR_REAL_GROQ_KEY_HERE", "gsk_replace_me", "changeme", "  gsk_replace_me  "],
)
def test_placeholder_keys_count_as_no_key(placeholder):
    with patch("interview.llm.get_secret", return_value=placeholder):
        assert llm.shared_api_key_for("groq") is None


def test_real_key_is_returned():
    with patch("interview.llm.get_secret", return_value="gsk_a_real_looking_key"):
        assert llm.shared_api_key_for("groq") == "gsk_a_real_looking_key"


def test_custom_base_url_reported_when_overridden():
    with patch("interview.llm.get_secret", return_value="http://127.0.0.1:8931/openai/v1"):
        assert llm.custom_base_url() == "http://127.0.0.1:8931/openai/v1"


def test_custom_base_url_none_for_default_endpoint():
    with patch("interview.llm.get_secret", return_value=llm.GroqProvider.DEFAULT_BASE_URL):
        assert llm.custom_base_url() is None
    with patch("interview.llm.get_secret", return_value=None):
        assert llm.custom_base_url() is None
