"""Tests for visual-QA command construction, flag parsing, and contact sheet."""

import numpy as np
import pytest

import visual_qa
from config import get_visual_config
from visual_qa import (
    FLAG_BLANK,
    FLAG_EDGE_CLIP,
    FLAG_TEXT_ONLY,
    FLAG_WHITE,
    analyze_array,
    build_contact_sheet,
    build_extract_frame_cmd,
    flag_frame,
    frame_timestamps,
    storyboard_metadata_flags,
)

CFG = get_visual_config()


# --- command construction -------------------------------------------------- #

def test_extract_frame_cmd():
    cmd = build_extract_frame_cmd("in.mp4", 3.5, "out.png")
    assert cmd[0] == visual_qa.FFMPEG_BIN
    assert "-ss" in cmd and "3.500" in cmd
    assert "-frames:v" in cmd and "1" in cmd
    assert cmd[-1] == "out.png"


def test_frame_timestamps_spacing():
    assert frame_timestamps(10.0, 3) == [2.5, 5.0, 7.5]
    assert frame_timestamps(10.0, 1) == [5.0]


# --- flag parsing ---------------------------------------------------------- #

def _frame(fill, size=(120, 120)):
    return np.full(size, fill, dtype=np.float64)


def test_blank_frame_flagged():
    stats = analyze_array(_frame(4))
    assert FLAG_BLANK in flag_frame(stats, CFG)


def test_white_frame_flagged():
    stats = analyze_array(_frame(250))
    assert FLAG_WHITE in flag_frame(stats, CFG)


def test_good_frame_has_no_flags():
    arr = _frame(30)  # dark background
    arr[45:75, 45:75] = 220  # bright centered content, away from edges
    stats = analyze_array(arr)
    assert flag_frame(stats, CFG) == []


def test_edge_content_flagged():
    arr = _frame(30)
    arr[0:8, :] = 230   # bright band along the top edge -> possible clipping
    stats = analyze_array(arr)
    assert FLAG_EDGE_CLIP in flag_frame(stats, CFG)


def test_storyboard_text_only_flag():
    text_entry = {"primary_objects": ["title text", "bullet label"],
                  "composition": "centered text card", "visual_metaphor": "bullet list"}
    assert FLAG_TEXT_ONLY in storyboard_metadata_flags(text_entry)

    visual_entry = {"primary_objects": ["rotating gear", "moving ball"],
                    "composition": "split screen", "visual_metaphor": "clockwork"}
    assert FLAG_TEXT_ONLY not in storyboard_metadata_flags(visual_entry)


def test_storyboard_metadata_flags_none():
    assert storyboard_metadata_flags(None) == []


# --- near-identical detection + contact sheet (Pillow, offline) ------------ #

def _write_img(path, color):
    from PIL import Image
    Image.new("RGB", (160, 90), color).save(path)


def test_near_identical_scenes(tmp_path):
    a = tmp_path / "t1.png"
    b = tmp_path / "t2.png"
    c = tmp_path / "t3.png"
    _write_img(a, (10, 10, 10))
    _write_img(b, (10, 10, 10))   # identical to a
    _write_img(c, (240, 240, 240))  # very different
    pairs = visual_qa.flag_near_identical_scenes([a, b, c], CFG)
    assert (0, 1) in pairs
    assert (1, 2) not in pairs


def test_build_contact_sheet(tmp_path):
    thumbs = []
    for i, color in enumerate([(200, 0, 0), (0, 200, 0), (0, 0, 200), (200, 200, 0)]):
        p = tmp_path / f"thumb_{i}.png"
        _write_img(p, color)
        thumbs.append(p)
    out = tmp_path / "sheet.png"
    ok = build_contact_sheet(thumbs, out, cols=3, labels=[f"Scene {i+1}" for i in range(4)])
    assert ok and out.exists()
    from PIL import Image
    with Image.open(out) as im:
        assert im.width > 0 and im.height > 0


def test_contact_sheet_no_thumbs(tmp_path):
    assert build_contact_sheet([], tmp_path / "x.png") is False
