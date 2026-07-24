"""Prompt-level animation-quality guarantees (3Blue1Brown-inspired direction).

These are contract tests on the INSTRUCTIONS sent to the model. They guard three
regressions that previously produced static, repetitive output:

* an over-narrow animation whitelist (6 verbs) that made every scene
  fade-in/fade-out;
* a false claim that Manim colour variants are unavailable;
* a repair prompt that let the model "fix" errors by deleting the animation.

Every construct asserted below was render-verified in this environment
(tests/../ smoke render, Manim 0.19.1, exit=0) before being permitted.
"""

import re

import pytest

import manim_generator as mg

# Verified-safe expressive vocabulary the prompt must offer.
EXPRESSIVE = [
    "LaggedStart", "AnimationGroup", "TransformFromCopy", "Indicate",
    "Circumscribe", "Flash", "ShowPassingFlash", "GrowArrow",
    "MoveAlongPath", "Rotate", "Succession",
]

# APIs that must stay genuinely, unconditionally out of scope (unlike
# ValueTracker/always_redraw, which moved to a narrow CONTROLLED CONTINUOUS
# MOTION allowlist — see test_controlled_continuous_motion.py).
OUT_OF_SCOPE = ["callbacks", "physics simulation"]


@pytest.fixture
def gen_prompt():
    """Whitespace-normalized (guidance text is hard-wrapped in the source)."""
    return re.sub(r"\s+", " ", mg._build_generation_prompt(
        text="narration", animation="anim", previous_context=None,
        audio_duration=9.0, chapter="C", objective="O", explanation="E",
        storyboard_entry=None, global_style="style", ledger_summary="",
    ))


@pytest.fixture
def fix_prompt():
    return mg._build_fix_prompt("code", "error", "MyScene")


# --- vocabulary ------------------------------------------------------------ #

@pytest.mark.parametrize("verb", EXPRESSIVE)
def test_expressive_vocabulary_is_offered(gen_prompt, verb):
    assert verb in gen_prompt


def test_reliable_core_is_retained(gen_prompt):
    for verb in ("Write", "Create", "FadeIn", "FadeOut",
                 "Transform", "ReplacementTransform"):
        assert verb in gen_prompt


@pytest.mark.parametrize("api", OUT_OF_SCOPE)
def test_fragile_apis_are_excluded(gen_prompt, api):
    assert api in gen_prompt, "must be named so the model knows to avoid it"
    idx = gen_prompt.index("OUT OF SCOPE")
    assert gen_prompt.index(api) > idx or api in gen_prompt[idx:], \
        f"{api} must appear in the out-of-scope block"


def test_raw_updaters_still_excluded(gen_prompt):
    """always_redraw is allowed; a raw add_updater is not."""
    assert "add_updater/remove_updater directly" in gen_prompt
    assert "never a raw updater function" in gen_prompt


def test_external_assets_still_excluded(gen_prompt):
    assert "external assets/images" in gen_prompt


def test_old_narrow_whitelist_is_gone(gen_prompt):
    """REGRESSION: 'Use only basic animations: ...' capped every scene."""
    assert "Use only basic animations" not in gen_prompt


# --- colour ---------------------------------------------------------------- #

def test_colour_variants_are_permitted(gen_prompt):
    """REGRESSION: prompt falsely claimed variants 'may not be imported'."""
    assert "DO NOT use color variants" not in gen_prompt
    assert "ONLY use these basic colors" not in gen_prompt
    for variant in ("BLUE_D", "TEAL_C", "GOLD_A"):
        assert variant in gen_prompt


def test_colour_discipline_is_required(gen_prompt):
    low = gen_prompt.lower()
    assert "small, coherent palette" in low
    assert "rainbow" in low          # explicitly forbidden
    assert "stable" in low           # same concept keeps its colour


# --- continuous motion ----------------------------------------------------- #

def test_continuous_motion_direction_present(gen_prompt):
    assert "CONTINUOUS MOTION" in gen_prompt
    assert "run_time is usually 1.5-4s" in gen_prompt
    assert "OVERLAP related animations" in gen_prompt


def test_unmotivated_waits_discouraged(gen_prompt):
    assert "Do NOT insert self.wait() between beats" in gen_prompt


def test_pacing_is_balanced_not_frantic(gen_prompt):
    """Guard against over-correcting into constant meaningless movement."""
    assert "does NOT mean frantic" in gen_prompt
    assert "breathing room" in gen_prompt


def test_motion_must_be_explanatory(gen_prompt):
    assert "MOTION MUST DO EXPLANATORY WORK" in gen_prompt
    assert "If you cannot say what a movement teaches, delete it." in gen_prompt


# --- continuity over delete-and-rebuild ------------------------------------ #

def test_transform_preferred_over_delete_and_rebuild(gen_prompt):
    """REGRESSION: 'ALWAYS FadeOut before showing new ones' forced hard cuts."""
    assert "ALWAYS use FadeOut() to remove old elements" not in gen_prompt
    assert "ALWAYS clean old elements" not in gen_prompt
    assert "Prefer TRANSFORMING an existing object" in gen_prompt


def test_frozen_tail_protection_preserved(gen_prompt):
    """Existing anti-frozen-tail safeguard must survive these edits."""
    assert "FINAL self.wait() (after the last animation) must be <= 0.5s" in gen_prompt
    assert "FORBIDDEN" in gen_prompt


def test_safe_margins_and_text_rules_preserved(gen_prompt):
    assert "safe margins" in gen_prompt
    assert "NEVER put the narration on screen" in gen_prompt
    assert "MathTex" in gen_prompt          # LaTeX still banned


# --- anti-slop ------------------------------------------------------------- #

def test_generic_composition_is_refused(gen_prompt):
    assert "AVOID REPETITIVE" in gen_prompt
    low = gen_prompt.lower()
    assert "bullet text" in low
    assert "title slides" in low


def test_no_fixed_template_is_imposed(gen_prompt):
    """The code example must be labelled as shape-only, not a template."""
    assert "do NOT copy this" in gen_prompt


# --- repair prompt --------------------------------------------------------- #

def test_repair_preserves_visual_intent(fix_prompt):
    assert "PRESERVE THE LESSON" in fix_prompt
    assert "SMALLEST technical fix" in fix_prompt
    assert "FAILED repair" in fix_prompt


def test_repair_forbids_flattening_to_static(fix_prompt):
    low = fix_prompt.lower()
    assert "static text slide" in low
    assert "do not \"fix\" the error by deleting the animation" in low


def test_repair_allows_expanded_vocabulary(fix_prompt):
    for verb in ("TransformFromCopy", "LaggedStart", "Indicate", "GrowArrow"):
        assert verb in fix_prompt
    assert "DO NOT use color variants" not in fix_prompt


def test_repair_still_excludes_fragile_apis(fix_prompt):
    assert "ValueTracker" in fix_prompt
    assert "always_redraw" in fix_prompt


def test_repair_keeps_technical_safeguards(fix_prompt):
    assert "MathTex" in fix_prompt
    assert "self.camera.frame" in fix_prompt


# --- system instructions --------------------------------------------------- #

def test_system_prompts_carry_the_style():
    assert "3Blue1Brown" in mg._GEN_SYSTEM
    assert "transform into one" in mg._GEN_SYSTEM.replace("\n", " ")
    assert "PRESERVING" in mg._FIX_SYSTEM
    assert "static text slide" in mg._FIX_SYSTEM
