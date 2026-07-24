"""Controlled multi-tag domain routing: schema, selection, and composition.

Routing exists to cut instruction dilution, not prompt size: a calculus scene
that also reads magnetism and astrophysics rules follows its own rules less
consistently. These tests pin the contract — valid single/multi tags, the
`general` fallback, rejection of unknown tags, deterministic de-duplication,
bounded tag counts, backward compatibility with stored scenes, and that a
prompt contains ONLY the modules it selected.
"""

import re

import pytest
from pydantic import ValidationError

import domain_guidance as dg
import manim_generator as mg
import schemas as S


def _sb(**over):
    base = dict(index=1, learning_goal="g", key_concept="k", visual_metaphor="m",
                composition="c", primary_objects=["a"], primary_motion="p",
                color_role="r", transition_from_prev="t", anti_repetition_notes="n")
    base.update(over)
    return base


def _scene(**over):
    return S.parse_storyboard([_sb(**over)]).scenes[0]


def _prompt(entry):
    return re.sub(r"\s+", " ", mg._build_generation_prompt(
        text="n", animation="a", previous_context=None, audio_duration=9.0,
        chapter="c", objective="o", explanation="e", storyboard_entry=entry,
        global_style="s", ledger_summary=""))


# --- schema: valid routing -------------------------------------------------- #

def test_valid_single_tag():
    s = _scene(primary_domain_tag="calculus")
    assert s.primary_domain_tag == "calculus"
    assert s.secondary_domain_tags == []
    assert s.domain_tags() == ["calculus"]


def test_valid_multi_tag():
    s = _scene(primary_domain_tag="magnetism",
               secondary_domain_tags=["electricity", "mechanics"])
    assert s.domain_tags() == ["magnetism", "electricity", "mechanics"]


def test_tag_normalization_is_forgiving_about_shape_not_vocabulary():
    """Case/spacing/hyphen variants normalize; unknown words still fail."""
    s = _scene(primary_domain_tag="Linear-Algebra",
               secondary_domain_tags=["  GEOMETRY  "])
    assert s.domain_tags() == ["linear_algebra", "geometry"]


# --- schema: fallback and backward compatibility ---------------------------- #

def test_general_is_the_default_when_absent():
    """Scenes stored before routing existed must stay valid."""
    s = _scene()
    assert s.primary_domain_tag == "general"
    assert s.secondary_domain_tags == []


def test_blank_primary_falls_back_to_general():
    assert _scene(primary_domain_tag="").primary_domain_tag == "general"
    assert _scene(primary_domain_tag=None).primary_domain_tag == "general"


def test_existing_stored_storyboard_still_parses():
    """A full pre-routing storyboard payload must load unchanged."""
    sb = S.parse_storyboard([_sb(index=1), _sb(index=2, dimension="3d")])
    assert [s.primary_domain_tag for s in sb.scenes] == ["general", "general"]
    assert sb.scenes[1].dimension == "3d"


# --- schema: rejection and de-duplication ----------------------------------- #

@pytest.mark.parametrize("bad", ["chemistry", "biology", "maths", "physics", "3d"])
def test_unknown_tag_is_rejected(bad):
    with pytest.raises((ValidationError, S.ScriptValidationError)):
        _scene(primary_domain_tag=bad)


def test_unknown_secondary_tag_is_rejected():
    with pytest.raises((ValidationError, S.ScriptValidationError)):
        _scene(primary_domain_tag="calculus", secondary_domain_tags=["astrology"])


def test_duplicate_secondaries_are_deduplicated():
    s = _scene(primary_domain_tag="waves",
               secondary_domain_tags=["geometry", "geometry"])
    assert s.secondary_domain_tags == ["geometry"]


def test_primary_repeated_in_secondaries_is_dropped():
    s = _scene(primary_domain_tag="calculus",
               secondary_domain_tags=["calculus", "algebra"])
    assert s.domain_tags() == ["calculus", "algebra"]


