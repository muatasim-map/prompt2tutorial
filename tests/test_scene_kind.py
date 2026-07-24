"""Worked-problem scene kind + the trig rule/bearing additions.

`scene_kind` is a bounded, storyboard-selected form tag (orthogonal to
`narrative_role`, which says where a scene sits in the arc). It exists because
three consecutive realistic A-level prompts — SOH-CAH-TOA, sine/cosine rules,
and any "solve this" request — all needed staged *calculation* direction that
nothing in the pipeline provided. The staging is deliberately domain-neutral:
the same skeleton serves a trig distance question, a suvat problem or a
titration, so it lives in manim_generator rather than inside one domain module.

Every construction referenced was render-verified before being written into the
guidance: the worked-problem staging (22.0s), cosine-rule-to-Pythagoras sweep
(17.8s) and the bearings figure (16.7s) all rendered rc=0.
"""

import re

import pytest
from pydantic import ValidationError

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


def _prompt(**over):
    return re.sub(r"\s+", " ", mg._build_generation_prompt(
        text="n", animation="a", previous_context=None, audio_duration=9.0,
        chapter="c", objective="o", explanation="e", storyboard_entry=_sb(**over),
        global_style="s", ledger_summary=""))


# --- schema ----------------------------------------------------------------- #

def test_scene_kind_defaults_to_explanation():
    assert _scene().scene_kind == "explanation"


def test_worked_problem_is_valid():
    assert _scene(scene_kind="worked_problem").scene_kind == "worked_problem"


def test_scene_kind_normalises_shape_not_vocabulary():
    assert _scene(scene_kind="Worked-Problem").scene_kind == "worked_problem"


@pytest.mark.parametrize("bad", ["derivation", "quiz", "proof", "example"])
def test_unknown_scene_kind_rejected(bad):
    """Closed taxonomy — a bogus kind must surface, not be silently swallowed."""
    with pytest.raises((ValidationError, S.ScriptValidationError)):
        _scene(scene_kind=bad)


def test_pre_existing_scene_without_scene_kind_still_parses():
    assert _scene().scene_kind == "explanation"


def test_scene_kind_is_orthogonal_to_narrative_role():
    s = _scene(scene_kind="worked_problem", narrative_role="development")
    assert s.scene_kind == "worked_problem"
    assert s.narrative_role == "development"


# --- prompt composition ----------------------------------------------------- #

def test_explanation_scene_gets_no_staging_noise():
    p = _prompt()
    assert "SCENE KIND: WORKED PROBLEM" not in p


def test_worked_problem_scene_gets_the_staging_block():
    p = _prompt(scene_kind="worked_problem")
    assert "SCENE KIND: WORKED PROBLEM" in p


def test_all_six_stages_present_and_ordered():
    p = _prompt(scene_kind="worked_problem")
    stages = ["1. GIVEN", "2. ASKED", "3. CHOOSE", "4. SUBSTITUTE", "5. SOLVE", "6. ANSWER"]
    positions = [p.index(s) for s in stages]
    assert positions == sorted(positions), "stages must appear in order"


def test_substitution_is_the_named_key_move():
    """Numbers must TRAVEL from the diagram into the equation, not fade in."""
    p = _prompt(scene_kind="worked_problem")
    assert "THE KEY MOVE" in p
    assert "TRAVEL FROM THE DIAGRAM INTO THE EQUATION" in p
    assert "TransformFromCopy(label_on_diagram, equation)" in p
    assert "Never simply fade in a finished equation" in p


def test_choice_of_method_is_made_visible():
    """Choosing the relation is the actual skill being taught."""
    p = _prompt(scene_kind="worked_problem")
    assert "make the CHOICE OF METHOD visible" in p
    assert "set_opacity(0.3)" in p


def test_answer_returns_to_the_diagram_with_units():
    p = _prompt(scene_kind="worked_problem")
    assert "send the result BACK to the diagram" in p
    assert "WITH ITS UNITS" in p


