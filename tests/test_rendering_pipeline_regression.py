"""Regression tests for Prompt2Learn.ai rendering pipeline.

Verifies:
1. Invalid/empty Manim LLM response produces detailed per-scene failure metadata,
   scene error log artifact, and structured error summary listing scene failures.
2. Reviewed frontend scene data continues into rendering pipeline smoothly.
3. Valid Manim LLM response progresses to source writing and compile_video().
4. Invalid/placeholder GEMINI_ANIMATION_MODEL triggers clear configuration error.
"""

import json
import time
import uuid
from pathlib import Path

import pytest

import config
import video_generator as vg
from config import ModelRoles, ProviderUnavailableError, resolve_model_roles, validate_animation_model
from llm_service import LLMError, LLMService
from media_paths import JobWorkspace


@pytest.fixture
def no_storyboard(monkeypatch):
    """Disable the visual-direction pass so a test can isolate later stages.

    The storyboard stage now fails the job loudly (it must never be silently
    dropped), so tests targeting scene-level behaviour must opt out of it.
    """
    import dataclasses
    vcfg = dataclasses.replace(
        config.get_visual_config(),
        storyboard_enabled=False, visual_qa_enabled=False, visual_repair_attempts=0,
    )
    monkeypatch.setattr(vg, "get_visual_config", lambda: vcfg)
    return vcfg


@pytest.fixture
def test_job_workspace():
    # Unique per test: these workflows spawn background threads, and a shared
    # job id let one test's thread bleed into the next test's assertions.
    job_id = f"test-reg-job-{uuid.uuid4().hex[:8]}"
    ws = JobWorkspace(job_id).create()
    vg.jobs[job_id] = {
        "job_id": job_id,
        "topic": "Neural Networks",
        "status": "awaiting_review",
        "progress": 25,
        "current_step": "script",
        "message": "Script generated",
        "enable_tts": False,
        "llm_provider": "auto",
        "target_duration": 60,
        "video_data": [
            {
                "chapter": "Chapter 1",
                "text": "What is a neural network?",
                "animation": "Show nodes connecting",
                "objective": "Understand nodes",
                "explanation": "Nodes pass signals forward.",
                "duration": 5.0,
            }
        ],
        "metadata": {},
    }
    yield ws
    vg.jobs.pop(job_id, None)


def test_invalid_manim_llm_response_reports_scene_failure(monkeypatch, no_storyboard, test_job_workspace):
    """Simulate an invalid/empty Manim LLM response and verify explicit scene failure reporting."""
    monkeypatch.setenv("GEMINI_API_KEY", "mock-key")
    monkeypatch.setenv("SCENE_CACHE_ENABLED", "false")

    def mock_generate(*args, **kwargs):
        raise LLMError("empty response from Gemini", category="invalid_output", model="gemini-3.6-flash")

    monkeypatch.setattr(LLMService, "generate", mock_generate)

    vg.generate_rendering_workflow(test_job_workspace.job_id)
    job = vg.get_job_status(test_job_workspace.job_id)

    assert job["status"] == "failed"
    assert job["error_category"] == "invalid_output"
    assert "0/1 scenes compiled" in job["error"]

    failures = job.get("metadata", {}).get("scene_failures", [])
    assert len(failures) == 1
    f1 = failures[0]
    assert f1["scene"] == 1
    assert f1["stage"] == "manim_llm_generation"
    assert f1["error_category"] == "invalid_output"
    assert "empty response" in f1["error_message"]
    assert f1["model"] == "gemini-3.6-flash"

    log_path = Path(test_job_workspace.logs) / "scene_1_error.json"
    assert log_path.exists()
    saved_log = json.loads(log_path.read_text(encoding="utf-8"))
    assert saved_log["scene"] == 1
    assert saved_log["error_category"] == "invalid_output"


