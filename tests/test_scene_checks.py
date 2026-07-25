"""Tests for the static pre-render motion gate (scene_checks).

The gate exists because the animation prompt's "transform, don't rebuild"
instruction is ignored in ~60% of generated scenes. These tests pin the two
behaviours that matter: it must fire on genuine delete-and-rebuild, and it must
NOT fire on scenes that are simply small.
"""

import pytest

from scene_checks import (
    FLAG_NO_MORPH,
    MIN_FADEOUTS_FOR_FLAG,
    MORPH_VERBS,
    analyze_scene_code,
    build_motion_feedback,
    check_scene_motion,
    needs_motion_revision,
)

# A textbook slideshow scene: build, discard, rebuild, discard.
DELETE_AND_REBUILD = '''
from manim import *

class Scene1(Scene):
    def construct(self):
        title = Text("Kinetic Energy")
        self.play(FadeIn(title))
        self.wait(1)
        self.play(FadeOut(title))

        formula = Text("KE = 1/2 m v^2")
        self.play(FadeIn(formula))
        self.wait(1)
        self.play(FadeOut(formula))
'''

# Same lesson, but the title BECOMES the formula.
CARRIES_FORWARD = '''
from manim import *

class Scene1(Scene):
    def construct(self):
        title = Text("Kinetic Energy")
        self.play(FadeIn(title))
        formula = Text("KE = 1/2 m v^2")
        self.play(ReplacementTransform(title, formula))
        self.wait(1)
        self.play(FadeOut(formula))
'''

# Legitimately simple: one object, one clean exit. Must not be flagged.
SIMPLE_SINGLE_FADEOUT = '''
from manim import *

class Scene1(Scene):
    def construct(self):
        dot = Dot()
        self.play(Create(dot))
        self.play(dot.animate.shift(RIGHT * 2))
        self.play(FadeOut(dot))
'''


# --- fact extraction ------------------------------------------------------- #

def test_extracts_fadeouts_and_discarded_names():
    facts = analyze_scene_code(DELETE_AND_REBUILD)
    assert facts.fadeout_count == 2
    assert facts.discarded_names == ["title", "formula"]
    assert facts.morph_verbs_used == []
    assert facts.uses_any_morph is False


def test_detects_morph_verb():
    facts = analyze_scene_code(CARRIES_FORWARD)
    assert "ReplacementTransform" in facts.morph_verbs_used
    assert facts.uses_any_morph is True


def test_counts_play_calls():
    assert analyze_scene_code(DELETE_AND_REBUILD).play_call_count == 4


@pytest.mark.parametrize("verb", sorted(MORPH_VERBS))
def test_every_morph_verb_is_recognised(verb):
    code = f"""
from manim import *

class S(Scene):
    def construct(self):
        self.play(FadeOut(a))
        self.play(FadeOut(b))
        x = {verb}(a, b)
"""
    facts = analyze_scene_code(code)
    assert verb in facts.morph_verbs_used
    assert needs_motion_revision(facts) is False, f"{verb} should satisfy the gate"


def test_attribute_and_subscript_first_args_are_named():
    code = """
from manim import *

class S(Scene):
    def construct(self):
        self.play(FadeOut(self.header))
        self.play(FadeOut(group[0]))
"""
    assert analyze_scene_code(code).discarded_names == ["header", "group"]


# --- gate decision --------------------------------------------------------- #

def test_flags_delete_and_rebuild():
    assert needs_motion_revision(analyze_scene_code(DELETE_AND_REBUILD)) is True


def test_does_not_flag_scene_that_transforms():
    assert needs_motion_revision(analyze_scene_code(CARRIES_FORWARD)) is False


def test_does_not_flag_single_fadeout_scene():
    facts = analyze_scene_code(SIMPLE_SINGLE_FADEOUT)
    assert facts.fadeout_count < MIN_FADEOUTS_FOR_FLAG
    assert needs_motion_revision(facts) is False


def test_bare_animate_does_not_satisfy_the_gate():
    """`.animate` mutates in place and is near-universal — counting it would
    make the gate fire almost never, defeating the purpose."""
    code = """
from manim import *

class S(Scene):
    def construct(self):
        self.play(FadeIn(a))
        self.play(a.animate.shift(RIGHT))
        self.play(FadeOut(a))
        self.play(FadeIn(b))
        self.play(FadeOut(b))
"""
    assert needs_motion_revision(analyze_scene_code(code)) is True


def test_unparseable_code_is_never_flagged():
    facts = analyze_scene_code("class S(Scene:\n  def construct(")
    assert facts.parse_error is not None
    assert needs_motion_revision(facts) is False


# --- feedback message ------------------------------------------------------ #

def test_feedback_names_the_discarded_objects():
    msg = build_motion_feedback(analyze_scene_code(DELETE_AND_REBUILD))
    assert "`title`" in msg
    assert "`formula`" in msg
    assert "ReplacementTransform" in msg


def test_feedback_permits_declining():
    """A forced transform between unrelated shapes is worse than a clean cut,
    so the revision prompt must allow returning the code unchanged."""
    msg = build_motion_feedback(analyze_scene_code(DELETE_AND_REBUILD))
    assert "unchanged" in msg.lower()


def test_feedback_protects_duration_and_structure():
    msg = build_motion_feedback(analyze_scene_code(DELETE_AND_REBUILD))
    assert "duration" in msg.lower()
    assert "run_time" in msg


def test_feedback_without_recoverable_names_still_works():
    code = """
from manim import *

class S(Scene):
    def construct(self):
        self.play(FadeOut(VGroup(a, b)))
        self.play(FadeOut(make_thing()))
"""
    facts = analyze_scene_code(code)
    assert facts.discarded_names == []
    assert needs_motion_revision(facts) is True
    assert "2 times" in build_motion_feedback(facts)


# --- wrapper --------------------------------------------------------------- #

def test_check_scene_motion_returns_none_when_healthy():
    assert check_scene_motion(CARRIES_FORWARD) is None


def test_check_scene_motion_returns_feedback_when_flagged():
    assert check_scene_motion(DELETE_AND_REBUILD) is not None


def test_flag_constant_is_stable():
    assert FLAG_NO_MORPH == "no_morph_delete_and_rebuild"
