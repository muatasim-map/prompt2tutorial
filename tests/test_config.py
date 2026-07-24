"""Tests for provider/model routing (no silent downgrade)."""

import pytest

import config
from config import ProviderUnavailableError, resolve_model_roles


@pytest.fixture
def gemini_only(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("CLAUDE_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # Clear model overrides so defaults apply.
    for var in ("GEMINI_SCRIPT_MODEL", "GEMINI_ANIMATION_MODEL",
                "GEMINI_REPAIR_MODEL", "GEMINI_FALLBACK_MODEL"):
        monkeypatch.delenv(var, raising=False)


def test_ui_gemini_selection_sets_script_only(gemini_only):
    roles = resolve_model_roles("gemini-3.5-flash-lite")
    assert roles.provider == "gemini"
    assert roles.script == "gemini-3.5-flash-lite"
    assert roles.animation == config.DEFAULT_GEMINI_ANIMATION_MODEL


def test_animation_model_env_override_applies_without_explicit_selection(gemini_only, monkeypatch):
    """With no explicit UI pick, env vars still drive per-role models."""
    monkeypatch.setenv("GEMINI_ANIMATION_MODEL", "gemini-custom-anim")
    roles = resolve_model_roles("auto")
    assert roles.animation == "gemini-custom-anim"


def test_env_cannot_override_explicit_ui_selection(gemini_only, monkeypatch):
    """REGRESSION: an explicit UI model pick must win over environment defaults.

    Previously a stray GEMINI_ANIMATION_MODEL silently hijacked the Manim stage,
    so selecting Gemini 3.6 Flash still rendered with a different model.
    """
    monkeypatch.setenv("GEMINI_ANIMATION_MODEL", "gemini-custom-anim")
    monkeypatch.setenv("GEMINI_REPAIR_MODEL", "gemini-other-repair")
    monkeypatch.setenv("GEMINI_STORYBOARD_MODEL", "gemini-other-sb")
    monkeypatch.setenv("GEMINI_FALLBACK_MODEL", "gemini-2.5-flash")

    roles = resolve_model_roles("gemini-3.6-flash")
    assert roles.script == "gemini-3.6-flash"
    assert roles.animation == "gemini-3.6-flash"
    assert roles.repair == "gemini-3.6-flash"
    assert roles.for_role("storyboard") == "gemini-3.6-flash"
    # Explicit selection disables in-provider fallback (strict by default).
    assert roles.fallback is None


def test_auto_prefers_gemini(gemini_only):
    roles = resolve_model_roles("auto")
    assert roles.provider == "gemini"


def test_auto_falls_to_claude_when_no_gemini(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_API_KEY", "c")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    roles = resolve_model_roles("auto")
    assert roles.provider == "claude"
    # Single-model providers use one model for every role.
    assert roles.script == roles.animation == roles.repair
    assert roles.fallback is None


def test_claude_selected_without_key_raises(monkeypatch):
    monkeypatch.delenv("CLAUDE_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ProviderUnavailableError):
        resolve_model_roles("claude")


def test_no_keys_raises(monkeypatch):
    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "CLAUDE_API_KEY",
                "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ProviderUnavailableError):
        resolve_model_roles("auto")


def test_retry_policy_from_env(monkeypatch):
    monkeypatch.setenv("GEMINI_RETRY_ATTEMPTS", "7")
    monkeypatch.setenv("GEMINI_COOLDOWN_SECONDS", "120")
    monkeypatch.setenv("GEMINI_MAX_CONCURRENCY", "5")
    policy = config.get_retry_policy()
    assert policy.attempts == 7
    assert policy.cooldown_seconds == 120
    assert policy.max_concurrency == 5
