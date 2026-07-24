"""Video-level narrative arc, continuity mode, and semantic color contract.

Covers: schema validation for narrative_role/continuity_mode/semantic_colors,
canonicalization of the video-wide continuity_mode and color contract from
per-scene fields, diversity-check behavior in "cumulative" mode (intentional
carry-forward must not be flagged as repetition), and prompt composition for
both initial generation and repair.
"""

import re

import pytest
from pydantic import ValidationError

import manim_generator as mg
import schemas as S
import storyboard as sb_mod


def _scene_kwargs(**over):
    base = dict(index=1, learning_goal="g", key_concept="k", visual_metaphor="m",
                composition="c", primary_objects=["a"], primary_motion="p",
                color_role="r", transition_from_prev="t", anti_repetition_notes="n")
    base.update(over)
    return base


def _scene(**over):
    return S.parse_storyboard([_scene_kwargs(**over)]).scenes[0]


def _storyboard(scene_kwargs_list):
    return S.parse_storyboard([_scene_kwargs(index=i + 1, **kw)
                               for i, kw in enumerate(scene_kwargs_list)])


def _prompt(entry):
    return re.sub(r"\s+", " ", mg._build_generation_prompt(
        text="n", animation="a", previous_context=None, audio_duration=9.0,
        chapter="c", objective="o", explanation="e", storyboard_entry=entry,
        global_style="s", ledger_summary="prior scene stuff"))


# --------------------------------------------------------------------------- #
# Schema: narrative_role
# --------------------------------------------------------------------------- #

def test_narrative_role_defaults_to_standalone():
    assert _scene().narrative_role == "standalone"


@pytest.mark.parametrize("role", S.NARRATIVE_ROLES)
def test_every_narrative_role_is_valid(role):
    assert _scene(narrative_role=role).narrative_role == role


def test_unknown_narrative_role_rejected():
    with pytest.raises((ValidationError, S.ScriptValidationError)):
        _scene(narrative_role="climax")


def test_narrative_role_normalizes_case():
    assert _scene(narrative_role="MISCONCEPTION").narrative_role == "misconception"
    assert _scene(narrative_role="Hook").narrative_role == "hook"


# --------------------------------------------------------------------------- #
# Schema: continuity_mode
# --------------------------------------------------------------------------- #

def test_continuity_mode_defaults_to_varied():
    assert _scene().continuity_mode == "varied"


def test_cumulative_mode_valid():
    assert _scene(continuity_mode="cumulative").continuity_mode == "cumulative"


def test_unknown_continuity_mode_rejected():
    with pytest.raises((ValidationError, S.ScriptValidationError)):
        _scene(continuity_mode="mixed")


# --------------------------------------------------------------------------- #
# Schema: semantic_colors
# --------------------------------------------------------------------------- #

def test_semantic_colors_default_empty():
    assert _scene().semantic_colors == []


def test_semantic_color_name_and_hex_both_valid():
    s = _scene(semantic_colors=[{"concept": "force", "color": "gold_a"},
                                {"concept": "velocity", "color": "#00ff88"}])
    assert s.semantic_colors[0].concept == "force"
    assert s.semantic_colors[0].color == "GOLD_A"
    assert s.semantic_colors[1].color == "#00FF88"


def test_invalid_color_value_rejected():
    with pytest.raises((ValidationError, S.ScriptValidationError)):
        _scene(semantic_colors=[{"concept": "force", "color": "not a color!"}])


def test_semantic_colors_capped():
    many = [{"concept": f"c{i}", "color": "BLUE_D"} for i in range(10)]
    s = _scene(semantic_colors=many)
    assert len(s.semantic_colors) == S.MAX_SEMANTIC_COLORS


# --------------------------------------------------------------------------- #
# Backward compatibility
# --------------------------------------------------------------------------- #

def test_pre_existing_stored_scene_still_parses():
    """A scene stored before this phase existed must remain valid."""
    sb = S.parse_storyboard([_scene_kwargs(index=1), _scene_kwargs(index=2)])
    for scene in sb.scenes:
        assert scene.narrative_role == "standalone"
        assert scene.continuity_mode == "varied"
        assert scene.semantic_colors == []


