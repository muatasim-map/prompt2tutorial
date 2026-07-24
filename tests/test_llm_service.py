"""Tests for centralized Gemini retry / fallback / cooldown / concurrency.

No live API calls: ``_raw_gemini_call`` is replaced with in-memory fakes.
"""

import threading
import time

import pytest

from config import ModelRoles, RetryPolicy
from llm_service import CAT_RATE_LIMIT, LLMError, LLMService


class FakeRateLimit(Exception):
    """Mimics a google-genai 429 / RESOURCE_EXHAUSTED after retries exhausted."""

    def __init__(self):
        super().__init__("rate limited")
        self.code = 429
        self.status = "RESOURCE_EXHAUSTED"
        self.message = "RESOURCE_EXHAUSTED"


class FakeNotFound(Exception):
    def __init__(self):
        super().__init__("model not found")
        self.code = 404
        self.status = "NOT_FOUND"
        self.message = "NOT_FOUND"


def _roles(fallback="fb"):
    return ModelRoles(provider="gemini", script="primary", animation="anim",
                      repair="repair", fallback=fallback)


def _policy(**kw):
    defaults = dict(attempts=1, initial_delay=0.0, max_delay=0.0, cooldown_threshold=1,
                    cooldown_seconds=100.0, max_concurrency=2, fallback_enabled=True)
    defaults.update(kw)
    return RetryPolicy(**defaults)


def test_primary_success_no_fallback():
    service = LLMService(_roles(), _policy())
    service._raw_gemini_call = lambda model, system, prompt, response_schema=None: ('{"ok":1}', 10, 5)
    result = service.generate("script", "sys", "p", "gemini")
    assert result.model == "primary"
    assert result.used_fallback is False


def test_falls_back_after_primary_rate_limit():
    events = []
    service = LLMService(_roles(), _policy(), status=events.append)

    def fake(model, system, prompt, response_schema=None):
        if model == "primary":
            raise FakeRateLimit()
        return '{"ok":1}', 10, 5

    service._raw_gemini_call = fake
    result = service.generate("script", "sys", "p", "gemini")
    assert result.model == "fb"
    assert result.used_fallback is True
    assert result.fallback_reason == CAT_RATE_LIMIT
    # A fallback event was surfaced.
    assert any(e.get("fallback") for e in events)


def test_no_silent_downgrade_when_no_fallback():
    service = LLMService(_roles(fallback=None), _policy())
    service._raw_gemini_call = lambda *a, **k: (_ for _ in ()).throw(FakeRateLimit())
    with pytest.raises(LLMError) as exc:
        service.generate("script", "sys", "p", "gemini")
    assert exc.value.category == CAT_RATE_LIMIT


def test_cooldown_skips_primary_on_next_call():
    calls = []
    service = LLMService(_roles(), _policy(cooldown_threshold=1))

    def fake(model, system, prompt, response_schema=None):
        calls.append(model)
        if model == "primary":
            raise FakeRateLimit()
        return '{"ok":1}', 10, 5

    service._raw_gemini_call = fake

    # First call: primary 429 -> threshold(1) reached -> cooldown set -> fallback.
    service.generate("script", "sys", "p", "gemini")
    assert "primary" in calls

    calls.clear()
    # Second call: primary is cooling down -> skipped entirely, straight to fb.
    result = service.generate("script", "sys", "p", "gemini")
    assert result.model == "fb"
    assert "primary" not in calls


def test_unavailable_model_falls_back():
    service = LLMService(_roles(), _policy())

    def fake(model, system, prompt, response_schema=None):
        if model == "primary":
            raise FakeNotFound()
        return '{"ok":1}', 10, 5

    service._raw_gemini_call = fake
    result = service.generate("script", "sys", "p", "gemini")
    assert result.model == "fb"


def test_role_routing_uses_animation_model():
    service = LLMService(_roles(), _policy())
    seen = {}

    def fake(model, system, prompt, response_schema=None):
        seen["model"] = model
        return '{"ok":1}', 10, 5

    service._raw_gemini_call = fake
    service.generate("animation", "sys", "p", "gemini")
    assert seen["model"] == "anim"


def test_bounded_concurrency():
    limit = 2
    service = LLMService(_roles(fallback=None), _policy(max_concurrency=limit))
    lock = threading.Lock()
    state = {"current": 0, "max": 0}

    def fake(model, system, prompt, response_schema=None):
        with lock:
            state["current"] += 1
            state["max"] = max(state["max"], state["current"])
        time.sleep(0.05)
        with lock:
            state["current"] -= 1
        return '{"ok":1}', 10, 5

    service._raw_gemini_call = fake

    threads = [threading.Thread(target=lambda: service.generate("script", "s", "p", "gemini"))
               for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert state["max"] <= limit
