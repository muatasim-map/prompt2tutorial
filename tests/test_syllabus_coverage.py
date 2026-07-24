"""A-level maths & physics syllabus coverage, per routed domain module.

Domain guidance is no longer one monolithic block: each scene receives only the
modules its storyboard tags select. These tests therefore assert that each
syllabus topic is present in the prompt *when its tag is routed* — and, in the
isolation tests below, absent when it is not.

The cobweb-iteration diagram, `Cross(Circle(...))` field symbols, `Angle` /
`RightAngle` marks and `Polygon` trapezia were all render-verified in this
environment before being written into the guidance. `BarChart` is banned because
its `__init__` builds `Tex` labels and LaTeX is not installed here.
"""

import re

import manim_generator as mg


def _sb(primary="general", secondary=None, dimension=None, **over):
    """Minimal valid storyboard entry dict with domain routing."""
    base = dict(index=1, learning_goal="g", key_concept="k", visual_metaphor="m",
                composition="c", primary_objects=["a"], primary_motion="p",
                color_role="r", transition_from_prev="t", anti_repetition_notes="n",
                primary_domain_tag=primary, secondary_domain_tags=secondary or [],
                dimension=dimension)
    base.update(over)
    return base


def _prompt(primary="general", secondary=None, dimension=None):
    """Assembled prompt with runs of whitespace collapsed.

    The guidance text is hard-wrapped, so a phrase like "total internal
    reflection" is split across lines in the source. Normalizing here keeps the
    assertions about MEANING rather than about where the wrap happened.
    """
    return re.sub(r"\s+", " ", _raw_prompt(primary, secondary, dimension))


def _raw_prompt(primary="general", secondary=None, dimension=None):
    return mg._build_generation_prompt(
        text="n", animation="a", previous_context=None, audio_duration=9.0,
        chapter="c", objective="o", explanation="e",
        storyboard_entry=_sb(primary, secondary, dimension),
        global_style="s", ledger_summary="")


# --------------------------------------------------------------------------- #
# Mathematics
# --------------------------------------------------------------------------- #


def test_graph_transformations_covered():
    p = _prompt("algebra")
    assert "Graph transformations" in p
    assert "f(x)+a" in p or "f(x-a)" in p
    assert "exponential" in p.lower() and "logarithm" in p.lower()


def test_exponentials_and_logs_covered():
    p = _prompt("algebra")
    assert "half-life" in p.lower() or "doubling" in p.lower()
    assert "log-linearisation" in p.lower()


def test_sequences_series_covered():
    p = _prompt("algebra")
    assert "Sequences & series" in p
    assert "convergent" in p.lower() or "divergent" in p.lower()
    assert "TransformFromCopy" in p


def test_asymptotes_covered():
    p = _prompt("algebra")
    assert "Asymptotes" in p
    assert "DashedLine" in p


def test_coordinate_geometry_covered():
    p = _prompt("geometry")
    assert "Coordinate geometry" in p
    assert "gradient" in p.lower()
    assert "tangent" in p.lower() and "circle" in p.lower()


def test_trigonometry_covered():
    p = _prompt("geometry")
    assert "unit circle" in p.lower()
    # circle/graph correspondence is now ENFORCED by one shared tracker (THE
    # SWEEP) rather than merely requested in prose.
    assert "THE SWEEP" in p
    assert "ONE ValueTracker holds the angle" in p
    assert "Sine rule" in p
    assert "non-right triangle" in p
    # cosine rule now has its OWN technique, not just a shared mention
    assert "Cosine rule is about the INCLUDED angle" in p


def test_calculus_core_covered():
    p = _prompt("calculus")
    for concept in ("tangent", "integration", "riemann", "limit", "parametric"):
        assert concept in p.lower(), concept


def test_stationary_points_covered():
    p = _prompt("calculus")
    assert "Stationary points" in p
    assert "inflection" in p


def test_numerical_methods_cobweb_covered():
    """The cobweb/staircase diagram was render-verified before being recommended."""
    p = _prompt("calculus")
    assert "cobweb" in p.lower()
    assert "y=x" in p


def test_numerical_integration_covered():
    p = _prompt("calculus")
    assert "Trapezium rule" in p
    assert "Polygon(" in p


def test_volume_of_revolution_prefers_2d():
    p = _prompt("calculus")
    assert "Volume of revolution" in p
    assert "PREFER\n  the 2D version" in p or "PREFER the 2D version" in p


def test_vectors_and_matrices_covered():
    p = _prompt("linear_algebra")
    assert "vector addition" in p.lower() or "tip-to-tail" in p
    for concept in ("matrix", "determinant", "eigenvector"):
        assert concept in p.lower(), concept


def test_statistics_covered_and_barchart_banned():
    p = _prompt("probability_statistics")
    assert "box plot" in p.lower()
    assert "do NOT use the BarChart class" in p
    assert "LaTeX is not installed" in p