# --------------------------------------------------------------------------- #
# Canonicalization (video-level derivation from per-scene fields)
# --------------------------------------------------------------------------- #

def test_canonical_continuity_mode_majority_vote():
    board = _storyboard([
        dict(continuity_mode="cumulative"), dict(continuity_mode="cumulative"),
        dict(continuity_mode="varied"),
    ])
    assert sb_mod.canonical_continuity_mode(board) == "cumulative"


def test_canonical_continuity_mode_ties_favor_varied():
    board = _storyboard([dict(continuity_mode="cumulative"), dict(continuity_mode="varied")])
    assert sb_mod.canonical_continuity_mode(board) == "varied"


def test_canonical_semantic_colors_first_wins_dedup():
    board = _storyboard([
        dict(semantic_colors=[{"concept": "force", "color": "GOLD_A"}]),
        dict(semantic_colors=[{"concept": "force", "color": "RED_C"},
                              {"concept": "velocity", "color": "TEAL_D"}]),
    ])
    colors = {c["concept"]: c["color"] for c in sb_mod.canonical_semantic_colors(board)}
    assert colors["force"] == "GOLD_A"          # first occurrence wins
    assert colors["velocity"] == "TEAL_D"


def test_narrative_arc_notes_flags_hook_without_resolution():
    board = _storyboard([dict(narrative_role="hook"), dict(narrative_role="development")])
    notes = sb_mod.narrative_arc_notes(board)
    assert any("resolution" in n for n in notes)


def test_narrative_arc_notes_silent_when_arc_is_complete():
    board = _storyboard([dict(narrative_role="hook"), dict(narrative_role="setup"),
                         dict(narrative_role="resolution")])
    assert sb_mod.narrative_arc_notes(board) == []


def test_narrative_arc_notes_silent_for_standalone_video():
    board = _storyboard([dict(), dict(), dict()])
    assert sb_mod.narrative_arc_notes(board) == []


# --------------------------------------------------------------------------- #
# Diversity checking: cumulative mode must not flag intentional continuity
# --------------------------------------------------------------------------- #

def test_varied_mode_still_flags_adjacent_repeats():
    board = _storyboard([
        dict(visual_metaphor="a bouncing ball", composition="split screen"),
        dict(visual_metaphor="a bouncing ball", composition="split screen"),
        dict(visual_metaphor="a rotating gear", composition="centered"),
    ])
    violations = sb_mod.check_diversity(board, target_duration=60)
    assert any("repeat the same visual metaphor" in v for v in violations)


def test_cumulative_mode_does_not_flag_carried_forward_visuals():
    """The exact scenario the task calls out: intentional continuity != repetition."""
    board = _storyboard([
        dict(continuity_mode="cumulative", visual_metaphor="a single evolving graph",
            composition="centered axes"),
        dict(continuity_mode="cumulative", visual_metaphor="a single evolving graph",
            composition="centered axes"),
        dict(continuity_mode="cumulative", visual_metaphor="a single evolving graph",
            composition="centered axes"),
    ])
    assert sb_mod.check_diversity(board, target_duration=60) == []


def test_cumulative_mode_relaxes_distinct_approach_requirement():
    board = _storyboard([dict(continuity_mode="cumulative")] * 5)
    # Would need 5 distinct approaches in varied mode for this duration; must
    # not be held to that bar when the video is intentionally one visual world.
    assert sb_mod.required_distinct_approaches(60, 5) > 1
    assert sb_mod.check_diversity(board, target_duration=60) == []


# --------------------------------------------------------------------------- #
# Prompt composition: initial generation
# --------------------------------------------------------------------------- #

def test_hook_scene_gets_narrative_guidance():
    p = _prompt(_scene(narrative_role="hook").model_dump())
    assert "OPENING HOOK" in p
    assert "never a generic" in p


def test_resolution_scene_told_to_answer_the_hook():
    p = _prompt(_scene(narrative_role="resolution").model_dump())
    assert "RETURNS TO THE VIDEO'S OPENING HOOK" in p
    assert "SHOWN via the visual" in p


