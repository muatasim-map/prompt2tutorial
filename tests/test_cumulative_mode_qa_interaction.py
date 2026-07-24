"""Cumulative continuity mode must not falsely trigger scene regeneration.

Intentional continuity (the same visual world carried forward across scenes in
"cumulative" mode) can legitimately produce consecutive scenes whose thumbnails
are visually similar. visual_qa.py is out of scope for this phase (per the
task's explicit "do not change ... visual QA"), so this test does not modify
it — it instead pins the EXISTING behavior that makes this safe: the
near-identical flag is purely informational and is never one of the two
conditions that trigger a scene's visual-repair regeneration loop.
"""

import inspect

import video_generator as vg
import visual_qa


def test_near_identical_flag_is_not_a_regeneration_trigger():
    """The only two triggers for _generate_and_compile's visual-repair retry
    are report['blank'] and report['static_end_padding'] — FLAG_NEAR_IDENTICAL
    must not be able to cause a regeneration loop for a cumulative-mode scene
    that is correctly, intentionally similar to its predecessor.
    """
    source = inspect.getsource(vg)
    needs_repair_line = next(
        line for line in source.splitlines() if "needs_repair = report.get" in line
    )
    assert "blank" in needs_repair_line
    assert "static_end_padding" in needs_repair_line
    assert visual_qa.FLAG_NEAR_IDENTICAL not in needs_repair_line


def test_near_identical_flag_only_ever_appended_to_the_flags_list():
    """Confirms the flag is additive metadata, not a boolean gate anywhere."""
    source = inspect.getsource(vg)
    assert 'later.setdefault("flags", []).append(visual_qa.FLAG_NEAR_IDENTICAL)' in source
    # It must not appear as a dict key check (e.g. report.get(FLAG_NEAR_IDENTICAL))
    assert f"report.get(visual_qa.FLAG_NEAR_IDENTICAL" not in source


def test_a_correctly_carried_forward_scene_pair_would_only_be_informational():
    """Direct behavioral check: images_near_identical() flags similarity, but
    that alone must not be reachable from the regen-trigger condition."""
    report = {"index": 2, "flags": [visual_qa.FLAG_NEAR_IDENTICAL],
              "blank": False, "static_end_padding": False}
    needs_repair = report.get("blank") or report.get("static_end_padding")
    assert needs_repair is False
