"""Prompt-quality tests for maths/physics visual reasoning and adaptive dimensionality.

Contract tests on the INSTRUCTIONS sent to the model (no API calls). They guard:
* adaptive 2D-default / 2.5D-layered / narrow-3D decision guidance;
* broad maths + physics capability coverage;
* that nothing hardcodes a topic or imposes a fixed template;
* that the repair prompt preserves the dimensionality + visual intent;
* that the optional storyboard `dimension` field stays backward compatible.

Every 3D construct referenced was render-verified in this environment before being
permitted (see the offline smoke renders accompanying this change).
"""

import re

import pytest

import manim_generator as mg
import schemas as S


def _sb(**over):
    base = dict(index=1, learning_goal="g", key_concept="k", visual_metaphor="m",
                composition="c", primary_objects=["a"], primary_motion="p",
                color_role="r", transition_from_prev="t", anti_repetition_notes="n")
    base.update(over)
    return base


def _p(primary="general", secondary=None, dimension=None):
    """Routed prompt, whitespace-normalized (the guidance text is hard-wrapped)."""
    return re.sub(r"\s+", " ", mg._build_generation_prompt(
        text="narration", animation="anim", previous_context=None, audio_duration=9.0,
        chapter="C", objective="O", explanation="E",
        storyboard_entry=_sb(primary_domain_tag=primary,
                             secondary_domain_tags=secondary or [],
                             dimension=dimension),
        global_style="style", ledger_summary=""))


@pytest.fixture
def gen_prompt():
    """An untagged scene -> the safe 'general' route."""
    return re.sub(r"\s+", " ", mg._build_generation_prompt(
        text="narration", animation="anim", previous_context=None, audio_duration=9.0,
        chapter="C", objective="O", explanation="E", storyboard_entry=None,
        global_style="style", ledger_summary=""))


@pytest.fixture
def fix_prompt():
    return mg._build_fix_prompt("code", "error", "MyScene")


# --- adaptive dimensionality ---------------------------------------------- #

def test_dimension_block_states_this_scene_dimension(gen_prompt):
    """Every scene is told its dimension explicitly, not asked to re-decide."""
    assert "DIMENSION FOR THIS SCENE: 2D" in gen_prompt


def test_depth_must_explain_not_decorate():
    """The single hard rule survives the rebalance toward more 2.5D/3D."""
    p = _p("linear_algebra", dimension="3d")
    assert "depth must EXPLAIN something" in p
    assert "forbidden" in p


def test_2d_scene_is_invited_to_lift_into_layered_depth():
    """2.5D stays available in a flat scene - the tag is a centre of gravity."""
    p = _p("algebra")
    assert "LIFT INTO DEPTH" in p
    assert "set_z_index" in p


def test_2p5d_is_layered_not_threed(gen_prompt):
    assert "2.5D LAYERING" in gen_prompt
    assert "set_z_index" in gen_prompt
    # 2.5D must stay in a normal Scene, explicitly
    assert "NOT a 3D scene" in gen_prompt


def test_true_3d_recipe_uses_only_verified_api():
    p = _p("linear_algebra", dimension="3d")
    assert "TRUE 3D RECIPE" in p
    for api in ("ThreeDScene", "set_camera_orientation", "ThreeDAxes", "Line3D",
                "Surface", "Cube"):
        assert api in p
    # the render-slow / unsafe constructs are explicitly discouraged/banned
    assert "Arrow3D" in p                   # named so the model avoids it
    assert "NO begin_ambient_camera_rotation" in p


def test_2d_scene_told_to_subclass_scene_and_never_sees_3d_api():
    """A flat scene must not even be shown the 3D vocabulary."""
    p = _p("algebra")
    assert "MUST inherit from Scene" in p
    for api in ("ThreeDScene", "set_camera_orientation", "ThreeDAxes"):
        assert api not in p, api


def test_3d_scene_told_to_subclass_threedscene():
    p = _p("linear_algebra", dimension="3d")
    assert "MUST inherit from ThreeDScene" in p


def test_dimension_from_storyboard_reaches_generation():
    assert "DIMENSION FOR THIS SCENE: 2.5D" in _p("geometry", dimension="2.5d")
    assert "TRUE 3D RECIPE" in _p("geometry", dimension="3d")
    assert "DIMENSION FOR THIS SCENE: 2D" in _p("geometry")   # absent -> 2d


# --- maths coverage -------------------------------------------------------- #

def test_maths_calculus_coverage():
    low = _p("calculus").lower()
    for concept in ("tangent", "integration", "riemann", "parametric", "limit"):
        assert concept in low, concept


