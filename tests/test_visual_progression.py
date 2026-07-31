"""Tests for purposeful visual progression, narration quality, and text safety.

All offline — no provider calls. Covers:
* optional/backward-compatible visual-direction fields;
* the visual-beat motion contract in the Manim prompt;
* duration-sync timing record + frozen-tail (static end padding) detection;
* narration pace, repetition, Unicode and mojibake handling;
* static-run / contact-sheet QA helpers.
"""

import dataclasses
import json

import pytest

import config
import manim_generator as mg
import schemas as S
import video_generator as vg
import visual_qa
from media_paths import JobWorkspace

CFG = config.get_visual_config()


def _sb_scene(**over):
    base = dict(index=1, learning_goal="g", key_concept="k", visual_metaphor="m",
                composition="c", primary_objects=["a"], primary_motion="p",
                color_role="blue", transition_from_prev="t", anti_repetition_notes="n")
    base.update(over)
    return base


# --- Part 3.1: optional, backward-compatible direction fields -------------- #

def test_visual_fields_are_optional_backward_compatible():
    """Old storyboards / reviewed scenes without the new fields must still validate."""
    scene = S.parse_storyboard([_sb_scene()]).scenes[0]
    assert scene.visual_beats == []
    assert scene.opening_state is None
    assert scene.ending_state is None
    assert scene.transformations == []
    assert scene.continuity_notes is None


def test_visual_beats_parse_and_order():
    scene = S.parse_storyboard([_sb_scene(
        opening_state="empty grid", ending_state="complete network",
        transformations=["split", "merge"], continuity_notes="carry the grid",
        visual_beats=[
            {"at_seconds": 0, "action": "reveal grid", "objects": ["grid"]},
            {"at_seconds": 3.5, "action": "weights update", "objects": ["edges"]},
        ],
    )]).scenes[0]
    assert [b.at_seconds for b in scene.visual_beats] == [0.0, 3.5]
    assert scene.visual_beats[1].action == "weights update"
    assert scene.transformations == ["split", "merge"]


def test_beat_requires_an_action():
    with pytest.raises(S.ScriptValidationError):
        S.parse_storyboard([_sb_scene(visual_beats=[{"at_seconds": 1, "action": "  "}])])


def test_reviewed_frontend_scene_still_validates():
    """The exact field set the review UI posts back must remain renderable."""
    reviewed = [{
        "chapter": "Edited Chapter", "text": "Edited narration.",
        "animation": "Edited animation.", "objective": "obj",
        "explanation": "expl", "duration": 6.0,
    }]
    script = S.parse_script(reviewed)
    assert script.scenes[0].duration == 6.0


# --- Part 3.2: motion contract present in the prompt ---------------------- #

def test_motion_contract_scales_beats_to_duration():
    contract = mg._motion_contract(12.0)
    assert "EXACTLY 12.00s" in contract
    assert "5 meaningful visual beats" in contract
    assert "every 2-3 seconds" in contract
    assert "FORBIDDEN" in contract


def test_motion_contract_forbids_frozen_tail():
    contract = mg._motion_contract(8.0)
    assert "frozen" in contract.lower()
    assert "FINAL self.wait() (after the last animation) must be <= 0.5s" in contract


def test_intentional_hold_is_distinguished_from_a_frozen_tail():
    """The 0.5s cap applies to the CLOSING wait only — a deliberate mid-scene
    hold after the key reveal is good direction and must stay permitted."""
    contract = mg._motion_contract(8.0)
    assert "HOLD THE KEY MOMENT" in contract
    assert "self.wait(0.6-1.0)" in contract
    assert "does not forbid the mid-scene key-moment hold" in contract


def test_prompt_includes_beats_and_text_rules():
    entry = _sb_scene(
        opening_state="single neuron", ending_state="full layer",
        transformations=["expand"],
        visual_beats=[{"at_seconds": 2.0, "action": "connect neurons", "objects": ["edge"]}],
    )
    prompt = mg._build_generation_prompt(
        text="narration", animation="anim", previous_context=None, audio_duration=9.0,
        chapter="Ch", objective="Obj", explanation="Expl",
        storyboard_entry=entry, global_style="style", ledger_summary="(none)",
    )
    assert "connect neurons" in prompt          # beat reached the model
    assert "~2.0s" in prompt                    # with its timing
    assert "single neuron" in prompt            # opening state
    assert "full layer" in prompt               # ending state
    assert "NEVER put the narration on screen" in prompt
    assert "PURPOSEFUL VISUAL PROGRESSION" in prompt


def test_format_beats_handles_missing_beats():
    assert "none supplied" in mg._format_beats(None)
    assert "none supplied" in mg._format_beats([])


# --- Part 3.3: duration sync records padding instead of hiding it --------- #

def test_duration_sync_writes_timing_record(tmp_path):
    code = "from manim import *\nclass A(Scene):\n    def construct(self):\n        pass\n"
    timing = tmp_path / "t.json"
    out = vg.append_duration_sync(code, 8.5, timing)
    assert "pad_seconds" in out and "_target = 8.5000" in out
    assert str(timing).replace("\\", "/") in out
    # exact sync is still enforced
    assert "self.wait(_pad)" in out


def test_duration_sync_replaces_terminal_hold_with_measured_audio_padding(tmp_path):
    code = """from manim import *
class A(Scene):
    def construct(self):
        self.play(Create(Circle()), run_time=2.0)
        self.wait(3.25)
"""
    out = vg.append_duration_sync(code, 2.5, tmp_path / "t.json")

    assert "self.wait(3.25)" not in out
    assert "self.play(Create(Circle()), run_time=2.0)" in out
    assert "_target = 2.5000" in out
    compile(out, "<duration-sync>", "exec")