def test_general_is_never_a_secondary():
    """'general' means 'no specialist guidance' - pairing it is contradictory."""
    s = _scene(primary_domain_tag="optics", secondary_domain_tags=["general"])
    assert s.domain_tags() == ["optics"]


def test_too_many_secondary_tags_rejected():
    with pytest.raises((ValidationError, S.ScriptValidationError)):
        _scene(primary_domain_tag="mechanics",
               secondary_domain_tags=["waves", "optics", "thermal", "algebra"])


def test_max_total_tags_allowed():
    """A real cross-domain A-level scene can need all four."""
    s = _scene(primary_domain_tag="mechanics",
               secondary_domain_tags=["calculus", "geometry", "algebra"])
    assert len(s.domain_tags()) == S.MAX_TOTAL_DOMAIN_TAGS == 4


def test_four_tag_scene_gets_all_four_modules():
    p = _prompt(_sb(primary_domain_tag="mechanics",
                    secondary_domain_tags=["calculus", "geometry", "algebra"]))
    assert "free-body" in p                  # mechanics
    assert "Riemann sum" in p                 # calculus
    assert "Cosine rule is about the INCLUDED angle" in p   # geometry
    assert "Sequences & series" in p          # algebra
    assert "Hertzsprung" not in p             # and nothing else


# --- composition: only the selected modules ships --------------------------- #

def test_calculus_scene_gets_no_unrelated_physics():
    """The core dilution guard: a calculus scene must not read magnetism etc."""
    p = _prompt(_sb(primary_domain_tag="calculus"))
    assert "Riemann sum" in p
    for foreign in ("Cross(Circle(", "Hertzsprung", "photoelectric",
                    "free-body", "rarefactions"):
        assert foreign not in p, foreign


def test_electromagnetism_scene_gets_both_modules():
    p = _prompt(_sb(primary_domain_tag="magnetism",
                    secondary_domain_tags=["electricity"]))
    assert "Cross(Circle(" in p          # magnetism
    assert "I-V characteristics" in p    # electricity
    assert "Hertzsprung" not in p        # nothing else


def test_three_tag_scene_gets_exactly_those_three():
    p = _prompt(_sb(primary_domain_tag="magnetism",
                    secondary_domain_tags=["electricity", "mechanics"]))
    assert "Lenz" in p                   # magnetism
    assert "Capacitors" in p             # electricity
    assert "free-body" in p              # mechanics
    assert "Hertzsprung" not in p and "photoelectric" not in p


def test_general_scene_gets_no_specialist_module():
    p = _prompt(_sb(primary_domain_tag="general"))
    assert "GENERAL SCENE" in p
    # NB: probe domain GUIDANCE phrasing, not API names - get_riemann_rectangles
    # lives in the always-on verified-Axes whitelist and is correctly global.
    for specialist in ("Riemann sum", "Cross(Circle(", "free-body", "Hertzsprung",
                       "I-V characteristics", "unit circle"):
        assert specialist not in p, specialist


def test_missing_storyboard_entry_routes_to_general():
    p = _prompt(None)
    assert "GENERAL SCENE" in p
    assert "free-body" not in p


def test_core_rules_are_always_on_regardless_of_tag():
    """Layer A must never be routed away - safety/quality/API rules are global."""
    for tag in ("general", "calculus", "astrophysics"):
        p = _prompt(_sb(primary_domain_tag=tag))
        for core in ("THESE ARE THE ONLY Axes METHODS THAT EXIST",
                     "DO NOT INVENT API",
                     "NEVER use MathTex",
                     "PURPOSEFUL VISUAL PROGRESSION",
                     "ON-SCREEN TEXT RULES",
                     "VISUAL QUALITY REQUIREMENTS",
                     "RESPONSE FORMAT (JSON)"):
            assert core in p, f"{core} missing for {tag}"


# --- composition: dimension guidance is conditional (layer D) --------------- #

