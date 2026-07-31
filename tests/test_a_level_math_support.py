import pytest

import domain_guidance as dg
import manim_generator as mg
from schemas import A_LEVEL_MATH_TOPICS, StoryboardScene
from storyboard import _build_prompt


def _entry(topic=None):
    return {
        "index": 1,
        "learning_goal": "Understand the method",
        "key_concept": "Core idea",
        "visual_metaphor": "An evolving mathematical diagram",
        "composition": "Axes and working",
        "primary_objects": ["axes", "curve"],
        "primary_motion": "transform the representation",
        "color_role": "stable colors for corresponding quantities",
        "transition_from_prev": "continue",
        "anti_repetition_notes": "new composition",
        "primary_domain_tag": "calculus",
        "secondary_domain_tags": [],
        "a_level_math_topic": topic,
    }


def _generation_prompt(topic):
    return mg._build_generation_prompt(
        text="Explain the idea",
        animation="Show why it works",
        previous_context=None,
        audio_duration=8.0,
        chapter="A-level Mathematics",
        objective="Build exam-ready understanding",
        explanation="Connect representation, method, and interpretation",
        storyboard_entry=_entry(topic),
        global_style="clear",
        ledger_summary="",
    )


def test_all_requested_a_level_topics_are_first_class():
    assert A_LEVEL_MATH_TOPICS == (
        "algebra_functions",
        "graphs",
        "coordinate_geometry",
        "sequences_series",
        "trigonometry",
        "exponentials_logarithms",
        "differentiation",
        "integration",
        "numerical_methods",
        "vectors",
        "statistics",
    )


@pytest.mark.parametrize("topic", A_LEVEL_MATH_TOPICS)
def test_each_a_level_topic_has_specialist_guidance(topic):
    section = dg.build_a_level_math_section(topic)

    assert section
    assert "A-LEVEL MATHEMATICS FOCUS" in section


def test_storyboard_scene_normalizes_a_level_topic():
    scene = StoryboardScene.model_validate(_entry("Coordinate Geometry"))

    assert scene.a_level_math_topic == "coordinate_geometry"


def test_unknown_a_level_topic_is_rejected():
    with pytest.raises(ValueError, match="unknown A-level mathematics topic"):
        StoryboardScene.model_validate(_entry("astrology"))


def test_older_storyboards_remain_valid_without_topic():
    scene = StoryboardScene.model_validate(_entry(None))

    assert scene.a_level_math_topic is None


def test_topic_guidance_is_routed_without_unrelated_syllabus_blocks():
    prompt = _generation_prompt("numerical_methods")

    assert "Newton-Raphson" in prompt
    assert "cobweb" in prompt.lower()
    assert "hypothesis test" not in prompt.lower()


def test_repair_prompt_preserves_exact_a_level_topic():
    prompt = mg._build_fix_prompt(
        "from manim import *",
        "NameError",
        "Scene1",
        domain_tags=["calculus"],
        scene_intent=_entry("integration"),
    )

    assert "signed accumulation" in prompt
    assert "constant of integration" in prompt


def test_storyboard_prompt_requests_controlled_a_level_topic():
    prompt = _build_prompt(
        "A-level mathematics",
        [{"chapter": "c", "text": "t", "objective": "o", "explanation": "e"}],
        60,
        "style",
    )

    assert "a_level_math_topic" in prompt
    for topic in A_LEVEL_MATH_TOPICS:
        assert topic in prompt
