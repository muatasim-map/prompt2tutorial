"""Scene-craft prompt guidance: continuity, framing, entrances/exits, label
timing, and an emphasis budget. Flexible defaults (not rigid rules), so tests
assert presence + framing-as-judgement, not exact wording."""

import manim_generator as mg


def _prompt(prev=None):
    return mg._build_generation_prompt(
        text="n", animation="a", previous_context=prev, audio_duration=9.0,
        chapter="c", objective="o", explanation="e", storyboard_entry=None,
        global_style="s", ledger_summary="")


def test_continuity_handoff_present_with_prior_scene():
    p = _prompt(prev={"text": "t", "metaphor": "m", "ending_state": "a vector on axes"})
    assert "make this feel like ONE film" in p
    assert "TRANSFORMING" in p
    # must remain flexible, not mandatory
    assert "with judgement" in p or "genuinely new idea" in p


def test_first_scene_has_no_forced_carryover():
    p = _prompt(prev=None)                      # first scene
    assert "FIRST scene of the video" in p


def test_framing_convention_present():
    p = _prompt()
    assert "FRAMING" in p and "to_edge(UP)" in p
    assert "same \"stage\"" in p or "same stage" in p


def test_entrance_exit_discipline():
    p = _prompt()
    assert "ENTRANCES & EXITS" in p
    assert "directional entrance" in p
    assert "hard pop-in" in p


def test_label_timing_rule():
    p = _prompt()
    assert "LABEL TIMING" in p
    assert "never a naked label" in p.lower()
    assert "orphaned label" in p.lower()


def test_emphasis_budget_capped():
    p = _prompt()
    assert "EMPHASIS BUDGET" in p
    assert "at most 1-2" in p
    assert "everything flashes" in p.lower()


def test_craft_is_flexible_not_rigid():
    p = _prompt()
    assert "apply with judgement" in p
    assert "not rigid rules" in p


def test_pacing_variety_present():
    p = _prompt()
    assert "VARY THE RHYTHM" in p
    assert "TIME BUDGET" in p          # variety must still respect total duration
    assert "quicker builds" in p and "slower, deliberate reveal" in p