def test_2d_scene_gets_no_3d_api():
    p = _prompt(_sb(primary_domain_tag="calculus", dimension="2d"))
    assert "DIMENSION FOR THIS SCENE: 2D" in p
    for api in ("ThreeDAxes", "set_camera_orientation", "Line3D", "TRUE 3D RECIPE"):
        assert api not in p, api


def test_3d_scene_gets_the_verified_recipe():
    p = _prompt(_sb(primary_domain_tag="linear_algebra", dimension="3d"))
    assert "TRUE 3D RECIPE" in p
    for api in ("ThreeDScene", "set_camera_orientation", "ThreeDAxes", "Line3D"):
        assert api in p, api


def test_2p5d_scene_is_layered_not_3d():
    p = _prompt(_sb(primary_domain_tag="geometry", dimension="2.5d"))
    assert "DIMENSION FOR THIS SCENE: 2.5D" in p
    assert "set_z_index" in p
    assert "TRUE 3D RECIPE" not in p
    assert "ThreeDAxes" not in p


def test_absent_dimension_defaults_to_2d():
    p = _prompt(_sb(primary_domain_tag="algebra"))
    assert "DIMENSION FOR THIS SCENE: 2D" in p
    assert "ThreeDAxes" not in p


# --- repair prompt preserves routing ---------------------------------------- #

def test_repair_prompt_carries_the_same_domain_modules():
    fix = re.sub(r"\s+", " ", mg._build_fix_prompt(
        "code", "err", "S",
        domain_tags=["magnetism", "electricity"],
        scene_intent=_sb(primary_domain_tag="magnetism")))
    assert "Cross(Circle(" in fix
    assert "I-V characteristics" in fix
    assert "Hertzsprung" not in fix


def test_repair_prompt_carries_scene_intent():
    fix = mg._build_fix_prompt("code", "err", "S", domain_tags=["calculus"],
                               scene_intent=_sb(learning_goal="understand limits",
                                                visual_metaphor="a shrinking secant"))
    assert "understand limits" in fix
    assert "a shrinking secant" in fix
    assert "PRESERVE THE LESSON" in fix


def test_repair_prompt_general_scene_has_no_domain_block():
    fix = mg._build_fix_prompt("code", "err", "S", domain_tags=["general"])
    assert "GENERAL SCENE" not in fix
    assert "PRESERVE THE LESSON" in fix          # core repair rules still there


def test_repair_prompt_backward_compatible_without_routing():
    """Old call sites pass no routing at all and must still work."""
    fix = mg._build_fix_prompt("code", "err", "MyScene")
    assert "PRESERVE THE LESSON" in fix
    assert "MyScene" in fix


# --- helper-level behaviour ------------------------------------------------- #

def test_normalize_tags_drops_unknowns_defensively():
    assert dg.normalize_tags(["calculus", "nonsense"]) == ["calculus"]
    assert dg.normalize_tags([]) == ["general"]
    assert dg.normalize_tags(None) == ["general"]
    assert dg.normalize_tags(["general", "waves"]) == ["waves"]


def test_every_taxonomy_tag_has_a_module():
    for tag in S.DOMAIN_TAGS:
        assert dg.build_domain_section([tag]).strip()


def test_storyboard_prompt_requests_domain_tags():
    from storyboard import _build_prompt
    p = _build_prompt("topic", [{"chapter": "c", "text": "t", "objective": "o",
                                 "explanation": "e"}], 60, "style")
    assert "primary_domain_tag" in p
    assert "secondary_domain_tags" in p
    assert "DOMAIN TAGS" in p
    for tag in ("calculus", "magnetism", "general"):
        assert tag in p
    # selection must be reasoned, not keyword-matched
    assert "not from surface words" in p
    # overlap must be invited, not discouraged - real A-level scenes span domains
    assert "REAL TOPICS OVERLAP" in p
    assert "Under-tagging a cross-domain scene is a" in p
