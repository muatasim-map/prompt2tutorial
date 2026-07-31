"""API contracts for readiness failures and recovery."""

import main


def test_generate_fails_early_when_runtime_is_not_ready(monkeypatch):
    monkeypatch.setattr(
        main,
        "check_runtime_readiness",
        lambda: {"ready": False, "failed_checks": ["ffmpeg"], "checks": {}},
    )

    response = main.app.test_client().post("/api/generate", json={"topic": "Vectors"})

    assert response.status_code == 503
    assert response.json["error_category"] == "environment_not_ready"
    assert response.json["details"]["failed_checks"] == ["ffmpeg"]


def test_health_exposes_dependency_readiness(monkeypatch):
    readiness = {
        "ready": False,
        "failed_checks": ["ffprobe"],
        "checks": {"ffprobe": {"ok": False}},
    }
    monkeypatch.setattr(main, "check_runtime_readiness", lambda: readiness)

    response = main.app.test_client().get("/api/health")

    assert response.status_code == 200
    assert response.json["status"] == "degraded"
    assert response.json["readiness"] == readiness


def test_retry_endpoint_starts_recovery(monkeypatch):
    monkeypatch.setattr(
        "video_generator.retry_failed_generation",
        lambda job_id: job_id == "failed-job",
    )

    response = main.app.test_client().post(
        "/api/generate/retry",
        json={"job_id": "failed-job"},
    )

    assert response.status_code == 202
    assert response.json["status"] == "queued"


def test_generate_normalizes_and_forwards_learning_profile(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        main,
        "check_runtime_readiness",
        lambda: {"ready": True, "failed_checks": [], "checks": {}},
    )
    monkeypatch.setattr("app_build.build_info", lambda: {"stale": False})

    def fake_start(*args):
        captured["args"] = args
        return "mode-job"

    monkeypatch.setattr(main, "start_video_generation", fake_start)

    response = main.app.test_client().post(
        "/api/generate",
        json={
            "topic": "Newton-Raphson method",
            "explanation_mode": "exam",
            "curriculum_profile": "AQA 7357",
        },
    )

    assert response.status_code == 202
    assert response.json["explanation_mode"] == "exam_technique"
    assert response.json["curriculum_profile"] == "aqa_a_level_mathematics"
    assert captured["args"][-2:] == (
        "exam_technique",
        "aqa_a_level_mathematics",
    )


def test_generate_rejects_unknown_explanation_mode_before_start(monkeypatch):
    monkeypatch.setattr(
        main,
        "check_runtime_readiness",
        lambda: {"ready": True, "failed_checks": [], "checks": {}},
    )
    monkeypatch.setattr("app_build.build_info", lambda: {"stale": False})

    response = main.app.test_client().post(
        "/api/generate",
        json={"topic": "Vectors", "explanation_mode": "cinematic magic"},
    )

    assert response.status_code == 400
    assert response.json["error_category"] == "invalid_request"