def test_duration_sync_noop_without_audio():
    code = "class A(Scene):\n    def construct(self):\n        pass\n"
    assert vg.append_duration_sync(code, None) == code


def test_static_end_padding_is_flagged_for_review(tmp_path):
    ws = JobWorkspace("timing-job", base_dir=tmp_path).create()
    ws.scene_timing(1).write_text(json.dumps(
        {"animation_seconds": 4.0, "target_seconds": 9.0, "pad_seconds": 5.0}), encoding="utf-8")

    report = {"index": 1, "flags": []}
    vg._apply_timing_flags(ws, 1, report, 9.0, CFG)

    assert report["static_end_padding"] is True
    assert visual_qa.FLAG_STATIC_END_PADDING in report["flags"]
    assert visual_qa.FLAG_AV_DRIFT in report["flags"]   # 4.0s < 60% of 9.0s
    assert report["pad_seconds"] == 5.0


def test_small_padding_is_not_flagged(tmp_path):
    ws = JobWorkspace("timing-ok", base_dir=tmp_path).create()
    ws.scene_timing(1).write_text(json.dumps(
        {"animation_seconds": 8.6, "target_seconds": 9.0, "pad_seconds": 0.4}), encoding="utf-8")
    report = {"index": 1, "flags": []}
    vg._apply_timing_flags(ws, 1, report, 9.0, CFG)
    assert not report.get("static_end_padding")
    assert report["flags"] == []


def test_timing_flags_missing_file_is_safe(tmp_path):
    ws = JobWorkspace("no-timing", base_dir=tmp_path).create()
    report = {"index": 1, "flags": []}
    vg._apply_timing_flags(ws, 1, report, 9.0, CFG)   # must not raise
    assert report["flags"] == []


# --- Part 3.4: static-run QA helpers -------------------------------------- #

def test_longest_and_trailing_static_run():
    # 0.25s per step; a frozen block in the middle and a frozen tail
    maes = [5.0, 0.1, 0.1, 0.1, 0.1, 6.0, 0.0, 0.0, 0.0]
    assert visual_qa.longest_static_run(maes, 1.0, 0.25) == pytest.approx(1.0)
    assert visual_qa.trailing_static_run(maes, 1.0, 0.25) == pytest.approx(0.75)


def test_no_static_run_when_always_moving():
    maes = [5.0, 6.0, 7.0]
    assert visual_qa.longest_static_run(maes, 1.0, 0.25) == 0
    assert visual_qa.trailing_static_run(maes, 1.0, 0.25) == 0


# --- Part 4: narration quality + text safety ------------------------------ #

def test_narration_pace_detects_overlong_and_sparse():
    assert "too long" in S.check_narration_pace("word " * 60, 8.0)
    assert "too sparse" in S.check_narration_pace("two words", 10.0)
    assert S.check_narration_pace("word " * 20, 8.0) is None


def test_duplicate_narration_detected():
    dupes = S.find_duplicate_narration([
        "Neural nets learn by adjusting weights.",
        "Neural nets learn by adjusting weights!",
        "Gradients flow backward through the layers.",
    ])
    assert dupes and dupes[0][0] == 0 and dupes[0][1] == 1


def test_distinct_narration_not_flagged():
    assert S.find_duplicate_narration([
        "Forward pass computes a prediction.",
        "Backward pass propagates the error gradient.",
    ]) == []


def _corrupt(text, encoding="cp1252"):
    """Produce real mojibake: UTF-8 bytes wrongly decoded as a single-byte codepage.

    Returns ``None`` when the codepage cannot represent the bytes at all (e.g.
    cp1252 has no mapping for 0x81) — that combination simply cannot occur.
    """
    try:
        return text.encode("utf-8").decode(encoding)
    except UnicodeDecodeError:
        return None


@pytest.mark.parametrize("encoding", ["cp1252", "latin-1"])
@pytest.mark.parametrize("original", ["Gradient -> update", "café", "→ ✓ π"])
def test_mojibake_detected_and_repaired(encoding, original):
    bad = _corrupt(original, encoding)
    if bad is None or bad == original:
        pytest.skip("no corruption possible for this sample/codepage")
    assert S.detect_mojibake(bad), "corruption should be detected"
    repaired = S.repair_mojibake(bad)
    assert repaired == original, "original text must be exactly recovered"
    assert S.detect_mojibake(repaired) == []


def test_legitimate_unicode_preserved():
    good = "La función π ≈ 3.14 → converge, Σx²"
    assert S.detect_mojibake(good) == []
    assert S.repair_mojibake(good) == good
    # and it survives full scene validation intact
    scene = S.parse_script([{
        "chapter": "C", "text": good, "animation": "a",
        "objective": "o", "explanation": "e"}]).scenes[0]
    assert "π" in scene.text and "→" in scene.text


def test_narration_mojibake_repaired_during_validation():
    original = "Gradient → weight update"
    scene = S.parse_script([{
        "chapter": "C", "text": _corrupt(original, "latin-1"),
        "animation": "a", "objective": "o", "explanation": "e"}]).scenes[0]
    assert scene.text == original
    assert S.detect_mojibake(scene.text) == []


def test_word_count_and_estimate():
    assert S.narration_word_count("one two three") == 3
    assert S.estimate_narration_seconds("word " * 26) == pytest.approx(10.0, abs=0.1)
