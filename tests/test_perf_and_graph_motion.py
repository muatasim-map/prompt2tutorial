"""Low-risk performance + animation improvements.

* compile timeout scales to render quality (fail-fast on hangs; never threatens a
  normal render which finishes well under the bound);
* graph/plot scenes are directed to keep moving with render-verified motion
  patterns instead of drawing a curve and freezing.
"""

import importlib

import pytest

import concat_video as cv
import manim_generator as mg


# --- adaptive compile timeout ---------------------------------------------- #

@pytest.mark.parametrize("quality,expected", [("low", 120), ("medium", 240), ("high", 300)])
def test_timeout_scales_with_quality(monkeypatch, quality, expected):
    monkeypatch.setenv("MANIM_QUALITY", quality)
    monkeypatch.delenv("MANIM_COMPILE_TIMEOUT", raising=False)
    assert cv.resolve_compile_timeout() == expected


def test_timeout_env_override(monkeypatch):
    monkeypatch.setenv("MANIM_COMPILE_TIMEOUT", "150")
    assert cv.resolve_compile_timeout() == 150


def test_timeout_has_a_floor(monkeypatch):
    monkeypatch.setenv("MANIM_COMPILE_TIMEOUT", "5")
    assert cv.resolve_compile_timeout() == 60          # never absurdly short


def test_timeout_bad_override_falls_back_to_quality(monkeypatch):
    monkeypatch.setenv("MANIM_QUALITY", "low")
    monkeypatch.setenv("MANIM_COMPILE_TIMEOUT", "not-a-number")
    assert cv.resolve_compile_timeout() == 120


def test_compile_video_default_timeout_is_quality_aware(monkeypatch):
    """None default must resolve via the quality-aware resolver, not a hard 300."""
    import inspect
    sig = inspect.signature(cv.compile_video)
    assert sig.parameters["timeout"].default is None


# --- graph-scene motion guidance ------------------------------------------- #

@pytest.fixture
def gen_prompt():
    return mg._build_generation_prompt(
        text="derivative", animation="tangent", previous_context=None, audio_duration=9.0,
        chapter="Calculus", objective="o", explanation="e", storyboard_entry=None,
        global_style="s", ledger_summary="")


def test_graph_scenes_directed_to_keep_moving(gen_prompt):
    assert "KEEP GRAPH SCENES MOVING" in gen_prompt
    # a plain draw-then-freeze plot is explicitly called out as a slideshow
    assert "then freezes is a" in gen_prompt.lower()


def test_graph_motion_patterns_are_verified_api_only(gen_prompt):
    """Every recommended pattern uses APIs render-verified in this environment."""
    block = gen_prompt.split("KEEP GRAPH SCENES MOVING")[1].split("2.5D LAYERING")[0]
    for api in ("MoveAlongPath", "i2gp", "get_vertical_line",
                "get_secant_slope_group", "get_riemann_rectangles", "get_area"):
        assert api in block, api
    # must NOT smuggle in the out-of-scope reactive APIs
    assert "always_redraw" not in block
    assert "ValueTracker" not in block


def test_small_motion_not_mistaken_for_static(gen_prompt):
    """Guards the measured pitfall: a tracing dot is engaging but low-pixel-change."""
    assert "small, purposeful motion" in gen_prompt.lower()
