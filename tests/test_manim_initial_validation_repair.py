"""Regression coverage for invalid initial Manim source recovery."""

from types import SimpleNamespace

import manim_generator as mg


class _RepairingService:
    roles = SimpleNamespace(animation="animation-model")

    def __init__(self):
        self.calls = []

    def generate(self, *, role, **kwargs):
        self.calls.append(role)
        if role == "animation":
            return SimpleNamespace(
                text=(
                    '{"content":"from manim import *\\nclass Broken(Scene):\\n'
                    '    def construct(self):\\n        self.add(Circle())\\n'
                    '          self.wait()","class_name":"Broken"}'
                ),
                model="animation-model",
                used_fallback=False,
                fallback_reason=None,
            )
        return SimpleNamespace(
            text=(
                '{"content":"from manim import *\\nclass Broken(Scene):\\n'
                '    def construct(self):\\n        self.add(Circle())",'
                '"class_name":"Broken","fix_explanation":"fixed indentation"}'
            ),
            model="repair-model",
        )


def test_invalid_initial_python_gets_one_repair_attempt():
    service = _RepairingService()

    result = mg.generate_manim_code(
        service=service,
        text="Narration",
        animation="Draw a circle",
        index=3,
        provider="gemini",
    )

    assert service.calls == ["animation", "repair"]
    assert result["content"]
    assert result["class_name"] == "Broken"
    assert result["initial_validation_repaired"] is True


class _MissingHelperImportService:
    roles = SimpleNamespace(animation="animation-model")

    def __init__(self):
        self.calls = []

    def generate(self, *, role, **kwargs):
        self.calls.append(role)
        return SimpleNamespace(
            text=(
                '{"content":"from manim import *\\nclass Demo(Scene):\\n'
                '    def construct(self):\\n        self.add(grid([[1]]))",'
                '"class_name":"Demo"}'
            ),
            model="animation-model",
            used_fallback=False,
            fallback_reason=None,
        )


def test_missing_project_helper_import_is_fixed_without_an_llm_repair():
    service = _MissingHelperImportService()

    result = mg.generate_manim_code(
        service=service,
        text="Narration",
        animation="Draw a grid",
        index=4,
        provider="gemini",
    )

    assert service.calls == ["animation"]
    assert "from visual_primitives import *" in result["content"]
    assert result["safe_source_fixes"] == ["visual_primitives_import"]


class _StillBrokenService(_RepairingService):
    def generate(self, *, role, **kwargs):
        result = super().generate(role=role, **kwargs)
        if role == "repair":
            return SimpleNamespace(
                text='{"content":"still invalid","class_name":"Broken"}',
                model="repair-model",
            )
        return result


def test_failed_initial_repairs_return_structured_invalid_output():
    service = _StillBrokenService()

    result = mg.generate_manim_code(
        service=service,
        text="Narration",
        animation="Draw a circle",
        index=3,
        provider="gemini",
    )

    assert service.calls == ["animation", "repair", "repair"]
    assert result["content"] == ""
    assert result["error_category"] == "invalid_output"
    assert "after two repair attempts" in result["error_message"]


class _SecondRepairSucceedsService(_RepairingService):
    def __init__(self):
        super().__init__()
        self.repair_count = 0

    def generate(self, *, role, **kwargs):
        if role == "animation":
            return super().generate(role=role, **kwargs)
        self.calls.append(role)
        self.repair_count += 1
        if self.repair_count == 1:
            return SimpleNamespace(
                text='{"content":"still invalid","class_name":"Broken"}',
                model="repair-model",
            )
        return SimpleNamespace(
            text=(
                '{"content":"from manim import *\\nclass Broken(Scene):\\n'
                '    def construct(self):\\n        self.add(Circle())",'
                '"class_name":"Broken","fix_explanation":"fixed on second pass"}'
            ),
            model="repair-model",
        )


def test_second_source_repair_can_recover_without_rendering_broken_code():
    service = _SecondRepairSucceedsService()

    result = mg.generate_manim_code(
        service=service,
        text="Narration",
        animation="Draw a circle",
        index=3,
        provider="gemini",
    )

    assert service.calls == ["animation", "repair", "repair"]
    assert result["content"]
    assert result["initial_validation_repair_attempts"] == 2