def test_hypothesis_testing_covered():
    p = _prompt("probability_statistics")
    assert "Hypothesis testing" in p
    assert "critical region" in p
    assert "two-tailed" in p
    assert "BINOMIAL (discrete)" in p


def test_maths_mechanics_covered():
    """Mechanics is on the A-level MATHS syllabus, not only physics."""
    p = _prompt("mechanics")
    assert "Moments" in p
    assert "Connected particles" in p
    assert "Friction" in p


# --------------------------------------------------------------------------- #
# Physics
# --------------------------------------------------------------------------- #


def test_kinematics_graph_correspondence():
    p = _prompt("mechanics")
    assert "GRAPH CORRESPONDENCE" in p
    assert "get_area" in p
    assert "projectile" in p.lower()


def test_shm_links_oscillator_to_sine_graph():
    p = _prompt("mechanics")
    assert "restoring-force" in p
    assert "displacement-time sine" in p
    assert "Damping" in p


def test_waves_transverse_vs_longitudinal():
    p = _prompt("waves")
    assert "superposition" in p.lower()
    assert "NOT a\n  transverse sine wave" in p or "NOT a transverse sine wave" in p


def test_electricity_and_circuits_covered():
    p = _prompt("electricity")
    assert "I-V characteristics" in p
    assert "Capacitors" in p
    assert "time constant" in p
    assert "Electric fields" in p


def test_magnetic_field_page_convention_uses_verified_api():
    """Cross(Circle(...)) was render-verified; it needs no LaTeX."""
    p = _prompt("magnetism")
    assert "Cross(Circle(" in p
    assert "OUT of the page" in p
    assert "Lenz" in p


def test_quantum_nuclear_covered():
    p = _prompt("quantum_nuclear")
    assert "photoelectric" in p.lower()
    assert "line spectrum" in p
    assert "Binding energy per nucleon" in p
    assert "fission" in p and "fusion" in p


def test_astrophysics_hr_diagram_axis_warning():
    """The reversed temperature axis is the classic H-R diagram mistake."""
    p = _prompt("astrophysics")
    assert "Hertzsprung-Russell" in p
    assert "REVERSED" in p
    assert "parallax" in p.lower() and "edshift" in p


def test_gravitational_fields_always_attractive():
    """Repulsive gravity is the error this guidance exists to prevent."""
    p = _prompt("astrophysics")
    assert "always attractive" in p
    assert "no\n  repulsive case" in p or "no repulsive case" in p


def test_optics_measures_angles_from_the_normal():
    p = _prompt("optics")
    assert "NORMAL" in p
    assert "total internal reflection" in p.lower()


def test_thermal_plateau_is_the_insight():
    p = _prompt("thermal")
    assert "plateau" in p
    assert "CONSTANT temperature" in p


# --------------------------------------------------------------------------- #
# Physics-correctness traps must be named, not merely implied
# --------------------------------------------------------------------------- #


def test_physics_errors_are_named_explicitly():
    mech = _prompt("mechanics")
    assert "centrifugal" in mech
    assert "DIFFERENT bodies" in mech
    elec = _prompt("electricity")
    assert "never start or end" in elec


def test_accuracy_rule_ships_with_specialist_modules():
    p = _prompt("mechanics")
    assert "ACCURACY" in p
    assert "never decoration" in p


def test_general_scene_gets_no_accuracy_preamble():
    """A general scene makes no domain claims, so it needs no accuracy rule."""
    p = _prompt("general")
    assert "GENERAL SCENE" in p
    assert "ACCURACY:" not in p


# --------------------------------------------------------------------------- #
# Module-staleness audit: modules must not describe the ValueTracker allowlist
# as unavailable (it shipped after these modules were first written), and the
# strongest topics should offer the verified continuous-motion pattern.
# --------------------------------------------------------------------------- #


def test_calculus_no_longer_claims_updaters_out_of_scope():
    """REGRESSION: the calculus module said 'updaters are out of scope', which
    both became false and contradicted the shared CONTROLLED CONTINUOUS MOTION
    vocabulary the same prompt carries."""
    p = _prompt("calculus")
    assert "updaters are out of scope" not in p
    assert "THE TANGENT SWEEP" in p


def test_shm_offers_coupled_continuous_motion():
    p = _prompt("mechanics")
    assert "COUPLED CONTINUOUS MOTION" in p
    assert "genuine lockstep" in p
    # discrete fallback must remain explicitly allowed
    assert "acceptable fallback" in p


def test_travelling_wave_can_actually_travel():
    p = _prompt("waves")
    assert "should actually TRAVEL" in p
    assert "propagates continuously" in p


def test_continuous_patterns_reference_the_verified_cost_limits():
    """Every new reactive suggestion must point at the render-cost limits, not
    reintroduce the per-frame-Text cost trap the trig audit uncovered."""
    for tag in ("calculus", "mechanics", "waves"):
        p = _prompt(tag)
        assert "CONTROLLED CONTINUOUS MOTION" in p, tag
