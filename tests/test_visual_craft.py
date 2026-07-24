"""Craft-level animation direction: hierarchy, rhythm, and inter-scene joins.

Six additions that target the "technically correct but visually plain" failure
mode — where every stroke is the same weight, everything in a beat moves at the
same speed, new elements land on top of old ones, and consecutive scenes have no
visual join:

  1. line-weight hierarchy      (stroke_width as a foreground/scaffolding signal)
  2. motion hierarchy           (a lead element, supporting cast behind it)
  4. reframing over overlaying  (the frame reorganises to make room)
  6. match-cut between scenes   (open on the previous ending frame, then move)
  9. intentional holds          (a key-moment pause is direction, not dead air)
 11. value hierarchy            (lightness before a new hue)
"""

import re

import manim_generator as mg


def _prompt(previous_context=None, continuity_mode="varied"):
    entry = dict(index=2, learning_goal="g", key_concept="k", visual_metaphor="m",
                 composition="c", primary_objects=["a"], primary_motion="p",
                 color_role="r", transition_from_prev="t", anti_repetition_notes="n",
                 continuity_mode=continuity_mode)
    return re.sub(r"\s+", " ", mg._build_generation_prompt(
        text="n", animation="a", previous_context=previous_context,
        audio_duration=9.0, chapter="c", objective="o", explanation="e",
        storyboard_entry=entry, global_style="s", ledger_summary=""))


_PREV = {
    "text": "prior narration",
    "metaphor": "a parabola on axes",
    "ending_state": "the parabola remains, shifted left at half size",
    "carry_forward": "the parabola",
}


# --- 1. line-weight hierarchy ---------------------------------------------- #

def test_line_weight_hierarchy_present():
    p = _prompt()
    assert "LINE WEIGHT CARRIES HIERARCHY" in p
    assert "stroke_width" in p


def test_line_weight_gives_concrete_values_not_vague_advice():
    """A small model needs numbers, not 'use appropriate weights'."""
    p = _prompt()
    assert "5-7" in p          # subject
    assert "1.5-2.5" in p      # scaffolding
    assert "no foreground" in p


# --- 2. motion hierarchy ---------------------------------------------------- #

def test_motion_hierarchy_present():
    p = _prompt()
    assert "MOTION HIERARCHY" in p
    assert "LEAD and a SUPPORTING cast" in p


def test_motion_hierarchy_gives_a_concrete_mechanism():
    p = _prompt()
    assert "DIFFERENT run_time values in the same self.play" in p
    assert "One thing leads" in p


def test_settle_rather_than_stop_dead():
    p = _prompt()
    assert "LAND, DON'T STOP DEAD" in p
    assert "overshoot" in p
    # must be scoped to the key arrival, not applied to everything
    assert "not on everything" in p


def test_lag_ratio_scales_with_item_count():
    p = _prompt()
    assert "Scale lag_ratio to the COUNT" in p
    assert "0.25-0.35" in p and "0.1-0.15" in p


# --- 4. reframing over overlaying ------------------------------------------ #

def test_reframe_dont_pile_up_present():
    p = _prompt()
    assert "REFRAME, DON'T PILE UP" in p
    assert "shift/scale the existing elements to make room" in p


def test_reframing_is_preferred_over_spacing_things_apart():
    """This must supersede the older 'just put things in different regions' habit."""
    p = _prompt()
    assert "preferred way to avoid overlap" in p


# --- 6. match-cut ----------------------------------------------------------- #

def test_match_cut_guidance_only_when_there_is_a_previous_scene():
    first_scene = _prompt(previous_context=None)
    assert "MATCH-CUT THE JOIN" not in first_scene
    later_scene = _prompt(previous_context=_PREV)
    assert "MATCH-CUT THE JOIN" in later_scene


def test_match_cut_names_position_scale_and_colour():
    p = _prompt(previous_context=_PREV)
    assert "POSITION" in p and "SCALE" in p and "COLOUR" in p


def test_match_cut_gives_a_concrete_mechanism():
    p = _prompt(previous_context=_PREV)
    assert ".move_to/.scale to match the described ending" in p
    # and explicitly rules out the failure mode it replaces
    assert "Do NOT fade the old element out and fade a fresh one in" in p


def test_ending_frame_and_carry_forward_reach_the_prompt():
    p = _prompt(previous_context=_PREV)
    assert "the parabola remains, shifted left at half size" in p
    assert "Element intended to carry forward: the parabola" in p


def test_carry_forward_line_omitted_when_absent():
    p = _prompt(previous_context={"text": "t", "metaphor": "m", "ending_state": "e"})
    assert "Element intended to carry forward" not in p


def test_deliberate_break_is_still_allowed():
    """Match-cut must not force an awkward join onto a genuinely new idea."""
    p = _prompt(previous_context=_PREV)
    assert "genuinely starts a NEW visual idea, a clean break is correct" in p


# --- 9. intentional holds --------------------------------------------------- #

def test_key_moment_hold_is_permitted():
    p = _prompt()
    assert "HOLD THE KEY MOMENT" in p
    assert "self.wait(0.6-1.0)" in p


def test_hold_is_explicitly_distinguished_from_a_frozen_tail():
    p = _prompt()
    assert "a HOLD follows a meaningful reveal" in p
    assert "a FROZEN TAIL is time left over" in p


