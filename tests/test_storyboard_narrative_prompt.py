"""Storyboard-prompt composition for narrative arc / continuity mode / colors."""

import storyboard as sb_mod


def _prompt():
    return sb_mod._build_prompt(
        "topic", [{"chapter": "c", "text": "t", "objective": "o", "explanation": "e"}],
        60, "style")


def test_prompt_requests_narrative_role_field():
    p = _prompt()
    assert "narrative_role" in p
    assert "hook | setup | development | misconception | resolution" in p


def test_prompt_requests_continuity_mode_field():
    p = _prompt()
    assert "continuity_mode" in p
    assert "cumulative | varied" in p


def test_prompt_requests_semantic_colors_field():
    p = _prompt()
    assert "semantic_colors" in p


def test_narrative_arc_guidance_explains_each_role():
    p = _prompt()
    for role in ("HOOK", "SETUP", "DEVELOPMENT", "MISCONCEPTION", "RESOLUTION", "RECAP"):
        assert role in p


def test_misconception_guidance_forbids_artificial_drama():
    p = _prompt()
    assert "never invent one artificially for drama" in p


def test_continuity_mode_guidance_explains_both_options():
    p = _prompt()
    assert '"cumulative"' in p
    assert '"varied"' in p
    assert "ONE coherent visual world" in p
    assert "Do not force cumulative onto every video" in p


def test_concrete_before_abstract_guidance_present():
    p = _prompt()
    assert "concrete example or physical situation" in p
    assert "BEFORE the general abstraction" in p


def test_semantic_color_guidance_forbids_forcing_colors():
    p = _prompt()
    assert "Do not assign a color where it would not help" in p
    assert "empty list is\n  correct" in p or "empty list is correct" in p.replace("\n  ", " ")


def test_diversity_rules_note_cumulative_exception():
    p = _prompt()
    assert "a video-wide choice of \"cumulative\" replaces these" in p
