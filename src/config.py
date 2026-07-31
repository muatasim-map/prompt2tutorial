"""Central provider / model configuration for Prompt2Learn.ai.

This module is the single source of truth for:

* which LLM provider (``gemini`` / ``claude`` / ``openai``) to use, and
* which concrete model IDs handle each *role* in the pipeline
  (script generation, Manim animation code, repair, and fallback).

Model IDs are intentionally **not** hard-coded across the codebase. They are
read from environment variables with safe defaults so operators can swap in real
model names (for example the exact Gemini 3.6 Flash identifier) without code
changes.

No secrets are stored or logged here; API keys are only read from the
environment on demand.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

# --------------------------------------------------------------------------- #
# Environment helpers
# --------------------------------------------------------------------------- #


def _env(name: str, default: str) -> str:
    """Return a stripped env var value or default if unset."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip()


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(_env(name, str(default))))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    return _env(name, "true" if default else "false").lower() in ("1", "true", "yes", "on")


# --------------------------------------------------------------------------- #
# Gemini role defaults
# --------------------------------------------------------------------------- #

# NOTE: these are *placeholder* defaults matching the model names referenced in
# the existing UI. Override them via environment variables with the exact model
# IDs your Google account has access to.
DEFAULT_GEMINI_SCRIPT_MODEL = "gemini-3.5-flash-lite"
DEFAULT_GEMINI_ANIMATION_MODEL = "gemini-3.5-flash-lite"
DEFAULT_GEMINI_REPAIR_MODEL = "gemini-3.5-flash-lite"
DEFAULT_GEMINI_FALLBACK_MODEL = "gemini-3.5-flash-lite"
# Visual-direction (storyboard) pass uses the strongest model; defaults to the
# animation model unless GEMINI_STORYBOARD_MODEL overrides it.
DEFAULT_GEMINI_STORYBOARD_MODEL = ""  # empty -> falls back to animation model

DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-5"
DEFAULT_OPENAI_MODEL = "gpt-4.1"

# Maps the UI ``llm_provider`` dropdown values to a concrete Gemini *script*
# model. The animation/repair/fallback roles still come from env so a UI choice
# never silently downgrades the high-quality animation model.
UI_GEMINI_SCRIPT_MODELS = {
    "gemini-lite": DEFAULT_GEMINI_SCRIPT_MODEL,
    "gemini-3.5-flash-lite": "gemini-3.5-flash-lite",
    "gemini-flash": "gemini-3.5-flash",
    "gemini-3.6-flash": "gemini-3.6-flash",
    "gemini-pro": "gemini-3.1-pro",
}

GEMINI_UI_VALUES = set(UI_GEMINI_SCRIPT_MODELS)

# Rate pricing table ($ USD per 1M tokens / 1k characters)
MODEL_PRICING_RATES = {
    # Gemini models (per 1,000,000 tokens)
    "gemini-3.5-flash-lite": {"input_per_1m": 0.075, "output_per_1m": 0.30},
    "gemini-3.5-flash": {"input_per_1m": 0.15, "output_per_1m": 0.60},
    "gemini-3.6-flash": {"input_per_1m": 0.15, "output_per_1m": 0.60},
    "gemini-3.1-pro": {"input_per_1m": 1.25, "output_per_1m": 5.00},
    "gemini-1.5-pro": {"input_per_1m": 1.25, "output_per_1m": 5.00},
    "gemini-2.0-flash": {"input_per_1m": 0.10, "output_per_1m": 0.40},
    # OpenAI models
    "gpt-4.1": {"input_per_1m": 2.50, "output_per_1m": 10.00},
    "gpt-4o": {"input_per_1m": 2.50, "output_per_1m": 10.00},
    "gpt-4o-mini": {"input_per_1m": 0.15, "output_per_1m": 0.60},
    # Claude models
    "claude-sonnet-4-5": {"input_per_1m": 3.00, "output_per_1m": 15.00},
    "claude-3-5-sonnet": {"input_per_1m": 3.00, "output_per_1m": 15.00},
    # TTS providers (per 1,000 characters)
    "openai-tts": {"per_1k_chars": 0.015},
    "edge-tts": {"per_1k_chars": 0.000},
}


