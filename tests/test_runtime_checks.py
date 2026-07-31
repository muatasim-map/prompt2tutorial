"""Readiness diagnostics for local generation dependencies."""

import runtime_checks


def test_readiness_reports_missing_required_tools(monkeypatch):
    monkeypatch.setattr(runtime_checks.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        runtime_checks.importlib.util,
        "find_spec",
        lambda name: object() if name == "flask" else None,
    )
    monkeypatch.setattr(runtime_checks, "_configured_llm_providers", lambda: ["gemini"])

    report = runtime_checks.check_runtime_readiness()

    assert report["ready"] is False
    assert report["checks"]["ffmpeg"]["ok"] is False
    assert report["checks"]["ffprobe"]["ok"] is False
    assert report["checks"]["manim"]["ok"] is False
    assert report["checks"]["llm_api_key"]["ok"] is True


def test_readiness_is_true_when_required_dependencies_exist(monkeypatch):
    monkeypatch.setattr(runtime_checks.shutil, "which", lambda name: f"C:/{name}.exe")
    monkeypatch.setattr(runtime_checks.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(runtime_checks, "_configured_llm_providers", lambda: ["openai"])

    report = runtime_checks.check_runtime_readiness()

    assert report["ready"] is True
    assert report["configured_llm_providers"] == ["openai"]
    assert all(check["ok"] for check in report["checks"].values())