def test_frozen_tail_rule_still_intact():
    """Softening the wait rule must not reopen the frozen-tail regression."""
    p = _prompt()
    assert "FINAL self.wait() (after the last animation) must be <= 0.5s" in p
    assert "Do NOT pad the END with a long self.wait()" in p


def test_only_one_hold_per_scene():
    p = _prompt()
    assert "usually has exactly ONE such hold" in p


# --- 11. value hierarchy ---------------------------------------------------- #

def test_value_before_hue_present():
    p = _prompt()
    assert "REACH FOR VALUE (lightness) BEFORE REACHING FOR ANOTHER HUE" in p


def test_value_scale_direction_is_stated():
    """_A lightest -> _E darkest; the model must not guess the direction."""
    p = _prompt()
    assert "_A is lightest through _E darkest" in p
    assert "BLUE_A > BLUE_B > BLUE_C > BLUE_D > BLUE_E" in p


def test_value_hierarchy_composes_with_layering_and_focus():
    p = _prompt()
    assert "nearer = larger + brighter" in p
    assert "dim-the-rest" in p


def test_new_hue_must_mean_a_different_kind_of_thing():
    p = _prompt()
    assert "genuinely different KIND of thing" in p


# --- ending_state plumbing (the bug match-cut depends on) ------------------- #

def test_next_previous_context_uses_the_real_ending_state():
    """REGRESSION: ending_state fell through to primary_motion, so the next
    scene was told to match a hand-off frame that was never described."""
    import video_generator as vg

    entry = {"visual_metaphor": "a parabola", "primary_motion": "the curve sweeps",
             "composition": "centered", "ending_state": "parabola at half size, left",
             "continuity_notes": "the parabola"}
    ctx = vg._next_previous_context({"text": "narr"}, entry)
    assert ctx["ending_state"] == "parabola at half size, left"
    assert ctx["carry_forward"] == "the parabola"


def test_next_previous_context_falls_back_for_older_scenes():
    """Scenes stored before ending_state existed must still produce something."""
    import video_generator as vg

    entry = {"visual_metaphor": "m", "primary_motion": "the curve sweeps",
             "composition": "centered"}
    ctx = vg._next_previous_context({"text": "narr"}, entry)
    assert ctx["ending_state"] == "the curve sweeps"


def test_storyboard_asks_for_a_concrete_spatial_ending_state():
    from storyboard import _build_prompt
    p = _build_prompt("topic", [{"chapter": "c", "text": "t", "objective": "o",
                                 "explanation": "e"}], 60, "style")
    assert "clean FINAL FRAME" in p
    assert "WHERE and at" in p
    assert "next scene is told to open by matching this" in p


# --- trigonometry: the four core visuals (all render-verified) -------------- #

def _geom():
    entry = dict(index=1, learning_goal="g", key_concept="k", visual_metaphor="m",
                 composition="c", primary_objects=["a"], primary_motion="p",
                 color_role="r", transition_from_prev="t", anti_repetition_notes="n",
                 primary_domain_tag="geometry")
    return re.sub(r"\s+", " ", mg._build_generation_prompt(
        text="n", animation="a", previous_context=None, audio_duration=9.0,
        chapter="c", objective="o", explanation="e", storyboard_entry=entry,
        global_style="s", ledger_summary=""))


def test_trig_sweep_is_one_tracker_driving_everything():
    p = _geom()
    assert "THE SWEEP" in p
    assert "ONE ValueTracker holds the angle" in p
    assert "GENERATED by the sweep, not traced" in p


def test_trig_rotation_caution_is_explicitly_inverted():
    """'Rotate sparingly' is right in general and wrong for trig — the angle IS
    the variable. The override must be explicit or the general rule wins."""
    p = _geom()
    assert 'the general "use Rotate sparingly" caution does NOT apply here' in p
    assert "the angle IS the variable" in p


def test_trig_projection_meaning_is_stated():
    p = _geom()
    assert "vertical drop from the circle point to the axis IS sin" in p
    assert "sine is a projection, not a number from a table" in p


def test_scale_invariance_visual_present():
    p = _geom()
    assert "SCALE INVARIANCE" in p
    assert "ratio opp/hyp stays FIXED" in p


def test_solving_in_an_interval_demands_every_solution():
    p = _geom()
    assert "IN AN INTERVAL" in p
    assert "Dot EVERY intersection" in p
    assert "Never show only the principal value" in p


def test_pythagorean_identity_is_derived_not_asserted():
    p = _geom()
    assert "not a fact to memorise, it is Pythagoras" in p
    assert "hypotenuse 1" in p


def test_trig_render_cost_warning_is_specific():
    """Measured: always_redraw(Text) cost 3x the rest of the scene combined."""
    p = _geom()
    assert "RENDER COST" in p
    assert "3x MORE THAN THE ENTIRE REST OF THE SCENE" in p
    assert "ONE always_redraw returning a VGroup" in p


def test_reactive_text_cost_warning_is_in_the_shared_vocabulary_too():
    """The finding is cross-cutting, not trig-specific — every domain needs it."""
    p = _prompt()          # untagged / general scene
    assert "rebuilds a **Text** every frame costs roughly 3x" in p
    assert "40s against 3.6s" in p