def test_maths_linear_algebra_coverage():
    low = _p("linear_algebra").lower()
    for concept in ("tip-to-tail", "matrix", "determinant", "eigenvector"):
        assert concept in low, concept


def test_maths_broad_coverage():
    """Breadth still exists - it is now reachable per route, not all at once."""
    assert "probability" in _p("probability_statistics").lower()
    assert "distribution" in _p("probability_statistics").lower()
    assert "derivation" in _p("algebra").lower()
    assert "unit circle" in _p("geometry").lower()


# --- physics coverage ------------------------------------------------------ #

def test_physics_coverage():
    mech = _p("mechanics").lower()
    for concept in ("free-body", "kinematics", "circular motion"):
        assert concept in mech, concept
    assert "superposition" in _p("waves").lower()
    assert "field line" in _p("electricity").lower()
    assert "ray diagram" in _p("optics").lower()


def test_physics_correctness_is_mandated():
    """Specialist visuals must represent real relationships, not decoration."""
    p = _p("mechanics")
    assert "ACCURACY" in p
    assert "never decoration" in p
    assert "must not imply a false claim" in p


# --- no hardcoded templates / topic routing -------------------------------- #

def test_no_hardcoded_topic_routing():
    """Domain guidance must be framed as inference, never a fixed template."""
    p = _p("calculus")
    assert "not a template" in p
    assert "infer the right metaphor" in p.lower()
    # do not force every scene to use the domain constructs
    assert "never force a listed technique into a scene that does not need it" in p


def test_generation_prompt_is_not_topic_specific():
    """The same prompt scaffold must be produced regardless of subject matter."""
    a = mg._build_generation_prompt(
        text="derivative of x squared", animation="tangent", previous_context=None,
        audio_duration=9.0, chapter="Calculus", objective="o", explanation="e",
        storyboard_entry=None, global_style="s", ledger_summary="")
    b = mg._build_generation_prompt(
        text="magnetic induction", animation="coil", previous_context=None,
        audio_duration=9.0, chapter="Physics", objective="o", explanation="e",
        storyboard_entry=None, global_style="s", ledger_summary="")
    # The always-on scaffold is identical regardless of subject; only the routed
    # domain slice differs (and with no storyboard both route to "general").
    for marker in ("DIMENSION FOR THIS SCENE", "DOMAIN VISUAL REASONING",
                   "PURPOSEFUL VISUAL PROGRESSION", "DO NOT INVENT API"):
        assert marker in a and marker in b


# --- derivation continuity (algebra) --------------------------------------- #

def test_derivation_uses_transform_not_disconnected_slides():
    low = _p("algebra").lower()
    assert "transformfromcopy" in low
    assert "never cut to a disconnected new text slide" in low


# --- repair prompt --------------------------------------------------------- #

def test_repair_preserves_dimensionality(fix_prompt):
    low = fix_prompt.lower()
    assert "dimensionality choice" in low
    assert "a threedscene stays a" in low          # base class preserved
    assert "do not downgrade a threedscene to flat 2d" in low


def test_repair_knows_verified_3d_api(fix_prompt):
    for api in ("set_camera_orientation", "ThreeDAxes", "Line3D"):
        assert api in fix_prompt
    assert "no ambient rotation" in fix_prompt.lower()


def test_repair_still_preserves_visual_intent(fix_prompt):
    assert "PRESERVE THE LESSON" in fix_prompt
    assert "FAILED repair" in fix_prompt


# --- schema: optional dimension field -------------------------------------- #

def test_dimension_field_optional_backward_compatible():
    scene = S.parse_storyboard([_sb()]).scenes[0]
    assert scene.dimension is None          # absent by default


@pytest.mark.parametrize("raw,expect", [
    ("2d", "2d"), ("2D", "2d"), ("two-dimensional", "2d"), ("flat", "2d"),
    ("2.5d", "2.5d"), ("2.5D layered", "2.5d"), ("layered", "2.5d"),
    ("3d", "3d"), ("3D", "3d"), ("three-dimensional", "3d"), ("use 3d", "3d"),
    ("", None), ("nonsense", None),
])
def test_dimension_normalization(raw, expect):
    scene = S.parse_storyboard([_sb(dimension=raw)]).scenes[0]
    assert scene.dimension == expect


def test_storyboard_prompt_requests_dimension_and_defaults_2d():
    from storyboard import _build_prompt
    p = _build_prompt("topic", [{"chapter": "c", "text": "t", "objective": "o",
                                 "explanation": "e"}], 60, "style")
    assert "dimension" in p
    # rebalanced: depth is invited where it teaches, not discouraged by default
    assert "2.5d (layered 2D) is UNDER-USED" in p
    assert "depth must EXPLAIN something" in p
