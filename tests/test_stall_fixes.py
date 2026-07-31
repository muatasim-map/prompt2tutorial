"""Fixes for the 115-minute job-stall incident (job baa10e08-2f58-4635-a037-2700831d42c9).

Forensic finding: the reported "60-minute rate-limit delay" was actually a hung
socket. google-genai's HttpOptions had NO timeout configured, so a stalled
connection blocked generate_content() until the OS's own TCP retransmission
ceiling gave up (~60 min on Windows) — our own bounded retry/backoff logic
never got a chance to run because the call never returned control.

Six fixes, verified here:
1. An explicit HTTP timeout is now set on the Gemini client.
2. 3D scenes get a longer compile timeout (2D-calibrated 120s falsely timed out
   a real 3D render three times in a row in the incident).
3. A compile timeout gets timeout-specific repair guidance, not the generic
   error-fixer (which made pointless unrelated edits against a non-error).
4. Surface resolution is capped and un-whitelisted solids are named as invalid
   (the incident's Scene 10 used Cylinder, which was never in the whitelist).
5. The color list is closed (no "etc.") - Scene 8 invented AMBER.
6. A hard per-scene wall-clock budget bounds the whole compile+repair loop
   regardless of how many of the 3 attempts are consumed.
"""

import time

import pytest

import concat_video as cv
import manim_generator as mg
from config import get_retry_policy


# --- fix 1: HTTP timeout is configured ------------------------------------- #

def test_retry_policy_has_a_finite_http_timeout():
    policy = get_retry_policy()
    assert policy.http_timeout_ms > 0
    assert policy.http_timeout_ms >= 5_000        # floor enforced
    assert policy.http_timeout_ms <= 600_000       # sane upper bound (10 min)


def test_gemini_client_receives_the_configured_timeout(monkeypatch):
    """The client construction must actually pass the timeout through."""
    import llm_service

    captured = {}

    class FakeHttpOptions:
        def __init__(self, **kw):
            captured.update(kw)

    class FakeClient:
        def __init__(self, **kw):
            pass

    monkeypatch.setattr(llm_service.genai_types, "HttpOptions", FakeHttpOptions)
    monkeypatch.setattr(llm_service.genai, "Client", FakeClient)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-test")

    svc = llm_service.LLMService.__new__(llm_service.LLMService)
    svc.policy = get_retry_policy()
    svc._gemini_client = None
    svc._client()

    assert captured.get("timeout") == svc.policy.http_timeout_ms


def test_http_timeout_env_override(monkeypatch):
    monkeypatch.setenv("GEMINI_HTTP_TIMEOUT_MS", "45000")
    assert get_retry_policy().http_timeout_ms == 45000


def test_http_timeout_has_a_floor(monkeypatch):
    """A misconfigured near-zero override must not re-create the hang risk."""
    monkeypatch.setenv("GEMINI_HTTP_TIMEOUT_MS", "10")
    assert get_retry_policy().http_timeout_ms >= 5_000


# --- fix 2: dimension-aware compile timeout --------------------------------- #

def test_3d_scene_gets_a_longer_timeout_than_2d(monkeypatch):
    monkeypatch.delenv("MANIM_COMPILE_TIMEOUT", raising=False)
    monkeypatch.delenv("MANIM_QUALITY", raising=False)
    flat = cv.resolve_compile_timeout(is_3d=False)
    spatial = cv.resolve_compile_timeout(is_3d=True)
    assert spatial > flat
    assert flat == 120                     # unchanged 2D default
    assert spatial == 210                  # enough for real 3D; not a 300s stall


def test_explicit_override_still_gets_3d_allowance(monkeypatch):
    monkeypatch.setenv("MANIM_COMPILE_TIMEOUT", "100")
    monkeypatch.delenv("MANIM_QUALITY", raising=False)
    assert cv.resolve_compile_timeout(is_3d=False) == 100
    assert cv.resolve_compile_timeout(is_3d=True) > 100