def test_misconception_scene_told_to_break_it_visibly():
    p = _prompt(_scene(narrative_role="misconception").model_dump())
    assert "PLAUSIBLE WRONG mental model" in p
    assert "VISIBLE on screen" in p


def test_standalone_scene_gets_no_narrative_role_noise():
    p = _prompt(_scene().model_dump())
    assert "NARRATIVE ROLE" not in p


_PREV_CTX = {"text": "prior narration", "metaphor": "a rotating wheel", "ending_state": "settled"}


def test_cumulative_mode_reframes_continuity_and_anti_repetition():
    entry = _scene(continuity_mode="cumulative").model_dump()
    p = re.sub(r"\s+", " ", mg._build_generation_prompt(
        text="n", animation="a", previous_context=_PREV_CTX, audio_duration=9.0,
        chapter="c", objective="o", explanation="e", storyboard_entry=entry,
        global_style="s", ledger_summary="prior scene stuff"))
    assert "CONTINUITY MODE for this video: cumulative" in p
    assert "keep the SAME visual world alive" in p
    assert "N/A in cumulative mode" in p
    # the ledger's "do not repeat" instruction must not fight the continuity rule
    assert "ALREADY-USED VISUAL CHOICES" not in p


def test_varied_mode_keeps_existing_anti_repetition_behavior():
    entry = _scene(continuity_mode="varied").model_dump()
    p = re.sub(r"\s+", " ", mg._build_generation_prompt(
        text="n", animation="a", previous_context=_PREV_CTX, audio_duration=9.0,
        chapter="c", objective="o", explanation="e", storyboard_entry=entry,
        global_style="s", ledger_summary="prior scene stuff"))
    assert "MUST differ from earlier scenes" in p
    assert "ALREADY-USED VISUAL CHOICES" in p
    assert "CONTINUITY (make this feel like ONE film" in p


def test_semantic_color_contract_reaches_the_prompt():
    entry = _scene(semantic_colors=[{"concept": "force", "color": "GOLD_A"},
                                    {"concept": "velocity", "color": "TEAL_D"}]).model_dump()
    p = _prompt(entry)
    assert "SEMANTIC COLOR CONTRACT" in p
    assert "force: GOLD_A" in p
    assert "velocity: TEAL_D" in p


def test_no_semantic_colors_means_no_section():
    p = _prompt(_scene().model_dump())
    assert "SEMANTIC COLOR CONTRACT" not in p


def test_dim_the_rest_focus_technique_present():
    p = _prompt(None)
    assert "DIM-THE-REST FOCUS" in p
    assert "set_opacity(0.3)" in p


def test_curved_arc_motion_guidance_present_and_not_decorative():
    p = _prompt(None)
    assert "ArcBetweenPoints" in p
    assert "never as decoration" in p or "never a decoration" in p.lower()


# --------------------------------------------------------------------------- #
# Prompt composition: repair preserves narrative role / continuity / colors
# --------------------------------------------------------------------------- #

def test_repair_preserves_narrative_role():
    intent = _scene(narrative_role="misconception").model_dump()
    fix = mg._build_fix_prompt("code", "err", "X", scene_intent=intent)
    assert "misconception" in fix
    assert "PLAUSIBLE WRONG mental model" in fix


def test_repair_preserves_cumulative_continuity_instruction():
    intent = _scene(continuity_mode="cumulative").model_dump()
    fix = mg._build_fix_prompt("code", "err", "X", scene_intent=intent)
    assert "cumulative" in fix
    assert "do not reset to a fresh unrelated visual" in fix


def test_repair_preserves_semantic_colors():
    intent = _scene(semantic_colors=[{"concept": "force", "color": "GOLD_A"}]).model_dump()
    fix = mg._build_fix_prompt("code", "err", "X", scene_intent=intent)
    assert "force=GOLD_A" in fix


def test_repair_allows_preserving_controlled_tracker_pattern():
    fix = mg._build_fix_prompt("code", "err", "X")
    assert "PRESERVE it" in fix
    assert "do not flatten it to static geometry" in fix
