"""Tests for storyboard validation, diversity enforcement, and generation."""

import json

import pytest

import storyboard as sb_mod
from schemas import Storyboard, ScriptValidationError, parse_storyboard
from storyboard import check_diversity, generate_storyboard, required_distinct_approaches
import manim_generator


def _sb_scene(i, metaphor, composition, objects=None):
    return {
        "index": i,
        "learning_goal": f"goal {i}",
        "key_concept": f"concept {i}",
        "visual_metaphor": metaphor,
        "composition": composition,
        "primary_objects": objects or [f"object{i}a", f"object{i}b"],
        "primary_motion": f"motion {i}",
        "color_role": "blue for primary, orange for accent",
        "transition_from_prev": f"grows from scene {i-1}",
        "anti_repetition_notes": f"differs via {metaphor}",
        "visual_complexity": "medium",
    }


def _diverse_storyboard(n):
    scenes = [_sb_scene(i, f"metaphor-{i}", f"layout-{i}") for i in range(1, n + 1)]
    return {"global_style": {"palette": ["blue", "orange"]}, "scenes": scenes}


# --- schema validation ----------------------------------------------------- #

def test_valid_storyboard_parses():
    sb = parse_storyboard(_diverse_storyboard(6))
    assert len(sb.scenes) == 6
    assert sb.scenes[0].visual_metaphor == "metaphor-1"
    assert sb.entry_for(3).index == 3


def test_storyboard_blank_field_rejected():
    data = _diverse_storyboard(3)
    data["scenes"][1]["visual_metaphor"] = "   "
    with pytest.raises(ScriptValidationError):
        parse_storyboard(data)


def test_storyboard_primary_objects_from_string():
    data = _diverse_storyboard(2)
    data["scenes"][0]["primary_objects"] = "arrow, circle, label"
    sb = parse_storyboard(data)
    assert sb.scenes[0].primary_objects == ["arrow", "circle", "label"]


def test_storyboard_empty_objects_rejected():
    data = _diverse_storyboard(2)
    data["scenes"][0]["primary_objects"] = []
    with pytest.raises(ScriptValidationError):
        parse_storyboard(data)


def test_storyboard_bare_list_gets_default_style():
    sb = parse_storyboard(_diverse_storyboard(3)["scenes"])
    assert isinstance(sb.global_style.typography, str)


# --- diversity rules ------------------------------------------------------- #

def test_required_counts_60_and_120():
    assert required_distinct_approaches(60, 12) == 5
    assert required_distinct_approaches(120, 12) == 8
    # clamped to scene count when fewer scenes exist
    assert required_distinct_approaches(120, 4) == 4


def test_diverse_storyboard_has_no_violations():
    sb = parse_storyboard(_diverse_storyboard(8))
    assert check_diversity(sb, 120) == []


def test_too_few_distinct_approaches_flagged():
    # 8 scenes but only 2 distinct metaphors/layouts -> fails 120s requirement
    scenes = [_sb_scene(i, f"metaphor-{i % 2}", f"layout-{i % 2}") for i in range(1, 9)]
    sb = parse_storyboard({"scenes": scenes})
    violations = check_diversity(sb, 120)
    assert any("distinct visual approaches" in v for v in violations)


def test_adjacent_repeated_metaphor_flagged():
    scenes = [_sb_scene(1, "same", "layout-1"), _sb_scene(2, "same", "layout-2"),
              _sb_scene(3, "metaphor-3", "layout-3")]
    sb = parse_storyboard({"scenes": scenes})
    violations = check_diversity(sb, 60)
    assert any("repeat the same visual metaphor" in v for v in violations)


def test_adjacent_repeated_composition_flagged():
    scenes = [_sb_scene(1, "m1", "grid"), _sb_scene(2, "m2", "grid"),
              _sb_scene(3, "m3", "circle")]
    sb = parse_storyboard({"scenes": scenes})
    violations = check_diversity(sb, 60)
    assert any("repeat the same composition" in v for v in violations)


def test_composition_overused_flagged():
    scenes = [_sb_scene(i, f"m{i}", "grid" if i <= 3 else f"layout-{i}") for i in range(1, 7)]
    sb = parse_storyboard({"scenes": scenes})
    violations = check_diversity(sb, 60)
    assert any("used 3 times" in v for v in violations)


# --- generation with a fake provider (no live calls) ----------------------- #

class _FakeResult:
    def __init__(self, text, model="fake-model"):
        self.text = text
        self.model = model


class _FakeService:
    def __init__(self, texts):
        self.texts = list(texts)
        self.roles_called = []

    def generate(self, role, system, prompt, provider, client=None, response_schema=None):
        self.roles_called.append(role)
        return _FakeResult(self.texts.pop(0))


def _scenes(n):
    return [{"chapter": f"C{i}", "text": f"t{i}", "animation": f"a{i}",
             "objective": f"o{i}", "explanation": f"e{i}"} for i in range(1, n + 1)]


def test_generate_storyboard_diverse_no_repair():
    service = _FakeService([json.dumps(_diverse_storyboard(6))])
    sb = generate_storyboard(service, "topic", _scenes(6), "gemini", 60, "style")
    assert isinstance(sb, Storyboard)
    assert len(sb.scenes) == 6
    assert service.roles_called == ["storyboard"]  # single call, no repair
    assert getattr(sb, "_residual_violations") == []


def test_generate_storyboard_repairs_on_diversity_violation():
    bad = {"scenes": [_sb_scene(i, "same", "same") for i in range(1, 7)]}
    good = _diverse_storyboard(6)
    service = _FakeService([json.dumps(bad), json.dumps(good)])
    sb = generate_storyboard(service, "topic", _scenes(6), "gemini", 60, "style")
    # One repair happened, and the repaired storyboard is diverse.
    assert service.roles_called == ["storyboard", "storyboard"]
    assert getattr(sb, "_residual_violations") == []


# --- prompt construction receives storyboard + ledger ---------------------- #

def test_manim_prompt_includes_storyboard_and_ledger():
    entry = _sb_scene(2, "a coiled spring", "split-screen comparison")
    prompt = manim_generator._build_generation_prompt(
        text="narration", animation="anim desc",
        previous_context={"text": "prev", "metaphor": "prev metaphor", "ending_state": "ended"},
        audio_duration=8.0, chapter="Ch", objective="Obj", explanation="Expl",
        storyboard_entry=entry, global_style="restrained palette",
        ledger_summary="- Scene 1: metaphor='a river'; layout='flow'",
    )
    assert "a coiled spring" in prompt
    assert "split-screen comparison" in prompt
    assert "ALREADY-USED VISUAL CHOICES" in prompt
    assert "a river" in prompt
    assert "restrained palette" in prompt
    # Full prior source code must NOT be embedded (compact summary only).
    assert "Previous generated code" not in prompt
    assert "prev metaphor" in prompt