def test_compile_video_passes_is_3d_through(monkeypatch, tmp_path):
    """compile_video's new is_3d kwarg must actually reach the timeout resolver."""
    seen = {}

    def fake_resolve(is_3d=False):
        seen["is_3d"] = is_3d
        return 999

    monkeypatch.setattr(cv, "resolve_compile_timeout", fake_resolve)

    def fake_run(*a, **kw):
        raise cv.subprocess.TimeoutExpired(cmd="manim", timeout=kw.get("timeout"))

    monkeypatch.setattr(cv.subprocess, "run", fake_run)

    scene = tmp_path / "s.py"
    scene.write_text("class X(Scene):\n    def construct(self): pass\n")
    cv.compile_video(scene, "X", tmp_path / "media", is_3d=True)
    assert seen["is_3d"] is True


# --- fix 3: timeout-aware repair prompt ------------------------------------- #

def test_timeout_error_gets_render_speed_guidance_not_generic_fix():
    p = mg._build_fix_prompt(
        "class X(ThreeDScene):\n    def construct(self): pass\n",
        "Timeout: compilation exceeded 120 seconds", "X")
    assert "RENDER-SPEED TIMEOUT, NOT A CODE ERROR" in p
    assert "resolution to (12, 12)" in p
    assert "Do NOT make unrelated" in p


def test_non_timeout_error_does_not_get_timeout_guidance():
    p = mg._build_fix_prompt(
        "class X(Scene):\n    def construct(self): pass\n",
        "NameError: name 'AMBER' is not defined", "X")
    assert "RENDER-SPEED TIMEOUT" not in p


def test_timeout_repair_still_carries_domain_and_intent():
    """The timeout fix must not lose the routing/intent work from before."""
    p = mg._build_fix_prompt(
        "code", "Timeout: compilation exceeded 120 seconds", "X",
        domain_tags=["astrophysics"],
        scene_intent={"learning_goal": "understand orbital motion"})
    assert "understand orbital motion" in p
    assert "Hertzsprung" in p or "orbit" in p.lower()


# --- fix 4: 3D render-cost guardrails --------------------------------------- #

def test_surface_resolution_is_capped_in_the_recipe():
    p = mg._THREED_RECIPE
    assert "resolution MUST be (16, 16) or lower" in p
    assert "QUADRUPLES render time" in p


def test_unwhitelisted_solids_are_named_invalid():
    """Scene 10 in the incident used Cylinder, which was never whitelisted."""
    p = mg._THREED_RECIPE
    assert "Cylinder" in p
    assert "does not exist here" in p


# --- fix 5: closed color vocabulary ----------------------------------------- #

def test_color_list_has_no_open_ended_etc():
    p = mg._COLOR_DIRECTION
    assert ", etc." not in p
    assert "does not exist" in p


def test_amber_is_named_as_an_invalid_example_not_a_valid_color():
    """AMBER (the incident's actual failure) is named ONLY as a bad example."""
    p = mg._COLOR_DIRECTION
    assert "guess (AMBER" in p            # named as an example of an invented color
    assert "does not exist" in p
    assert "hex code" in p.lower()
    import manim
    assert not hasattr(manim, "AMBER")    # confirms the example is a real trap


@pytest.mark.parametrize("name", [
    "WHITE", "BLUE_D", "TEAL_C", "GOLD_A", "MAROON_B", "PURPLE_E",
    "GREY_BROWN", "RED_C", "GREEN_D", "YELLOW_D",
])
def test_listed_colors_are_real_manim_constants(name):
    import manim
    assert hasattr(manim, name), f"{name} listed in prompt but does not exist"


# --- fix 6: per-scene wall-clock budget ------------------------------------- #

def test_scene_compile_budget_constant_is_generous_but_finite():
    import video_generator as vg
    # Timeout handling allows one simplification repair, not three full stalls.
    worst_case_2_attempts = cv.resolve_compile_timeout(is_3d=True) * 2
    assert vg._SCENE_COMPILE_BUDGET_SECONDS > 0
    assert vg._SCENE_COMPILE_BUDGET_SECONDS >= worst_case_2_attempts
    assert vg._SCENE_COMPILE_BUDGET_SECONDS <= 480


def test_is_timeout_error_detects_the_compile_timeout_message():
    import video_generator as vg
    assert vg._is_timeout_error("Timeout: compilation exceeded 120 seconds")
    assert not vg._is_timeout_error("NameError: name 'AMBER' is not defined")
    assert not vg._is_timeout_error(None)
    assert not vg._is_timeout_error("")
