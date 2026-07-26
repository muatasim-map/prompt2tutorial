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


@pytest.mark.parametrize("target", [30, 60, 120])
def test_word_budget_matches_target(target):
    p = A._duration_profile(target)
    assert abs(p["total_words"] / A.TTS_WORDS_PER_SECOND - target) < 1.0


# --- the estimator reproduces the observed failure ------------------------ #

def test_estimator_matches_observed_failure():
    """9 scenes x ~16 words produced a real 57.9s video."""
    est = A.estimate_script_seconds(_scenes(9, 16))
    assert 50 < est < 62


# --- the gate ------------------------------------------------------------- #

def test_short_script_triggers_one_resize(monkeypatch):
    short = _scenes(9, 16)          # ~55s
    good = _scenes(13, 24)          # ~120s
    svc = _Service(good)

    out = A._enforce_target_duration(short, svc, "topic", "gemini", None, 120)

    assert svc.roles == ["repair"]                    # exactly one extra call
    assert len(out) == 13
    assert 100 < A.estimate_script_seconds(out) < 140


def test_on_target_script_is_untouched():
    on_target = _scenes(13, 24)     # ~120s
    svc = _Service()                # any call would IndexError
    out = A._enforce_target_duration(on_target, svc, "topic", "gemini", None, 120)
    assert out == on_target
    assert svc.roles == []          # no wasted LLM call


def test_resize_kept_only_if_closer_to_target():
    """A resize that overshoots worse than the original must be discarded."""
    short = _scenes(9, 16)          # ~55s, off by 65
    worse = _scenes(40, 40)         # ~615s, off by 495
    svc = _Service(worse)
    out = A._enforce_target_duration(short, svc, "topic", "gemini", None, 120)
    assert out == short             # original kept


def test_resize_failure_keeps_original():
    class Boom:
        roles = []
        def generate(self, **kw):
            raise RuntimeError("provider down")
        def __getattr__(self, n):
            raise RuntimeError("provider down")

    short = _scenes(9, 16)
    out = A._enforce_target_duration(short, Boom(), "t", "gemini", None, 120)
    assert out == short             # never fails the job over length


def test_overlong_script_is_tightened():
    long_script = _scenes(20, 40)   # ~307s
    tighter = _scenes(13, 24)       # ~120s
    svc = _Service(tighter)
    out = A._enforce_target_duration(long_script, svc, "t", "gemini", None, 120)
    assert len(out) == 13
