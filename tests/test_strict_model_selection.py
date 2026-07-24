"""Regression tests for exact selected-model enforcement and strict no-fallback.

Covers the Gemini 3.6 Flash regression requirements:
* an explicit UI selection reaches script, storyboard, animation and repair calls
  unchanged, and survives the scene-review /continue resume;
* strict mode retries only that exact model and never falls back;
* environment defaults cannot override an explicit selection.

No live API calls: the Gemini transport is replaced with in-memory fakes.
"""

import pytest

import config
import llm_service
import storyboard as sb_mod
from config import ModelSelection, resolve_model_selection
from llm_service import CAT_RATE_LIMIT, CAT_BAD_REQUEST, LLMError, LLMService


@pytest.fixture
def gemini_env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    for var in ("GEMINI_SCRIPT_MODEL", "GEMINI_ANIMATION_MODEL", "GEMINI_REPAIR_MODEL",
                "GEMINI_STORYBOARD_MODEL", "GEMINI_FALLBACK_MODEL", "LLM_ALLOW_FALLBACK"):
        monkeypatch.delenv(var, raising=False)


class _Boom(Exception):
    def __init__(self, code, status):
        super().__init__(status)
        self.code = code
        self.status = status
        self.message = status


def _fast_policy(monkeypatch, attempts=3):
    """Retry policy with ~zero sleeps so tests stay fast."""
    from config import RetryPolicy
    return RetryPolicy(attempts=attempts, initial_delay=0.0, max_delay=0.0,
                       exp_base=1.0, jitter=0.0, cooldown_threshold=99,
                       cooldown_seconds=0.0, max_concurrency=2, fallback_enabled=True)


# --- selection record ------------------------------------------------------ #

def test_explicit_selection_is_strict_and_uniform(gemini_env):
    sel = resolve_model_selection("gemini-3.6-flash")
    assert sel.strict is True
    assert sel.provider == "gemini"
    assert sel.model == "gemini-3.6-flash"
    for role in ("script", "storyboard", "animation", "repair"):
        assert sel.model_for(role) == "gemini-3.6-flash", role
    audit = sel.audit()
    assert audit["fallback_enabled"] is False
    assert audit["fallback_model"] is None


def test_auto_selection_is_not_strict(gemini_env):
    assert resolve_model_selection("auto").strict is False


def test_fallback_is_opt_in_only(gemini_env, monkeypatch):
    monkeypatch.setenv("LLM_ALLOW_FALLBACK", "true")
    assert resolve_model_selection("gemini-3.6-flash").strict is False


# --- strict runtime behaviour --------------------------------------------- #

def test_strict_never_falls_back_even_when_fallback_configured(gemini_env, monkeypatch):
    from config import ModelRoles
    roles = ModelRoles(provider="gemini", script="gemini-3.6-flash",
                       animation="gemini-3.6-flash", repair="gemini-3.6-flash",
                       fallback="gemini-2.5-flash", storyboard="gemini-3.6-flash")
    svc = LLMService(roles, _fast_policy(monkeypatch), strict=True)

    tried = []

    def fake(model, system, prompt, response_schema=None):
        tried.append(model)
        raise _Boom(429, "RESOURCE_EXHAUSTED")

    svc._raw_gemini_call = fake
    with pytest.raises(LLMError) as exc:
        svc.generate("animation", "sys", "p", "gemini")

    assert set(tried) == {"gemini-3.6-flash"}       # only the selected model
    assert "gemini-2.5-flash" not in tried          # never downgraded
    assert exc.value.category == CAT_RATE_LIMIT
    assert "strict mode" in str(exc.value)