def test_diagram_must_stay_on_screen():
    p = _prompt(scene_kind="worked_problem")
    assert "diagram STAYS ON SCREEN throughout" in p
    assert "Never cut away to a full-screen wall" in p


def test_equation_is_explicitly_exempt_from_the_text_slide_rule():
    """Without this the anti-slop rules would fight the scene's own purpose."""
    p = _prompt(scene_kind="worked_problem")
    assert "An equation on screen is REQUIRED here" in p
    assert "does NOT count as a \"text slide\"" in p
    # ...but the rest of the anti-slop rules must still stand
    assert "no headings, no bullet lists, no title cards" in p


def test_real_numbers_and_units_are_demanded():
    p = _prompt(scene_kind="worked_problem")
    assert "real, sensible numbers and real units" in p
    assert "dimensionally inconsistent" in p


def test_staging_is_domain_neutral():
    """Must not be trig-specific — the same skeleton serves every quantitative domain."""
    p = _prompt(scene_kind="worked_problem", primary_domain_tag="electricity")
    assert "SCENE KIND: WORKED PROBLEM" in p
    assert "1. GIVEN" in p


# --- repair preserves the staging ------------------------------------------- #

def test_repair_preserves_worked_problem_staging():
    fix = re.sub(r"\s+", " ", mg._build_fix_prompt(
        "code", "err", "X", scene_intent=_sb(scene_kind="worked_problem")))
    assert "Scene kind: WORKED PROBLEM" in fix
    assert "KEEP the diagram on screen" in fix
    assert "FAILED repair" in fix


def test_repair_of_explanation_scene_has_no_staging_block():
    fix = mg._build_fix_prompt("code", "err", "X", scene_intent=_sb())
    assert "Scene kind: WORKED PROBLEM" not in fix


# --- storyboard plans it (rather than generation improvising it) ------------ #

def test_storyboard_prompt_requests_and_explains_scene_kind():
    from storyboard import _build_prompt
    p = _build_prompt("topic", [{"chapter": "c", "text": "t", "objective": "o",
                                 "explanation": "e"}], 60, "style")
    assert "scene_kind" in p
    assert "explanation | worked_problem" in p
    assert "SCENE KIND" in p


def test_storyboard_bounds_worked_problem_count():
    from storyboard import _build_prompt
    p = _build_prompt("topic", [{"chapter": "c", "text": "t", "objective": "o",
                                 "explanation": "e"}], 60, "style")
    assert "AT MOST 1-2 worked_problem scenes" in p
    assert "all worked examples stops teaching" in p


def test_storyboard_asks_for_calculation_shaped_beats():
    from storyboard import _build_prompt
    p = _build_prompt("topic", [{"chapter": "c", "text": "t", "objective": "o",
                                 "explanation": "e"}], 60, "style")
    assert "follow the stages of the calculation" in p


# --- B / C / E : trig rule additions ---------------------------------------- #

def _geom():
    return _prompt(primary_domain_tag="geometry")


def test_cosine_rule_has_its_own_technique():
    """REGRESSION: 'Sine/cosine rules' shared one bullet whose technique was
    purely a sine-rule idea, leaving the cosine rule with no visual at all."""
    p = _geom()
    assert "Cosine rule is about the INCLUDED angle" in p
    assert "GENERALISED PYTHAGORAS" in p
    assert "shrink to nothing" in p


def test_rule_selection_is_visible_not_announced():
    p = _geom()
    assert "CHOOSING WHICH RULE is the actual exam skill" in p
    assert "select the rule on screen before its name appears" in p


def test_bearings_convention_is_specified():
    p = _geom()
    assert "CLOCKWISE from north" in p
    assert "three figures (065°, not 65°)" in p
    assert "convention error" in p


def test_real_world_modelling_uses_layering():
    p = _geom()
    assert "show the ABSTRACTION happening" in p
    assert "set_z_index" in p


def test_sine_rule_pairing_still_intact():
    """The one thing that was already good must survive the split."""
    p = _geom()
    assert "Sine rule" in p
    assert "side-angle PAIR sharing a" in p
