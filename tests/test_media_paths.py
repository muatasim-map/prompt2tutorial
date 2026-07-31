"""Tests for per-job asset isolation and cache-key versioning."""

import media_paths
from media_paths import JobWorkspace
from video_generator import compute_scene_cache_key


def test_workspaces_are_isolated(tmp_path):
    a = JobWorkspace("job-a", base_dir=tmp_path)
    b = JobWorkspace("job-b", base_dir=tmp_path)
    assert a.root != b.root
    assert a.final_video() != b.final_video()
    assert a.silent_video() != b.silent_video()
    assert a.concat_list() != b.concat_list()


def test_audio_fragments_unique_per_job_and_scene(tmp_path):
    a = JobWorkspace("job-a", base_dir=tmp_path)
    b = JobWorkspace("job-b", base_dir=tmp_path)
    # unique per scene within a job
    assert a.audio_fragment(1) != a.audio_fragment(2)
    # unique across jobs for the same scene index
    assert a.audio_fragment(1) != b.audio_fragment(1)


def test_scene_code_and_video_unique(tmp_path):
    a = JobWorkspace("job-a", base_dir=tmp_path)
    assert a.scene_code(1) != a.scene_code(2)
    assert a.scene_video(1) != a.scene_video(2)


def test_create_makes_all_dirs(tmp_path):
    ws = JobWorkspace("job-x", base_dir=tmp_path).create()
    for d in (ws.audio, ws.scenes, ws.code, ws.video, ws.logs, ws.final):
        assert d.is_dir()


def test_cache_key_includes_version(monkeypatch):
    args = dict(text="t", animation="a", index=1, previous_context=None,
                provider="gemini", model="m", audio_duration=5.0,
                chapter="c", objective="o", explanation="e")
    key_v = compute_scene_cache_key(**args)

    # Simulate a cache-version bump -> different key for identical inputs.
    monkeypatch.setattr("video_generator.CACHE_VERSION", "v-different")
    key_v2 = compute_scene_cache_key(**args)
    assert key_v != key_v2


def test_cache_key_changes_with_model():
    base = dict(text="t", animation="a", index=1, previous_context=None,
                provider="gemini", audio_duration=5.0, chapter="c",
                objective="o", explanation="e")
    k1 = compute_scene_cache_key(model="model-1", **base)
    k2 = compute_scene_cache_key(model="model-2", **base)
    assert k1 != k2


def test_scene_cache_isolated_by_explanation_mode_and_curriculum():
    common = dict(
        text="Find a root",
        animation="Show a tangent",
        index=1,
        previous_context=None,
        provider="gemini",
        model="test-model",
        audio_duration=8,
    )

    general_key = compute_scene_cache_key(**common)
    exam_key = compute_scene_cache_key(
        **common,
        explanation_mode="exam_technique",
        curriculum_profile="aqa_a_level_mathematics",
    )

    assert general_key != exam_key


def test_scene_cache_isolated_by_render_quality(monkeypatch):
    common = dict(
        text="Explain a tangent",
        animation="Draw a changing tangent",
        index=1,
        previous_context=None,
        provider="gemini",
        model="test-model",
        audio_duration=8,
    )

    monkeypatch.setenv("MANIM_QUALITY", "low")
    low_key = compute_scene_cache_key(**common)
    monkeypatch.setenv("MANIM_QUALITY", "high")
    high_key = compute_scene_cache_key(**common)

    assert low_key != high_key


def test_caches_live_outside_job_dirs():
    # Reusable caches must not sit inside the per-job tree.
    assert media_paths.JOBS_DIR not in media_paths.SCENE_CACHE_DIR.parents
    assert media_paths.JOBS_DIR not in media_paths.TTS_CACHE_DIR.parents
