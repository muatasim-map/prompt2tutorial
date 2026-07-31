"""Fast, side-effect-free readiness checks for the local video pipeline."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import sys


def _configured_llm_providers() -> list[str]:
    providers = []
    if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
        providers.append("gemini")
    if os.getenv("CLAUDE_API_KEY") or os.getenv("ANTHROPIC_API_KEY"):
        providers.append("claude")
    if os.getenv("OPENAI_API_KEY"):
        providers.append("openai")
    return providers


def _module_check(name: str) -> dict:
    found = importlib.util.find_spec(name) is not None
    return {
        "ok": found,
        "detail": "installed" if found else f"Python package '{name}' is not installed",
    }


def _executable_check(name: str) -> dict:
    path = shutil.which(name)
    return {
        "ok": bool(path),
        "detail": str(Path(path)) if path else f"'{name}' is not available on PATH",
    }


def check_runtime_readiness() -> dict:
    """Return machine-readable diagnostics without contacting external services."""
    providers = _configured_llm_providers()
    checks = {
        "python": {
            "ok": sys.version_info >= (3, 9),
            "detail": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        },
        "flask": _module_check("flask"),
        "manim": _module_check("manim"),
        "ffmpeg": _executable_check("ffmpeg"),
        "ffprobe": _executable_check("ffprobe"),
        "llm_api_key": {
            "ok": bool(providers),
            "detail": (
                f"configured providers: {', '.join(providers)}"
                if providers
                else "configure GEMINI_API_KEY, CLAUDE_API_KEY, or OPENAI_API_KEY"
            ),
        },
    }
    failed = [name for name, result in checks.items() if not result["ok"]]
    return {
        "ready": not failed,
        "checks": checks,
        "failed_checks": failed,
        "configured_llm_providers": providers,
        "python_executable": sys.executable,
    }