def test_strict_retries_same_model_with_visible_attempts(gemini_env, monkeypatch):
    from config import ModelRoles
    roles = ModelRoles(provider="gemini", script="gemini-3.6-flash",
                       animation="gemini-3.6-flash", repair="gemini-3.6-flash",
                       fallback=None, storyboard="gemini-3.6-flash")
    events = []
    svc = LLMService(roles, _fast_policy(monkeypatch, attempts=3),
                     status=events.append, strict=True)

    calls = {"n": 0}

    def fake(model, system, prompt, response_schema=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _Boom(503, "UNAVAILABLE")
        return '{"ok":1}', 10, 5

    svc._raw_gemini_call = fake
    res = svc.generate("script", "sys", "p", "gemini")

    assert res.model == "gemini-3.6-flash"
    assert res.used_fallback is False
    assert calls["n"] == 3
    # attempt number and backoff wait are surfaced for the progress log
    assert any(e.get("attempt", 0) > 1 for e in events)
    assert any("retry_in_seconds" in e for e in events)


def test_non_retryable_bad_request_is_not_retried(gemini_env, monkeypatch):
    """A malformed request (HTTP 400) must fail fast, not burn the retry budget."""
    from config import ModelRoles
    roles = ModelRoles(provider="gemini", script="m", animation="m", repair="m",
                       fallback=None, storyboard="m")
    svc = LLMService(roles, _fast_policy(monkeypatch, attempts=4), strict=True)

    calls = {"n": 0}

    def fake(model, system, prompt, response_schema=None):
        calls["n"] += 1
        raise _Boom(400, "INVALID_ARGUMENT")

    svc._raw_gemini_call = fake
    with pytest.raises(LLMError) as exc:
        svc.generate("script", "s", "p", "gemini")

    assert calls["n"] == 1                       # tried once only
    assert exc.value.category == CAT_BAD_REQUEST  # not "unavailable_model"


def test_400_is_categorised_as_bad_request_not_unavailable_model():
    """REGRESSION: an unsupported response_schema was reported as a missing model."""
    assert llm_service._categorize(_Boom(400, "INVALID_ARGUMENT")) == CAT_BAD_REQUEST
    assert llm_service._categorize(_Boom(404, "NOT_FOUND")) == "unavailable_model"


# --- storyboard uses a Gemini-compatible schema --------------------------- #

def test_storyboard_uses_flat_gemini_compatible_schema(gemini_env):
    """REGRESSION: the nested Storyboard model was rejected with HTTP 400."""
    from schemas import StoryboardScene, gemini_storyboard_schema
    assert gemini_storyboard_schema() == list[StoryboardScene]


def test_storyboard_pass_uses_selected_model(gemini_env):
    """The storyboard request must go out on the exact selected model."""
    import json
    sel = resolve_model_selection("gemini-3.6-flash")
    seen = {}

    class FakeResult:
        def __init__(self, text):
            self.text = text
            self.model = "gemini-3.6-flash"

    class FakeService:
        roles = sel.roles
        strict = True

        def generate(self, role, system, prompt, provider, client=None, response_schema=None):
            seen["role"] = role
            seen["model"] = self.roles.for_role(role)
            seen["schema"] = response_schema
            scenes = [{
                "index": i, "learning_goal": f"g{i}", "key_concept": f"k{i}",
                "visual_metaphor": f"metaphor-{i}", "composition": f"layout-{i}",
                "primary_objects": ["a", "b"], "primary_motion": f"m{i}",
                "color_role": "blue", "transition_from_prev": "grows",
                "anti_repetition_notes": "distinct", "visual_complexity": "medium",
            } for i in range(1, 4)]
            return FakeResult(json.dumps(scenes))

    scenes = [{"chapter": "c", "text": "t", "animation": "a",
               "objective": "o", "explanation": "e"} for _ in range(3)]
    board = sb_mod.generate_storyboard(FakeService(), "topic", scenes, "gemini", 30, "style")

    assert seen["role"] == "storyboard"
    assert seen["model"] == "gemini-3.6-flash"
    assert len(board.scenes) == 3


# --- quota diagnostics: turn opaque 429s into actionable errors ------------ #

class _QuotaBoom(Exception):
    code = 429
    status = "RESOURCE_EXHAUSTED"

    def __init__(self, quota_id, value="20", retry="42s"):
        self._q, self._v, self._r = quota_id, value, retry
        super().__init__("429")

    def __str__(self):
        return ("429 RESOURCE_EXHAUSTED {'error':{'details':[{"
                f"'quotaId': '{self._q}','quotaValue': '{self._v}'}}],"
                f"'retryDelay': '{self._r}'}}}}")


def test_quota_details_identifies_daily_free_tier():
    q = llm_service.quota_details(
        _QuotaBoom("GenerateRequestsPerDayPerProjectPerModel-FreeTier"))
    assert q["scope"] == "per-day"
    assert q["tier"] == "free"
    assert q["quota_value"] == "20"
    assert q["retry_after_seconds"] == 42


def test_quota_details_identifies_per_minute():
    q = llm_service.quota_details(
        _QuotaBoom("GenerateRequestsPerMinutePerProjectPerModel"))
    assert q["scope"] == "per-minute"


def test_daily_quota_message_is_actionable():
    msg = llm_service._safe_error_message(
        _QuotaBoom("GenerateRequestsPerDayPerProjectPerModel-FreeTier"))
    assert "DAILY cap" in msg
    assert "limit=20" in msg


def test_daily_quota_fails_fast_without_burning_retries(gemini_env, monkeypatch):
    """A per-day cap cannot clear by waiting, so it must not stall the job."""
    from config import ModelRoles
    roles = ModelRoles(provider="gemini", script="gemini-3.6-flash",
                       animation="gemini-3.6-flash", repair="gemini-3.6-flash",
                       fallback=None, storyboard="gemini-3.6-flash")
    svc = LLMService(roles, _fast_policy(monkeypatch, attempts=4), strict=True)

    calls = {"n": 0}

    def fake(model, system, prompt, response_schema=None):
        calls["n"] += 1
        raise _QuotaBoom("GenerateRequestsPerDayPerProjectPerModel-FreeTier")

    svc._raw_gemini_call = fake
    with pytest.raises(LLMError) as exc:
        svc.generate("script", "s", "p", "gemini")

    assert calls["n"] == 1                      # did not burn the retry budget
    assert exc.value.category == CAT_RATE_LIMIT
    assert "DAILY cap" in str(exc.value)
