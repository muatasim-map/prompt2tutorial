"""Performance-related concurrency must not change ordering or content."""

import random
import time

import pytest

import tts_generator as tts
import visual_qa


def test_parallel_tts_preserves_scene_order(monkeypatch, tmp_path):
    """Fragments finish out of order but must be assembled in scene order."""
    monkeypatch.setenv("TTS_MAX_CONCURRENCY", "4")

    def fake_fragment(**kwargs):
        index = kwargs["index"]
        # Later scenes finish FIRST — the worst case for ordering.
        time.sleep(0.05 * (5 - index))
        path = tmp_path / f"fragment_{index}.mp3"
        path.write_bytes(b"\x00")
        return str(path), float(index)

    monkeypatch.setattr(tts, "generate_audio_fragment", fake_fragment)
    captured = {}
    monkeypatch.setattr(tts, "concatenate_audio_fragments",
                        lambda frags, out, lst: captured.setdefault("frags", list(frags)) or True)

    video_data = [{"text": f"scene {i}"} for i in range(1, 5)]
    _out, durations = tts.generate_complete_audio(
        client=None, video_data=video_data, output_dir=tmp_path,
        output_path=tmp_path / "n.m4a", list_file=tmp_path / "l.txt",
    )

    assert captured["frags"] == [str(tmp_path / f"fragment_{i}.mp3") for i in range(1, 5)]
    assert durations == {1: 1.0, 2: 2.0, 3: 3.0, 4: 4.0}


def test_parallel_tts_skips_blank_text(monkeypatch, tmp_path):
    monkeypatch.setattr(tts, "generate_audio_fragment",
                        lambda **kw: (str(tmp_path / f"f{kw['index']}.mp3"), 1.0))
    for i in (1, 3):
        (tmp_path / f"f{i}.mp3").write_bytes(b"\x00")
    captured = {}
    monkeypatch.setattr(tts, "concatenate_audio_fragments",
                        lambda frags, out, lst: captured.setdefault("frags", list(frags)) or True)

    video_data = [{"text": "a"}, {"text": ""}, {"text": "c"}]
    tts.generate_complete_audio(
        client=None, video_data=video_data, output_dir=tmp_path,
        output_path=tmp_path / "n.m4a", list_file=tmp_path / "l.txt")

    assert captured["frags"] == [str(tmp_path / "f1.mp3"), str(tmp_path / "f3.mp3")]


def test_parallel_frame_extraction_preserves_time_order(monkeypatch, tmp_path):
    """Frames come back chronologically regardless of ffmpeg completion order."""
    class R:
        returncode = 0

    def fake_run(cmd, timeout=120):
        out = tmp_path / cmd[-1] if not str(cmd[-1]).startswith(str(tmp_path)) else cmd[-1]
        time.sleep(random.uniform(0, 0.03))
        open(cmd[-1], "wb").write(b"\x89PNG")
        return R()

    monkeypatch.setattr(visual_qa, "_run", fake_run)
    outs = [tmp_path / f"f_{i}.png" for i in range(5)]
    created = visual_qa.extract_frames("v.mp4", [0.5, 1.5, 2.5, 3.5, 4.5], outs)
    assert created == outs
