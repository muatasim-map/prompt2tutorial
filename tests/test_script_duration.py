"""Duration-accuracy regression tests (offline, fake provider).

Reproduces the real defect: a 120s request produced a 58s video because nothing
validated that the narration could actually fill the requested duration.
"""

import json

import pytest

import animations as A


def _scenes(n, words):
    return [{"chapter": f"C{i}", "text": " ".join(["word"] * words),
             "animation": "a", "objective": "o", "explanation": "e"}
            for i in range(1, n + 1)]


class _Result:
    def __init__(self, text):
        self.text = text
        self.model = "fake"


class _Service:
    """Returns a scripted sequence of responses and records the roles used."""

    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.roles = []

    def generate(self, role, system, prompt, provider, client=None, response_schema=None):
        self.roles.append(role)
        return _Result(json.dumps(self.payloads.pop(0)))


# --- word budget derived from target ------------------------------------- #

def test_profile_targets_not_ceilings():
    p = A._duration_profile(120)
    assert "scenes" in p["scene_count"]
    assert p["total_words"] == round(120 * A.TTS_WORDS_PER_SECOND)
    # must not read as an upper bound any more
    assert "MAXIMUM" not in p["time_rest"]


@pytest.mark.parametrize("target", [30, 60, 120, 180])
def test_word_budget_matches_target(target):
    p = A._duration_profile(target)
    assert abs(p["total_words"] / A.TTS_WORDS_PER_SECOND - target) < 1.0


# --- the estimator reproduces the observed failure ------------------------ #

def test_estimator_matches_observed_failure():
    """9 scenes x ~16 words produced a real 57.9s video."""
    est = A.estimate_script_seconds(_scenes(9, 16))
    assert 50 < est < 62


def test_estimator_matches_three_minute_job_failure():
    """The supplied 19-scene/306-word job rendered to 119.5s, not 180s."""
    est = A.estimate_script_seconds(_scenes(19, 16))
    assert 115 < est < 125


# --- the gate ------------------------------------------------------------- #

def test_short_script_triggers_one_resize(monkeypatch):
    short = _scenes(9, 16)          # ~55s
    good = _scenes(13, 24)          # ~120s
    svc = _Service(good)

    out = A._enforce_target_duration(short, svc, "topic", "gemini", None, 120)

    assert svc.roles == ["repair"]                    # exactly one extra call
    assert len(out) == 13
    assert 100 < A.estimate_script_seconds(out) < 140


def test_invalid_initial_script_reaches_the_repair_call():
    """The validation error must remain available after leaving the except block."""
    good = _scenes(13, 24)
    svc = _Service("not a scene list", good)

    out = A.generate_script(
        svc,
        "why honeycombs are hexagonal",
        "gemini",
        target_duration=120,
    )

    assert svc.roles == ["script", "repair"]
    assert len(out) == len(good)
    assert 100 < A.estimate_script_seconds(out) < 140


def test_three_minute_script_repairs_until_it_is_within_tolerance():
    short = _scenes(19, 16)          # ~117s: supplied job's failure
    improved_but_short = _scenes(20, 20)  # ~154s, still below 90%
    good = _scenes(20, 24)           # ~185s
    svc = _Service(improved_but_short, good)

    out = A._enforce_target_duration(short, svc, "topic", "gemini", None, 180)

    assert svc.roles == ["repair", "repair"]
    assert 162 <= A.estimate_script_seconds(out) <= 207


def test_on_target_script_is_untouched():
    on_target = _scenes(13, 24)     # ~120s
    svc = _Service()                # any call would IndexError
    out = A._enforce_target_duration(on_target, svc, "topic", "gemini", None, 120)
    assert out == on_target
    assert svc.roles == []          # no wasted LLM call


def test_resize_kept_only_if_closer_to_target():
    """Bad resize attempts must never be rendered as if they met the target."""
    short = _scenes(9, 16)          # ~55s, off by 65
    worse = _scenes(40, 40)         # ~615s, off by 495
    svc = _Service(worse, worse)
    with pytest.raises(A.ScriptValidationError, match="duration"):
        A._enforce_target_duration(short, svc, "topic", "gemini", None, 120)


def test_resize_failure_fails_before_expensive_rendering():
    class Boom:
        roles = []
        def generate(self, **kw):
            raise RuntimeError("provider down")
        def __getattr__(self, n):
            raise RuntimeError("provider down")

    short = _scenes(9, 16)
    with pytest.raises(A.ScriptValidationError, match="duration"):
        A._enforce_target_duration(short, Boom(), "t", "gemini", None, 120)


def test_reviewed_three_minute_script_cannot_silently_render_at_two_minutes():
    short = _scenes(19, 16)
    with pytest.raises(A.ScriptValidationError, match="180"):
        A.validate_script_duration(short, 180)


def test_actual_two_minute_audio_cannot_pass_as_three_minutes():
    with pytest.raises(A.ScriptValidationError, match="Synthesized narration"):
        A.validate_duration_seconds(119.5, 180, source="Synthesized narration")


def test_overlong_script_is_tightened():
    long_script = _scenes(20, 40)   # ~307s
    tighter = _scenes(13, 24)       # ~120s
    svc = _Service(tighter)
    out = A._enforce_target_duration(long_script, svc, "t", "gemini", None, 120)
    assert len(out) == 13
