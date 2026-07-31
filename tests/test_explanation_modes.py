"""Explanation-mode and curriculum contracts across the prompt pipeline."""

import pytest

import animations
import manim_generator
import storyboard
from learning_profiles import (
    EXPLANATION_MODES,
    build_manim_guidance,
    build_script_guidance,
    build_storyboard_guidance,
    normalize_curriculum_profile,
    normalize_explanation_mode,
)


EXPECTED_MODES = (
    "general",
    "conceptual_intuition",
    "worked_example",
    "derivation_visual_proof",
    "graphical_exploration",
    "exam_technique",
    "misconception_repair",
    "revision_recap",
)


def test_eight_explanation_modes_are_stable():
    assert EXPLANATION_MODES == EXPECTED_MODES


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, "general"),
        ("auto", "general"),
        ("Conceptual Intuition", "conceptual_intuition"),
        ("worked-example", "worked_example"),
        ("visual proof", "derivation_visual_proof"),
        ("graph exploration", "graphical_exploration"),
        ("exam", "exam_technique"),
        ("misconception", "misconception_repair"),
        ("revision", "revision_recap"),
    ],
)
def test_mode_aliases_normalize(raw, expected):
    assert normalize_explanation_mode(raw) == expected


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError, match="Unknown explanation mode"):
        normalize_explanation_mode("cinematic magic")


def test_general_mode_preserves_existing_prompt_behavior():
    assert build_script_guidance("general", "general") == ""
    assert build_storyboard_guidance("general", "general") == ""
    assert build_manim_guidance("general", "general") == ""


@pytest.mark.parametrize("mode", EXPECTED_MODES[1:])
def test_each_specialized_mode_has_stage_specific_guidance(mode):
    script = build_script_guidance(mode, "general")
    storyboard_text = build_storyboard_guidance(mode, "general")
    manim = build_manim_guidance(mode, "general")

    assert "EXPLANATION MODE" in script
    assert "VISUAL DIRECTION FOR MODE" in storyboard_text
    assert "SCENE EXECUTION FOR MODE" in manim


def test_aqa_profile_is_explicit_and_mentions_overarching_themes():
    assert normalize_curriculum_profile("AQA A-Level Maths") == "aqa_a_level_mathematics"

    guidance = build_script_guidance("general", "aqa_a_level_mathematics")

    assert "AQA A-level Mathematics (7357)" in guidance
    assert "mathematical argument" in guidance
    assert "problem solving" in guidance
    assert "mathematical modelling" in guidance


def test_script_prompt_receives_mode_and_aqa_contract():
    prompt = animations._build_prompt(
        "Newton-Raphson method",
        60,
        explanation_mode="exam_technique",
        curriculum_profile="aqa_a_level_mathematics",
    )

    assert "EXAM TECHNIQUE" in prompt
    assert "AQA A-level Mathematics (7357)" in prompt


def test_length_repair_preserves_mode_and_curriculum_contract():
    prompt = animations._build_length_repair_prompt(
        "Newton-Raphson method",
        60,
        [{"chapter": "Roots", "text": "Short", "animation": "Draw a tangent"}],
        4,
        explanation_mode="exam_technique",
        curriculum_profile="aqa_a_level_mathematics",
    )

    assert "EXAM TECHNIQUE" in prompt
    assert "AQA A-level Mathematics (7357)" in prompt


def test_storyboard_prompt_receives_visual_mode_contract():
    prompt = storyboard._build_prompt(
        "Newton-Raphson method",
        [{"chapter": "Roots", "text": "Find a root", "objective": "Apply Newton-Raphson"}],
        60,
        "clear",
        explanation_mode="graphical_exploration",
        curriculum_profile="aqa_a_level_mathematics",
    )

    assert "GRAPHICAL EXPLORATION" in prompt
    assert "cobweb" in prompt.lower() or "graph" in prompt.lower()
    assert "AQA 7357" in prompt


def test_manim_prompt_receives_scene_execution_mode():
    prompt = manim_generator._build_generation_prompt(
        text="Explain why Newton-Raphson converges",
        animation="Show successive tangents",
        previous_context=None,
        audio_duration=10,
        chapter="Numerical methods",
        objective="Connect tangent geometry to iteration",
        explanation="Each tangent intercept becomes the next estimate",
        storyboard_entry=None,
        global_style="clear",
        ledger_summary="",
        explanation_mode="conceptual_intuition",
        curriculum_profile="aqa_a_level_mathematics",
    )

    assert "CONCEPTUAL INTUITION" in prompt
    assert "AQA 7357" in prompt


def test_explicit_camera_plan_enables_one_purposeful_2d_camera_move():
    prompt = manim_generator._build_generation_prompt(
        text="Inspect the tangent near the stationary point",
        animation="Zoom toward the tangent and derivative value",
        previous_context=None,
        audio_duration=8,
        chapter="Differentiation",
        objective="Connect local gradient to the derivative",
        explanation="The camera move reveals local behavior",
        storyboard_entry={
            "dimension": "2d",
            "camera_plan": "One slow zoom toward the tangent, then restore",
        },
    )

    assert "MUST inherit from MovingCameraScene" in prompt
    assert "self.camera.frame.animate" in prompt


def test_no_camera_plan_keeps_the_reliable_fixed_camera_default():
    prompt = manim_generator._build_generation_prompt(
        text="Show a quadratic",
        animation="Draw the curve",
        previous_context=None,
        audio_duration=8,
        chapter="Graphs",
        objective="Recognize the parabola",
        explanation="A single graph needs no camera move",
        storyboard_entry={"dimension": "2d", "camera_plan": None},
    )

    assert "MUST inherit from Scene" in prompt
    assert "DO NOT use self.camera.frame" in prompt