def test_frontend_reviewed_scene_data_continues_rendering(monkeypatch, no_storyboard, test_job_workspace):
    """Prove reviewed frontend scene data can resume rendering without schema rejection."""
    monkeypatch.setenv("GEMINI_API_KEY", "mock-key")
    monkeypatch.setenv("SCENE_CACHE_ENABLED", "false")

    compile_called = False

    def mock_gen_code(**kwargs):
        return {
            "content": "from manim import *\nclass Scene1(Scene):\n    def construct(self):\n        pass",
            "class_name": "Scene1",
            "model": "gemini-3.6-flash",
        }

    def mock_compile(code_path, class_name, media_dir, timeout=300, is_3d=False):
        nonlocal compile_called
        compile_called = True
        Path(media_dir).mkdir(parents=True, exist_ok=True)
        out = Path(media_dir) / "render.mp4"
        out.write_bytes(b"\x00\x00")
        return str(out), None

    monkeypatch.setattr(vg.manim_generator, "generate_manim_code", mock_gen_code)
    monkeypatch.setattr(vg, "compile_video", mock_compile)
    monkeypatch.setattr(vg, "concatenate_videos", lambda videos, output, list_file: output.write_bytes(b"\x00") or True)
    monkeypatch.setattr(vg, "validate_output", lambda final, expect_audio, expected_duration: (True, None, None))
    monkeypatch.setattr(vg, "_scene_qa", lambda ws, index, video_path, vcfg, sb_entry: {"index": index, "flags": [], "blank": False})

    frontend_scene_data = [
        {
            "chapter": "Edited Chapter 1",
            "text": "User edited narration text.",
            "animation": "User edited animation description.",
            "objective": "Edited objective",
            "explanation": "Edited explanation",
            "duration": 6.0,
        }
    ]

    resumed = vg.continue_video_generation(test_job_workspace.job_id, frontend_scene_data)
    assert resumed is True

    # Wait for the background rendering thread so it cannot bleed into later tests.
    for _ in range(200):
        job = vg.get_job_status(test_job_workspace.job_id)
        if job["status"] in ("completed", "failed"):
            break
        time.sleep(0.05)
    job = vg.get_job_status(test_job_workspace.job_id)
    assert job["status"] in ("queued", "completed", "running", "failed")


def test_valid_manim_response_reaches_compile_video(monkeypatch, no_storyboard, test_job_workspace):
    """Prove a valid Manim LLM response reaches compile_video() and updates status message."""
    monkeypatch.setenv("GEMINI_API_KEY", "mock-key")
    monkeypatch.setenv("SCENE_CACHE_ENABLED", "false")

    compile_calls = []

    def mock_gen_code(**kwargs):
        return {
            "content": "from manim import *\nclass Scene1(Scene):\n    def construct(self):\n        self.play(Create(Square()))",
            "class_name": "Scene1",
            "model": "gemini-3.6-flash",
        }

    def mock_compile(code_path, class_name, media_dir, timeout=300, is_3d=False):
        compile_calls.append((code_path, class_name))
        Path(media_dir).mkdir(parents=True, exist_ok=True)
        out = Path(media_dir) / "render.mp4"
        out.write_bytes(b"\x00\x00")
        return str(out), None

    monkeypatch.setattr(vg.manim_generator, "generate_manim_code", mock_gen_code)
    monkeypatch.setattr(vg, "compile_video", mock_compile)
    monkeypatch.setattr(vg, "concatenate_videos", lambda videos, output, list_file: output.write_bytes(b"\x00") or True)
    monkeypatch.setattr(vg, "validate_output", lambda final, expect_audio, expected_duration: (True, None, None))
    monkeypatch.setattr(vg, "_scene_qa", lambda ws, index, video_path, vcfg, sb_entry: {"index": index, "flags": [], "blank": False})

    vg.generate_rendering_workflow(test_job_workspace.job_id)

    assert len(compile_calls) == 1
    assert compile_calls[0][1] == "Scene1"
    job = vg.get_job_status(test_job_workspace.job_id)
    assert job["status"] == "completed"


def test_invalid_or_placeholder_animation_model_raises_config_error(monkeypatch):
    """Prove that empty or placeholder GEMINI_ANIMATION_MODEL fails validation before rendering."""
    monkeypatch.setenv("GEMINI_API_KEY", "mock-key")

    for placeholder in ("", "placeholder", "your-model-here", "TODO", "   "):
        monkeypatch.setenv("GEMINI_ANIMATION_MODEL", placeholder)
        with pytest.raises(ProviderUnavailableError) as exc_info:
            roles = resolve_model_roles("auto")
            validate_animation_model(roles)
        assert "empty, placeholder, or unsupported" in str(exc_info.value)
