"""Controlled ValueTracker/always_redraw continuous motion.

Previously ValueTracker/always_redraw were entirely OUT OF SCOPE. This phase
introduces a narrow, render-verified allowlist of five patterns (point+readout,
tangent/secant sweep, parameter-driven curve, rotating-mechanism+coupled
readout, wave-phase advance) while keeping everything else that made reactive
Manim risky — raw add_updater, callbacks, multiple trackers, arbitrary lambdas
— explicitly banned.

All five patterns were render-verified in this environment (Manim CE 0.19.1,
cairo, no LaTeX) before being written into the prompt:
  - point + coupled readout/guide  (rc=0, ~19s)
  - changing tangent/secant         (rc=0, ~8s)
  - parameter reshaping a curve     (rc=0, ~30s -- notably slower, see below)
  - rotating mechanism + readout    (rc=0, ~11s)
  - wave phase advancing            (rc=0, ~13s)

This file only checks PROMPT COMPOSITION (fast, no Manim invocation) — the
actual renders were done as one-off smoke tests, not part of the pytest suite,
consistent with this project's convention of no live-render tests in CI.
"""

import re

import manim_generator as mg


def _prompt():
    return re.sub(r"\s+", " ", mg._build_generation_prompt(
        text="n", animation="a", previous_context=None, audio_duration=9.0,
        chapter="c", objective="o", explanation="e", storyboard_entry=None,
        global_style="s", ledger_summary=""))


def _fix_prompt(error="NameError"):
    return re.sub(r"\s+", " ", mg._build_fix_prompt("code", error, "X"))


# --------------------------------------------------------------------------- #
# The allowlist exists and is bounded
# --------------------------------------------------------------------------- #

def test_controlled_continuous_motion_section_present():
    p = _prompt()
    assert "CONTROLLED CONTINUOUS MOTION" in p
    assert "NOT a general license for reactive code" in p


def test_at_most_one_tracker_per_scene_is_stated():
    p = _prompt()
    assert "AT MOST ONE ValueTracker per scene" in p
    assert "more than one" in p and "ValueTracker per scene" in p  # in the ban list too


def test_prefer_discrete_when_it_teaches_the_same_thing():
    """The capability must not become the default choice over reliable discrete moves."""
    p = _prompt()
    assert "prefer it" in p
    assert "should not become the default choice" in p


# --------------------------------------------------------------------------- #
# Each of the five verified patterns is named
# --------------------------------------------------------------------------- #

def test_point_with_coupled_readout_pattern():
    p = _prompt()
    assert "Point + coupled readout/guide" in p
    assert "ValueTracker(x0)" in p
    assert "always_redraw(lambda: Dot(ax.i2gp" in p


def test_changing_tangent_secant_pattern():
    p = _prompt()
    assert "Changing tangent/secant" in p
    # the guidance must acknowledge the discrete alternative can be clearer
    assert "Transform between two" in p


def test_parameter_reshaping_curve_pattern():
    p = _prompt()
    assert "A parameter reshaping a curve/vector/construction" in p
    assert "render cost is meaningfully higher" in p


def test_rotating_mechanism_coupled_effect_pattern():
    p = _prompt()
    assert "Rotating mechanism with a coupled effect" in p
    assert "rotating coil and induced current" in p


def test_wave_phase_pattern():
    p = _prompt()
    assert "Wave phase" in p
    assert "phase.get_value()" in p


# --------------------------------------------------------------------------- #
# What remains genuinely banned
# --------------------------------------------------------------------------- #

def test_raw_updaters_banned():
    p = _prompt()
    assert "add_updater/remove_updater directly" in p
    assert "never a raw updater function" in p


def test_callbacks_and_event_handlers_banned():
    p = _prompt()
    assert "callbacks, event handlers" in p


def test_lambdas_bound_to_anything_other_than_one_tracker_banned():
    p = _prompt()
    assert "lambdas bound to" in p
    assert "anything other than a single ValueTracker's .get_value()" in p


def test_multiple_trackers_on_one_mobject_family_banned():
    p = _prompt()
    assert "Never wire two ValueTrackers to the same mobject family" in p


def test_external_assets_and_physics_simulation_still_banned():
    p = _prompt()
    assert "external assets/images" in p
    assert "physics simulation" in p


# --------------------------------------------------------------------------- #
# Repair prompt: preserve controlled patterns, still fix genuinely broken ones
# --------------------------------------------------------------------------- #

def test_repair_prompt_preserves_a_correct_controlled_pattern():
    p = _fix_prompt("NameError: name 'foo' is not defined")
    assert "PRESERVE it" in p
    assert "do not flatten it to static geometry" in p


def test_repair_prompt_still_allows_removing_genuinely_broken_reactive_code():
    p = _fix_prompt("AttributeError: 'ValueTracker' object has no attribute 'foo'")
    assert "the tracker/redraw itself is the broken part" in p
    assert "raw add_updater" in p


def test_repair_prompt_never_reintroduces_hard_bans():
    p = _fix_prompt()
    assert "DO NOT introduce raw add_updater, callbacks, event handlers" in p