def calculate_llm_cost(model_name: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate estimated USD cost for an LLM request."""
    rates = MODEL_PRICING_RATES.get(model_name) or MODEL_PRICING_RATES.get("gemini-3.5-flash-lite")
    input_cost = (input_tokens / 1_000_000) * rates.get("input_per_1m", 0.075)
    output_cost = (output_tokens / 1_000_000) * rates.get("output_per_1m", 0.30)
    return round(input_cost + output_cost, 6)


def calculate_tts_cost(provider_name: str, char_count: int) -> float:
    """Calculate estimated USD cost for TTS audio generation."""
    rates = MODEL_PRICING_RATES.get(provider_name, {"per_1k_chars": 0.0})
    return round((char_count / 1_000) * rates.get("per_1k_chars", 0.0), 6)


@dataclass(frozen=True)
class ModelRoles:
    """Concrete model IDs for each pipeline role for a single provider.

    ``storyboard`` defaults to the empty string so existing constructors keep
    working; :meth:`for_role` falls back to the animation model when unset, i.e.
    the strongest configured model drives the visual-direction pass.
    """

    provider: str
    script: str
    animation: str
    repair: str
    fallback: Optional[str]
    storyboard: str = ""

    def for_role(self, role: str) -> str:
        """Return the model ID for ``role`` (script/animation/repair/storyboard)."""
        if role == "storyboard":
            return self.storyboard or self.animation
        return {
            "script": self.script,
            "animation": self.animation,
            "repair": self.repair,
        }.get(role, self.script)


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded retry + cooldown + concurrency policy for Gemini."""

    attempts: int = 4
    initial_delay: float = 1.0
    max_delay: float = 30.0
    exp_base: float = 2.0
    jitter: float = 1.0
    cooldown_threshold: int = 2  # consecutive 429s before a model is cooled down
    cooldown_seconds: float = 60.0
    max_concurrency: int = 2
    fallback_enabled: bool = True
    # Per-HTTP-call timeout (ms). Without this, google-genai has NO deadline of
    # its own: a hung socket blocks generate_content() until the OS's own TCP
    # retransmission ceiling gives up (observed: ~60 minutes on Windows). This
    # is the deadline that makes our own bounded retry/backoff logic actually
    # reachable — retries can't run while the call itself never returns.
    http_timeout_ms: int = 120_000


def get_retry_policy() -> RetryPolicy:
    """Build the Gemini retry/cooldown/concurrency policy from the environment."""
    return RetryPolicy(
        attempts=max(1, _env_int("GEMINI_RETRY_ATTEMPTS", 4)),
        initial_delay=_env_float("GEMINI_RETRY_INITIAL_DELAY", 1.0),
        max_delay=_env_float("GEMINI_RETRY_MAX_DELAY", 30.0),
        exp_base=_env_float("GEMINI_RETRY_EXP_BASE", 2.0),
        jitter=_env_float("GEMINI_RETRY_JITTER", 1.0),
        cooldown_threshold=max(1, _env_int("GEMINI_COOLDOWN_THRESHOLD", 2)),
        cooldown_seconds=_env_float("GEMINI_COOLDOWN_SECONDS", 60.0),
        max_concurrency=max(1, _env_int("GEMINI_MAX_CONCURRENCY", 2)),
        fallback_enabled=_env_bool("LLM_FALLBACK_ENABLED", True),
        http_timeout_ms=max(5_000, _env_int("GEMINI_HTTP_TIMEOUT_MS", 120_000)),
    )


def _gemini_roles(script_override: Optional[str] = None) -> ModelRoles:
    chosen_script = script_override or _env("GEMINI_SCRIPT_MODEL", DEFAULT_GEMINI_SCRIPT_MODEL)

    if script_override:
        # EXPLICIT UI SELECTION — the selected model is authoritative for EVERY
        # role and environment variables must NOT override it. This is what makes
        # "select Gemini 3.6 Flash" mean 3.6 Flash for script, storyboard, Manim
        # generation and repair alike. Fallback is disabled (strict by default).
        return ModelRoles(
            provider="gemini",
            script=chosen_script,
            animation=chosen_script,
            repair=chosen_script,
            fallback=None,
            storyboard=chosen_script,
        )

    # No explicit selection: env-driven defaults (fallback permitted).
    return ModelRoles(
        provider="gemini",
        script=chosen_script,
        animation=_env("GEMINI_ANIMATION_MODEL", DEFAULT_GEMINI_ANIMATION_MODEL),
        repair=_env("GEMINI_REPAIR_MODEL", DEFAULT_GEMINI_REPAIR_MODEL),
        fallback=_env("GEMINI_FALLBACK_MODEL", DEFAULT_GEMINI_FALLBACK_MODEL) or None,
        storyboard=_env("GEMINI_STORYBOARD_MODEL", DEFAULT_GEMINI_STORYBOARD_MODEL),
    )


def _single_model_roles(provider: str, model: str) -> ModelRoles:
    """Claude/OpenAI use one model for every role (no in-provider fallback)."""
    return ModelRoles(
        provider=provider,
        script=model,
        animation=model,
        repair=model,
        fallback=None,
        storyboard=model,
    )


def gemini_api_key() -> Optional[str]:
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")


def claude_api_key() -> Optional[str]:
    return os.getenv("CLAUDE_API_KEY") or os.getenv("ANTHROPIC_API_KEY")


def openai_api_key() -> Optional[str]:
    return os.getenv("OPENAI_API_KEY")


class ProviderUnavailableError(RuntimeError):
    """Raised when no API key is available for any usable provider."""


_PLACEHOLDER_MODELS = {
    "placeholder", "your-model-here", "your_model_here", "<your-model>",
    "todo", "none", "null", "invalid", "change_me", "your-api-key",
}


def validate_animation_model(roles: ModelRoles) -> None:
    """Validate that the configured animation model is non-empty and not a placeholder.

    Raises:
        ProviderUnavailableError: if the animation model is empty, placeholder, or unsupported.
    """
    model = (roles.animation or "").strip().lower()
    if not model or model in _PLACEHOLDER_MODELS or any(p in model for p in ("your-model", "placeholder")):
        raise ProviderUnavailableError(
            f"Configured animation model '{roles.animation}' is empty, placeholder, or unsupported. "
            "Please set a valid GEMINI_ANIMATION_MODEL in environment."
        )


def resolve_model_roles(provider_preference: str = "auto") -> ModelRoles:
    """Resolve the UI ``provider_preference`` to concrete per-role model IDs.

    Selection rules:

    * A specific Gemini UI value (e.g. ``gemini-3.6-flash``) overrides only the
      *script* model. The animation/repair/fallback roles always come from env
      so the high-quality animation model is never silently downgraded.
    * ``claude`` / ``openai`` route to their configured single model.
    * ``auto`` prefers Gemini, then Claude, then OpenAI, based on which API keys
      are present.

    Raises:
        ProviderUnavailableError: if the requested provider has no API key and,
            for ``auto``, no provider has a key.
    """
    pref = (provider_preference or "auto").strip().lower()

    if pref in GEMINI_UI_VALUES:
        if not gemini_api_key():
            raise ProviderUnavailableError(
                "Gemini selected but GEMINI_API_KEY/GOOGLE_API_KEY is not configured."
            )
        roles = _gemini_roles(script_override=UI_GEMINI_SCRIPT_MODELS[pref])
    elif pref == "claude":
        if not claude_api_key():
            raise ProviderUnavailableError(
                "Claude selected but CLAUDE_API_KEY is not configured."
            )
        roles = _single_model_roles("claude", _env("CLAUDE_MODEL", DEFAULT_CLAUDE_MODEL))
    elif pref == "openai":
        if not openai_api_key():
            raise ProviderUnavailableError(
                "OpenAI selected but OPENAI_API_KEY is not configured."
            )
        roles = _single_model_roles("openai", _env("OPENAI_MODEL", DEFAULT_OPENAI_MODEL))
    elif gemini_api_key():
        roles = _gemini_roles()
    elif claude_api_key():
        roles = _single_model_roles("claude", _env("CLAUDE_MODEL", DEFAULT_CLAUDE_MODEL))
    elif openai_api_key():
        roles = _single_model_roles("openai", _env("OPENAI_MODEL", DEFAULT_OPENAI_MODEL))
    else:
        raise ProviderUnavailableError(
            "No API key found. Configure GEMINI_API_KEY, CLAUDE_API_KEY, or OPENAI_API_KEY."
        )

    validate_animation_model(roles)
    return roles


# --------------------------------------------------------------------------- #
# Canonical per-job model selection (strict / no-fallback enforcement)
# --------------------------------------------------------------------------- #


def is_explicit_selection(provider_preference: str) -> bool:
    """True when the user explicitly picked a concrete model in the UI."""
    pref = (provider_preference or "auto").strip().lower()
    return pref in GEMINI_UI_VALUES or pref in ("claude", "openai")


@dataclass(frozen=True)
class ModelSelection:
    """The authoritative, per-job record of what the user selected.

    Created once at job creation and reused for *every* LLM stage (script,
    storyboard, Manim generation, Manim repair) including after the
    scene-review ``/api/generate/continue`` resume, so a job can never drift
    onto a different model or provider midway.
    """

    ui_value: str
    provider: str
    roles: ModelRoles
    strict: bool

    @property
    def model(self) -> str:
        """The single selected model ID (identical across roles in strict mode)."""
        return self.roles.script

    def model_for(self, role: str) -> str:
        return self.roles.for_role(role)

    def audit(self) -> dict:
        """Safe, loggable routing record (contains no secrets)."""
        return {
            "ui_selection": self.ui_value,
            "provider": self.provider,
            "strict_model": self.strict,
            "fallback_enabled": (not self.strict) and bool(self.roles.fallback),
            "fallback_model": None if self.strict else self.roles.fallback,
            "models": {
                "script": self.roles.script,
                "storyboard": self.roles.for_role("storyboard"),
                "animation": self.roles.animation,
                "repair": self.roles.repair,
            },
        }


def resolve_model_selection(provider_preference: str = "auto") -> ModelSelection:
    """Resolve the UI preference into the canonical per-job selection record.

    An explicit UI model pick implies **strict mode**: retry only that exact
    model, never switch model or provider. ``LLM_ALLOW_FALLBACK=true`` is the
    only way to opt back into fallback, and even then it is recorded in the
    job audit.
    """
    pref = (provider_preference or "auto").strip().lower()
    roles = resolve_model_roles(pref)
    explicit = is_explicit_selection(pref)
    # Strict by default for an explicit pick; opt-in override only.
    strict = explicit and not _env_bool("LLM_ALLOW_FALLBACK", False)
    return ModelSelection(ui_value=pref, provider=roles.provider, roles=roles, strict=strict)



# --------------------------------------------------------------------------- #
# Visual-direction / visual-QA configuration
# --------------------------------------------------------------------------- #

DEFAULT_GLOBAL_VISUAL_STYLE = (
    "One coherent visual language across the whole video: a restrained palette "
    "(2-4 accent colors on a dark background), clean high-contrast sans-serif "
    "typography, generous safe margins, and consistent, calm animation pacing. "
    "Scenes share this language but must NOT look identical — vary composition, "
    "metaphor, and motion from scene to scene."
)


@dataclass(frozen=True)
class VisualConfig:
    """Toggles + thresholds for the visual-direction and visual-QA systems."""

    storyboard_enabled: bool = True
    visual_repair_attempts: int = 1
    # Advisory QA remains visible in reports but does not automatically spend
    # another LLM call and full render unless explicitly opted in.
    auto_repair_advisory_qa: bool = False
    contact_sheet_enabled: bool = True
    visual_qa_enabled: bool = True
    global_style: str = DEFAULT_GLOBAL_VISUAL_STYLE
    manim_quality: str = "low"  # low -> -ql/480p15, high -> -qh/1080p60
    # Frames sampled per scene for QA. This is the RESOLUTION of every temporal
    # measurement downstream: static-run length is counted in steps of
    # duration/frames_per_scene, so at 3 frames a "trailing static run" could
    # only ever be 0, 1/3 or 2/3 of the clip — measured across 13 benchmark runs
    # it returned exactly 0.67 on 12 of 16 scenes, which read as a precise
    # finding but was quantisation. 12 gives sub-second precision on a ~9s
    # scene, and also makes the all-frames-blank test meaningfully harder to
    # pass by luck (a fully blank scene shipped undetected at 3).
    # Cost is a few extra FFmpeg stills per scene against ~40s of compile.
    frames_per_scene: int = 12
    # QA heuristic thresholds (frame brightness is 0-255).
    # NOTE: Manim scenes are dark by default, so "blank" is judged primarily by
    # how little *content* a frame has, not by absolute brightness.
    blank_content_ratio: float = 0.0025  # < this fraction of content -> blank
    white_min_brightness: float = 247.0
    min_stddev: float = 1.5
    edge_content_ratio: float = 0.020
    near_identical_mae: float = 4.0
    # Retained for the first-vs-last "no meaningful change" test, which compares
    # two deliberately distant frames where a whole-canvas mean still works.
    min_scene_change_mae: float = 2.0
    # Motion floor for frozen-passage detection, as a fraction of VISIBLE
    # CONTENT that changed between consecutive samples (see
    # visual_qa.motion_between). Calibrated on 77 benchmark scenes: the old
    # whole-canvas MAE test flagged 84% of them as containing a long frozen
    # run, this flags 10%, and spot-checks confirm the 74-point difference was
    # false positives on sparse-but-animating frames.
    min_scene_motion: float = 0.05
    # Longest frozen tail tolerated before a scene is regenerated with more
    # visual beats instead of shipping a still frame.
    max_tail_pad_seconds: float = 0.75
    # A frozen stretch longer than this anywhere in a scene is flagged.
    max_static_run_seconds: float = 3.0
    # Scenes compiled concurrently. Measured over 13 benchmark runs, Manim
    # compilation is 79% of total wall clock (251 of 316 minutes) and is pure
    # CPU work that is independent per scene — each scene already renders into
    # its own media_dir precisely so concurrent runs cannot collide (see
    # media_paths.scene_media_dir, verified at 4 concurrent renders).
    #
    # Two workers are the safe default for laptop CPUs. A measured 12-thread
    # mobile i7 run with four workers pushed ordinary 10-second scenes past the
    # timeout together. Raise RENDER_WORKERS only after host benchmarking.
    render_workers: int = 2


def get_visual_config() -> VisualConfig:
    """Build the visual-direction/QA configuration from the environment."""
    return VisualConfig(
        storyboard_enabled=_env_bool("STORYBOARD_ENABLED", True),
        visual_repair_attempts=max(0, _env_int("VISUAL_REPAIR_ATTEMPTS", 1)),
        auto_repair_advisory_qa=_env_bool("AUTO_REPAIR_ADVISORY_QA", False),
        contact_sheet_enabled=_env_bool("CONTACT_SHEET_ENABLED", True),
        visual_qa_enabled=_env_bool("VISUAL_QA_ENABLED", True),
        global_style=_env("GLOBAL_VISUAL_STYLE", DEFAULT_GLOBAL_VISUAL_STYLE),
        manim_quality=_env("MANIM_QUALITY", "low").lower(),
        frames_per_scene=max(1, _env_int("QA_FRAMES_PER_SCENE", 12)),
        render_workers=max(1, min(8, _env_int("RENDER_WORKERS", 2))),
        blank_content_ratio=_env_float("QA_BLANK_CONTENT_RATIO", 0.0025),
        white_min_brightness=_env_float("QA_WHITE_MIN_BRIGHTNESS", 247.0),
        min_stddev=_env_float("QA_MIN_STDDEV", 1.5),
        edge_content_ratio=_env_float("QA_EDGE_CONTENT_RATIO", 0.020),
        near_identical_mae=_env_float("QA_NEAR_IDENTICAL_MAE", 4.0),
        min_scene_motion=_env_float("QA_MIN_SCENE_MOTION", 0.05),
        min_scene_change_mae=_env_float("QA_MIN_SCENE_CHANGE_MAE", 2.0),
        max_tail_pad_seconds=_env_float("QA_MAX_TAIL_PAD_SECONDS", 0.75),
        max_static_run_seconds=_env_float("QA_MAX_STATIC_RUN_SECONDS", 3.0),
    )
