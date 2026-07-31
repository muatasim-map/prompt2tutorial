"""Durable manifests and safe retry behavior for generation jobs."""

import json

import media_paths
import video_generator as vg
from media_paths import JobWorkspace


def test_status_updates_write_a_durable_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(media_paths, "JOBS_DIR", tmp_path / "jobs")
    vg.jobs["manifest-job"] = {
        "job_id": "manifest-job",
        "topic": "Vectors",
        "status": "queued",
        "progress": 0,
        "metadata": {},
    }

    vg.update_job_status(
        "manifest-job",
        status="running",
        progress=15,
        current_step="script",
        meta={"cache_stats": {"script_hits": 1}},
    )

    manifest = json.loads(
        JobWorkspace("manifest-job").manifest_file().read_text(encoding="utf-8")
    )
    assert manifest["schema_version"] == 1
    assert manifest["job"]["status"] == "running"
    assert manifest["job"]["progress"] == 15
    assert manifest["job"]["metadata"]["cache_stats"]["script_hits"] == 1
    assert "_model_selection" not in manifest["job"]


def test_retry_failed_generation_reuses_successful_caches(monkeypatch):
    started = []
    vg.jobs["retry-job"] = {
        "job_id": "retry-job",
        "status": "failed",
        "progress": 70,
        "current_step": "code",
        "video_data": [{"text": "A", "animation": "Show A"}],
        "bypass_cache": True,
        "bypass_scene_cache": True,
        "metadata": {"scene_failures": [{"scene": 1}]},
    }
    monkeypatch.setattr(vg, "_start_render_thread", lambda job_id: started.append(job_id))

    assert vg.retry_failed_generation("retry-job") is True
    assert started == ["retry-job"]
    assert vg.jobs["retry-job"]["status"] == "queued"
    assert vg.jobs["retry-job"]["bypass_cache"] is False
    assert vg.jobs["retry-job"]["bypass_scene_cache"] is False
    assert vg.jobs["retry-job"]["retry_count"] == 1


def test_retry_rejects_non_failed_job(monkeypatch):
    vg.jobs["running-job"] = {
        "job_id": "running-job",
        "status": "running",
        "video_data": [{"text": "A", "animation": "Show A"}],
    }
    monkeypatch.setattr(vg, "_start_render_thread", lambda job_id: None)

    assert vg.retry_failed_generation("running-job") is False


def test_retry_restores_failed_job_from_manifest_after_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(media_paths, "JOBS_DIR", tmp_path / "jobs")
    workspace = JobWorkspace("restored-job").create()
    workspace.manifest_file().write_text(json.dumps({
        "schema_version": 1,
        "job": {
            "job_id": "restored-job",
            "status": "failed",
            "progress": 75,
            "current_step": "code",
            "video_data": [{"text": "A", "animation": "Show A"}],
            "bypass_cache": True,
            "bypass_scene_cache": True,
            "metadata": {"scene_failures": [{"scene": 1}]},
        },
    }), encoding="utf-8")
    vg.jobs.pop("restored-job", None)
    started = []
    monkeypatch.setattr(vg, "_start_render_thread", lambda job_id: started.append(job_id))

    assert vg.retry_failed_generation("restored-job") is True
    assert started == ["restored-job"]
    assert vg.jobs["restored-job"]["bypass_cache"] is False
    assert vg.jobs["restored-job"]["bypass_scene_cache"] is False
