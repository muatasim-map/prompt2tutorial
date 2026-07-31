"""Tests for FFmpeg command construction and FFprobe output validation."""

import ffmpeg_utils
from ffmpeg_utils import (
    MediaInfo,
    build_concat_audio_cmd,
    build_concat_list_text,
    build_concat_video_cmd,
    build_concat_video_transcode_cmd,
    build_mux_cmd,
    build_mux_transcode_audio_cmd,
    parse_probe,
    validate_output,
)


def test_concat_list_text_uses_posix_and_escapes_quotes():
    text = build_concat_list_text(["/tmp/a.mp4", "/tmp/it's here.mp4"])
    lines = text.strip().splitlines()
    assert lines[0].startswith("file '")
    # single quote inside path is escaped for the concat demuxer
    assert "'\\''" in lines[1]


def test_concat_audio_cmd_reencodes_aac():
    cmd = build_concat_audio_cmd("list.txt", "out.m4a")
    assert cmd[0] == ffmpeg_utils.FFMPEG_BIN
    assert "-f" in cmd and "concat" in cmd
    assert "aac" in cmd
    assert "44100" in cmd  # normalized sample rate
    assert cmd[-1] == "out.m4a"


def test_concat_video_cmd_preserves_rendered_stream():
    cmd = build_concat_video_cmd("list.txt", "out.mp4")
    assert "copy" in cmd
    assert "libx264" not in cmd
    assert "-an" in cmd  # silent concat
    assert "+faststart" in cmd


def test_concat_video_fallback_cmd_normalizes_h264():
    cmd = build_concat_video_transcode_cmd("list.txt", "out.mp4")
    assert "libx264" in cmd
    assert "yuv420p" in cmd
    assert "-an" in cmd


def test_mux_cmd_has_faststart_and_maps():
    cmd = build_mux_cmd("v.mp4", "a.m4a", "final.mp4")
    assert "+faststart" in cmd
    assert "0:v:0" in cmd
    assert "1:a:0" in cmd
    assert cmd[cmd.index("-c:v") + 1] == "copy"
    assert cmd[cmd.index("-c:a") + 1] == "copy"


def test_mux_fallback_cmd_reencodes_only_audio():
    cmd = build_mux_transcode_audio_cmd("v.mp4", "a.m4a", "final.mp4")
    assert cmd[cmd.index("-c:v") + 1] == "copy"
    assert cmd[cmd.index("-c:a") + 1] == "aac"
    assert "192k" in cmd


def test_parse_probe_reads_streams():
    probe = {
        "format": {"duration": "12.5"},
        "streams": [
            {"codec_type": "video", "width": 854, "height": 480, "nb_frames": "180"},
            {"codec_type": "audio"},
        ],
    }
    info = parse_probe(probe)
    assert info.duration == 12.5
    assert info.has_video and info.has_audio
    assert info.video_frames == 180
    assert info.width == 854


def test_validate_output_missing_file(tmp_path):
    ok, reason, info = validate_output(tmp_path / "nope.mp4", expect_audio=False)
    assert not ok
    assert "not created" in reason


def test_validate_output_empty_file(tmp_path):
    f = tmp_path / "empty.mp4"
    f.write_bytes(b"")
    ok, reason, _ = validate_output(f, expect_audio=False)
    assert not ok
    assert "empty" in reason


def _patch_probe(monkeypatch, info):
    monkeypatch.setattr(ffmpeg_utils, "probe_media", lambda *a, **k: info)


def test_validate_output_no_video_stream(tmp_path, monkeypatch):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x")
    _patch_probe(monkeypatch, MediaInfo(5.0, has_video=False, has_audio=True,
                                        video_frames=1, width=1, height=1))
    ok, reason, _ = validate_output(f, expect_audio=True)
    assert not ok and "video stream" in reason


def test_validate_output_zero_duration(tmp_path, monkeypatch):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x")
    _patch_probe(monkeypatch, MediaInfo(0.0, True, False, 10, 1, 1))
    ok, reason, _ = validate_output(f, expect_audio=False)
    assert not ok and "duration is zero" in reason


def test_validate_output_missing_audio_when_expected(tmp_path, monkeypatch):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x")
    _patch_probe(monkeypatch, MediaInfo(5.0, True, has_audio=False, video_frames=10, width=1, height=1))
    ok, reason, _ = validate_output(f, expect_audio=True)
    assert not ok and "audio stream" in reason


def test_validate_output_duration_out_of_tolerance(tmp_path, monkeypatch):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x")
    _patch_probe(monkeypatch, MediaInfo(60.0, True, True, 100, 1, 1))
    ok, reason, _ = validate_output(f, expect_audio=True, expected_duration=10.0, tolerance=0.5)
    assert not ok and "differs from expected" in reason


def test_validate_output_success(tmp_path, monkeypatch):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x")
    _patch_probe(monkeypatch, MediaInfo(10.2, True, True, 150, 854, 480))
    ok, reason, info = validate_output(f, expect_audio=True, expected_duration=10.0)
    assert ok and reason == "ok"
    assert info.duration == 10.2
