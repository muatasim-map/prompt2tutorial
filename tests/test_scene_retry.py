"""Tests that a blank scene triggers exactly one scene-level visual repair —
never a whole-video regeneration. All heavy operations are mocked (no real
Manim, FFmpeg, or LLM calls)."""

import dataclasses

import pytest

import video_generator as vg
from config import get_visual_config
from media_paths import JobWorkspace


@pytest.fixture
def ws(tmp_path):
    return JobWorkspace("retry-job", base_dir=tmp_path).create()


def _fake_code(_i=1):
    return {"content": "from manim import *\nclass X(Scene):\n    def construct(self):\n        pass",
            "class_name": "X"}


def _install_fakes(monkeypatch, qa_blank_sequence):
    """Fake generate_manim_code, compile_video, and _scene_qa; return call counters."""
    counters = {"gen": 0, "compile": 0, "qa": 0}

    def fake_gen(**kwargs):
        counters["gen"] += 1
        return _fake_code()

    def fake_compile(code_path, class_name, media_dir, timeout=300, is_3d=False):
        counters["compile"] += 1
        from pathlib import Path
        Path(media_dir).mkdir(parents=True, exist_ok=True)
        out = Path(media_dir) / "render.mp4"
        out.write_bytes(b"\x00\x00")  # dummy non-empty file
        return str(out), None

    qa_seq = list(qa_blank_sequence)

    def fake_qa(ws_, index, video_path, vcfg, storyboard_entry):
        blank = qa_seq[min(counters["qa"], len(qa_seq) - 1)]
        counters["qa"] += 1
        return {"index": index, "flags": ([] if not blank else ["blank_or_black"]), "blank": blank}

    monkeypatch.setattr(vg.manim_generator, "generate_manim_code", fake_gen)
    monkeypatch.setattr(vg, "compile_video", fake_compile)
    monkeypatch.setattr(vg, "_scene_qa", fake_qa)
    monkeypatch.setenv("SCENE_CACHE_ENABLED", "false")
    return counters


def _render(ws, vcfg):
    scene = {"text": "t", "animation": "a", "chapter": "c", "objective": "o", "explanation": "e"}
    return vg._render_scene(
        ws=ws, service=object(), provider="gemini", client=None, index=1, total=3,
        scene=scene, previous_context=None, audio_duration=None, bypass_scene_cache=True,
        job_id="retry-job", provider_model="m", storyboard_entry=None,
        global_style="style", ledger_summary="(none)", vcfg=vcfg,
    )


def test_blank_scene_triggers_one_visual_repair(monkeypatch, ws):
    # First render is blank, the repaired render is good.
    counters = _install_fakes(monkeypatch, qa_blank_sequence=[True, False])
    vcfg = dataclasses.replace(get_visual_config(), visual_repair_attempts=1, visual_qa_enabled=True)

    dest, prev_ctx, report = _render(ws, vcfg)

    assert dest is not None
    assert counters["gen"] == 2      # original + exactly one visual repair
    assert counters["compile"] == 2
    assert report["blank"] is False  # repaired scene is not blank


def test_no_repair_when_attempts_zero(monkeypatch, ws):
    counters = _install_fakes(monkeypatch, qa_blank_sequence=[True, True])
    vcfg = dataclasses.replace(get_visual_config(), visual_repair_attempts=0, visual_qa_enabled=True)

    dest, _, report = _render(ws, vcfg)

    assert dest is not None
    assert counters["gen"] == 1      # no visual repair attempted
    assert report["blank"] is True


def test_good_scene_no_repair(monkeypatch, ws):
    counters = _install_fakes(monkeypatch, qa_blank_sequence=[False])
    vcfg = dataclasses.replace(get_visual_config(), visual_repair_attempts=1, visual_qa_enabled=True)

    dest, _, report = _render(ws, vcfg)

    assert dest is not None
    assert counters["gen"] == 1      # not blank -> no repair
    assert report["blank"] is False


def test_repair_capped_at_configured_attempts(monkeypatch, ws):
    # Stays blank even after repair; must not loop beyond the budget of 1.
    counters = _install_fakes(monkeypatch, qa_blank_sequence=[True, True, True])
    vcfg = dataclasses.replace(get_visual_config(), visual_repair_attempts=1, visual_qa_enabled=True)

    dest, _, report = _render(ws, vcfg)

    assert dest is not None
    assert counters["gen"] == 2      # original + 1 repair only (capped)


@pytest.mark.parametrize("flag", [
    "long_static_run",
    "no_meaningful_change",
    "text_below_minimum_size",
    "too_many_text_mobjects",
    "content_near_edge_possible_clipping",
])
def test_advisory_visual_flag_does_not_trigger_repair_by_default(monkeypatch, ws, flag):
    counters = _install_fakes(monkeypatch, qa_blank_sequence=[False])

    reports = iter([{"index": 1, "flags": [flag], "blank": False}])
    monkeypatch.setattr(vg, "_scene_qa", lambda *args, **kwargs: next(reports))
    vcfg = dataclasses.replace(
        get_visual_config(), visual_repair_attempts=1, visual_qa_enabled=True
    )

    _, _, report = _render(ws, vcfg)

    assert counters["gen"] == 1
    assert flag in report["flags"]


def test_advisory_visual_repair_can_be_enabled(monkeypatch, ws):
    counters = _install_fakes(monkeypatch, qa_blank_sequence=[False, False])
    reports = iter([
        {"index": 1, "flags": ["long_static_run"], "blank": False},
        {"index": 1, "flags": [], "blank": False},
    ])
    monkeypatch.setattr(vg, "_scene_qa", lambda *args, **kwargs: next(reports))
    vcfg = dataclasses.replace(
        get_visual_config(),
        visual_repair_attempts=1,
        visual_qa_enabled=True,
        auto_repair_advisory_qa=True,
    )

    _, _, report = _render(ws, vcfg)

    assert counters["gen"] == 2
    assert report["flags"] == []


def test_visual_repair_feedback_names_quality_failure():
    report = {
        "flags": ["text_below_minimum_size", "too_many_text_mobjects"],
        "blank": False,
    }
    reason = vg._visual_repair_reason(report, 8.0, allow_advisory=True)
    assert reason is not None
    assert "font size" in reason[1]
    assert "text objects" in reason[1]
