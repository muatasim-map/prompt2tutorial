"""Tests for Pydantic script/scene validation."""

import pytest

import schemas
from schemas import (
    MAX_SCENES,
    ScriptValidationError,
    parse_manim_code,
    parse_script,
    parse_script_from_text,
    validate_target_duration,
)


def _scene(**overrides):
    base = {
        "chapter": "Chapter 1: Intro",
        "text": "Language models turn text into numbers.",
        "animation": "Show the word Hello then split into tokens.",
        "objective": "Understand tokenization.",
        "explanation": "Text is parsed into tokens mapped to ids.",
    }
    base.update(overrides)
    return base


def test_valid_script_parses():
    script = parse_script([_scene(), _scene(duration=8.0)])
    assert len(script.scenes) == 2
    assert script.scenes[1].duration == 8.0
    dicts = script.as_scene_dicts()
    assert dicts[0]["chapter"] == "Chapter 1: Intro"


def test_blank_text_rejected():
    with pytest.raises(ScriptValidationError) as exc:
        parse_script([_scene(text="   ")])
    assert "text" in str(exc.value)


def test_blank_animation_rejected():
    with pytest.raises(ScriptValidationError):
        parse_script([_scene(animation="")])


def test_missing_field_rejected():
    bad = _scene()
    del bad["objective"]
    with pytest.raises(ScriptValidationError):
        parse_script([bad])


def test_too_many_scenes_rejected():
    with pytest.raises(ScriptValidationError):
        parse_script([_scene() for _ in range(MAX_SCENES + 1)])


def test_zero_scenes_rejected():
    with pytest.raises(ScriptValidationError):
        parse_script([])


def test_excessively_long_text_rejected():
    with pytest.raises(ScriptValidationError):
        parse_script([_scene(text="x" * (schemas.MAX_TEXT_CHARS + 1))])


def test_unicode_preserved():
    script = parse_script([_scene(text="¿Qué es un modelo? 日本語 café", chapter="Capítulo 1")])
    assert "日本語" in script.scenes[0].text
    assert "café" in script.scenes[0].text


def test_whitespace_normalized_but_newlines_kept():
    script = parse_script([_scene(text="hello    world\nsecond   line")])
    assert script.scenes[0].text == "hello world\nsecond line"


def test_scene_duration_over_max_rejected():
    with pytest.raises(ScriptValidationError):
        parse_script([_scene(duration=999)])


def test_non_positive_duration_becomes_none():
    script = parse_script([_scene(duration=0)])
    assert script.scenes[0].duration is None


def test_object_with_scenes_key():
    script = parse_script({"scenes": [_scene()]})
    assert len(script.scenes) == 1


def test_markdown_fenced_json_extracted():
    raw = '```json\n[' + '{"chapter":"C","text":"t","animation":"a","objective":"o","explanation":"e"}' + ']\n```'
    script = parse_script_from_text(raw)
    assert script.scenes[0].chapter == "C"


def test_invalid_json_text_raises():
    with pytest.raises(ScriptValidationError):
        parse_script_from_text("not json at all")


def test_extra_future_fields_allowed():
    # Forward-compatibility: template/style fields must not break validation.
    script = parse_script([_scene(style="dark", transition="fade")])
    assert script.scenes[0].chapter


def test_target_duration_clamped():
    assert validate_target_duration(1) == schemas.MIN_TARGET_DURATION
    assert validate_target_duration(99999) == schemas.MAX_TARGET_DURATION
    assert validate_target_duration("60") == 60
    assert validate_target_duration("bad") == 60


def test_manim_code_validation():
    code = parse_manim_code({"content": "from manim import *\nclass A(Scene):\n    def construct(self): pass", "class_name": "A"})
    assert code.class_name == "A"


def test_manim_code_blank_rejected():
    with pytest.raises(ScriptValidationError):
        parse_manim_code({"content": "   ", "class_name": "A"})


def test_manim_code_invalid_class_name_rejected():
    with pytest.raises(ScriptValidationError):
        parse_manim_code({"content": "class A(Scene): pass", "class_name": "1bad name"})
