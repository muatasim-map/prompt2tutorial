"""Contracts for the low-cost visual-quality layer.

These tests stay offline: they verify storyboard data, prompt direction,
reusable Manim primitives, and pre-render AST checks without invoking an LLM or
rendering a scene.
"""

import re

import manim_generator as mg
import schemas as S
import scene_checks
import storyboard
import video_generator as vg
import visual_primitives as vp


def _generation_prompt(entry=None):
    return re.sub(
        r"\s+",
        " ",
        mg._build_generation_prompt(
            text="When the value doubles, the output becomes four times larger.",
            animation="Show the relationship changing.",
            previous_context=None,
            audio_duration=9.0,
            chapter="Relationship",
            objective="Connect input and output.",
            explanation="A proportional visual explanation.",
            storyboard_entry=entry,
            global_style="Clean educational style.",
            ledger_summary="",
        ),
    )


def test_motion_grammar_maps_semantic_intents_to_easing():
    prompt = _generation_prompt()
    for intent in ("INTRODUCE", "CONNECT", "TRANSFORM", "COMPARE", "EMPHASIZE", "RESOLVE"):
        assert intent in prompt
    assert "rate_functions.ease_out_cubic" in prompt
    assert "rate_functions.ease_in_out_cubic" in prompt
    assert "there_and_back" in prompt
    assert "linear" in prompt


def test_narration_linked_beat_fields_survive_validation_and_prompting():
    entry = {
        "index": 1,
        "learning_goal": "See why the output quadruples.",
        "key_concept": "squared relationship",
        "visual_metaphor": "growing area",
        "composition": "input left, square right",
        "primary_objects": ["input bar", "square"],
        "primary_motion": "double the side and grow the area",
        "color_role": "blue input, yellow result",
        "transition_from_prev": "continue the same square",
        "anti_repetition_notes": "use an area transformation",
        "visual_beats": [{
            "at_seconds": 3.2,
            "narration_cue": "the output becomes four times larger",
            "action": "expand the square to four tiles",
            "objects": ["square"],
            "focus_object": "square",
            "emphasis": "primary",
        }],
    }

    scene = S.parse_storyboard([entry]).scenes[0]
    beat = scene.visual_beats[0]
    assert beat.narration_cue == "the output becomes four times larger"
    assert beat.focus_object == "square"
    assert beat.emphasis == "primary"

    prompt = _generation_prompt(scene.model_dump())
    assert 'Narration cue: "the output becomes four times larger"' in prompt
    assert "Focus: square" in prompt
    assert "Emphasis: primary" in prompt


def test_storyboard_requests_narration_linked_visual_beats():
    prompt = storyboard._build_prompt(
        "quadratic growth",
        [{
            "chapter": "Example",
            "text": "When the side doubles, the area becomes four times larger.",
            "objective": "Connect side length to area.",
            "explanation": "Area scales with the square of side length.",
        }],
        30,
        "Clean visual style.",
    )
    assert "narration_cue" in prompt
    assert "focus_object" in prompt
    assert "emphasis" in prompt


def test_typography_tokens_define_readable_roles():
    assert vp.TYPE_SCALE == {
        "hero": 60,
        "title": 48,
        "section": 36,
        "body": 30,
        "label": 26,
        "caption": 22,
    }
    assert vp.MIN_READABLE_FONT_SIZE == 22


def test_focus_primitives_are_available_to_generated_scenes():
    assert callable(vp.focus_on)
    assert callable(vp.restore_focus)
    prompt = _generation_prompt()
    assert "focus_on" in prompt
    assert "restore_focus" in prompt


def test_prompt_sets_composition_limits_and_persistent_object_rule():
    prompt = _generation_prompt()
    assert "ONE dominant focal element" in prompt
    assert "no more than 6 simultaneously important objects" in prompt
    assert "PERSISTENT OBJECT IDENTITY" in prompt
    assert "TransformFromCopy" in prompt


def test_static_quality_checks_flag_small_dense_long_text():
    code = """
from manim import *
class DenseScene(Scene):
    def construct(self):
        tiny = Text("tiny", font_size=16)
        long = Text("x" * 130, font_size=30)
        a = Text("a", font_size=24)
        b = Text("b", font_size=24)
        c = Text("c", font_size=24)
        d = Text("d", font_size=24)
        e = Text("e", font_size=24)
        self.add(tiny, long, a, b, c, d, e)
"""
    facts = scene_checks.analyze_scene_code(code)
    assert scene_checks.FLAG_SMALL_TEXT in facts.static_quality_flags
    assert scene_checks.FLAG_TEXT_DENSITY in facts.static_quality_flags
    assert scene_checks.FLAG_LONG_TEXT in facts.static_quality_flags


def test_static_quality_checks_accept_concise_readable_text():
    code = """
from manim import *
class ClearScene(Scene):
    def construct(self):
        title = Text("Quadratic growth", font_size=48)
        label = Text("Area", font_size=26)
        self.add(title, label)
"""
    facts = scene_checks.analyze_scene_code(code)
    assert facts.static_quality_flags == []


def test_static_code_flags_are_added_to_scene_qa_report():
    report = {"index": 1, "flags": []}
    code = """
from manim import *
class TinyText(Scene):
    def construct(self):
        self.add(Text("Unreadable", font_size=14))
"""
    vg._apply_static_code_flags(report, code)
    assert scene_checks.FLAG_SMALL_TEXT in report["flags"]
    assert report["static_code_quality"]["text_mobject_count"] == 1


def test_matching_shape_transforms_count_as_persistent_motion():
    code = """
from manim import *
class EvolvingScene(Scene):
    def construct(self):
        old = Text("x")
        new = Text("x + 1")
        self.play(TransformMatchingShapes(old, new))
"""
    assert scene_checks.analyze_scene_code(code).uses_any_morph
