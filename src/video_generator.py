"""Job orchestration for the Prompt2Learn.ai pipeline.

Coordinates: provider/model routing (:mod:`config`), reliable LLM calls
(:mod:`llm_service`), Pydantic-validated scripts (:mod:`schemas`), per-job asset
isolation (:mod:`media_paths`), and FFmpeg-based assembly + FFprobe validation
(:mod:`ffmpeg_utils`). Jobs run in background threads; state lives in the
in-process ``jobs`` dict (swap for Redis/DB in production).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import anthropic
import openai
from dotenv import load_dotenv

import animations
import domain_guidance
import manim_generator
import storyboard as storyboard_mod
import visual_qa
from app_build import BUILD_ID as APP_BUILD_ID
from concat_video import (
    compile_video,
    concatenate_videos,
    merge_video_and_audio,
    sanitize_filename,
)
from config import (
    ProviderUnavailableError,
    calculate_llm_cost,
    calculate_tts_cost,
    claude_api_key,
    get_retry_policy,
    get_visual_config,
    openai_api_key,
    resolve_model_roles,
    resolve_model_selection,
    validate_animation_model,
)
from ffmpeg_utils import FFmpegError, validate_output
from llm_service import LLMError, LLMService
from media_paths import (
    CACHE_VERSION,
    PROJECT_ROOT,
    SCENE_CACHE_DIR,
    SCRIPT_CACHE_DIR,
    JobWorkspace,
    ensure_base_dirs,
)
from schemas import ScriptValidationError, parse_script, validate_target_duration
from tts_generator import generate_complete_audio
from visual_ledger import LedgerEntry, VisualLedger

load_dotenv()

# Global job storage (in production, use Redis or a database).
jobs: dict = {}


# --------------------------------------------------------------------------- #
# Provider clients
# --------------------------------------------------------------------------- #


def build_provider_client(provider: str) -> Any:
    """Build a raw SDK client for Claude/OpenAI; Gemini is handled in-service."""
    if provider == "claude":
        return anthropic.Anthropic(api_key=claude_api_key())
    if provider == "openai":
        return openai.OpenAI(api_key=openai_api_key())
    return None  # gemini: LLMService owns its own client


# --------------------------------------------------------------------------- #
# Job status
# --------------------------------------------------------------------------- #


def update_job_status(
    job_id: str,
    status: Optional[str] = None,
    progress: Optional[float] = None,
    current_step: Optional[str] = None,
    message: Optional[str] = None,
    error: Optional[str] = None,
    video_url: Optional[str] = None,
    meta: Optional[dict] = None,
    error_category: Optional[str] = None,
) -> None:
    """Update job status. ``meta`` merges into safe, frontend-visible metadata."""
    if job_id not in jobs:
        jobs[job_id] = {}
    job = jobs[job_id]

    if status:
        job["status"] = status
    if progress is not None:
        job["progress"] = progress
    if current_step:
        job["current_step"] = current_step
    if message:
        job["message"] = message
    if error:
        job["error"] = error
    if error_category:
        job["error_category"] = error_category
    if video_url:
        job["video_url"] = video_url
    if meta:
        job.setdefault("metadata", {}).update(meta)

    job["updated_at"] = datetime.now().isoformat()


def _job_selection(job_id: str, llm_provider: str):
    """Resolve (once) and persist the canonical model selection for a job.

    Created at the script stage and reused verbatim by the rendering workflow
    after ``/api/generate/continue`` so a job can never drift onto a different
    model or provider partway through.
    """
    job = jobs.setdefault(job_id, {})
    selection = job.get("_model_selection")
    if selection is None:
        selection = resolve_model_selection(llm_provider)
        job["_model_selection"] = selection
        audit = selection.audit()
        audit["app_build"] = APP_BUILD_ID
        job["model_audit"] = audit
        update_job_status(job_id, meta={
            "app_build": APP_BUILD_ID,
            "provider": selection.provider,
            "selected_model": selection.model,
            "strict_model": selection.strict,
            "script_model": selection.model_for("script"),
            "storyboard_model": selection.model_for("storyboard"),
            "animation_model": selection.model_for("animation"),
            "repair_model": selection.model_for("repair"),
            "fallback_model": audit["fallback_model"],
            "fallback_enabled": audit["fallback_enabled"],
        })
        _write_job_log(job_id, "model_routing", audit)
    return selection


def _write_job_log(job_id: str, event: str, payload: dict) -> None:
    """Append a safe, structured line to the per-job log (never raises)."""
    try:
        ws = JobWorkspace(job_id)
        ws.logs.mkdir(parents=True, exist_ok=True)
        record = {"ts": datetime.now().isoformat(), "event": event, **payload}
        with open(ws.log_file(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


# Human-readable stage label per LLM role, for the cost-audit breakdown table.
_ROLE_LABELS = {
    "script": "Script generation",
    "storyboard": "Storyboard / visual direction",
    "animation": "Manim code generation",
    "repair": "Compile-repair (REPL)",
}


def _aggregate_cost_report(job_id: str, scene_count: int) -> dict:
    """Read this job's own log and compute REAL cost/usage — never estimated.

    Every number here comes from a provider's own response (usage_metadata)
    or an actually-synthesized narration character count, both logged as they
    happen (see _make_llm_status and the tts_usage log point). A job with no
    successful LLM calls yet (e.g. failed immediately) gets an honest
    all-zero report rather than a plausible-looking fabricated one.
    """
    report = {
        "total_cost_usd": 0.0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_tts_characters": 0,
        "llm_call_count": 0,
        "scene_count": scene_count,
        "cost_breakdown": [],
        "data_source": "measured",
    }
    try:
        log_path = JobWorkspace(job_id).log_file()
        if not log_path.exists():
            report["data_source"] = "no_log"
            return report

        by_role: dict = {}
        tts_chars = 0
        tts_provider_seen = None
        with open(log_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event = rec.get("event")
                if event == "llm_usage":
                    role = rec.get("role") or "other"
                    model = rec.get("model") or "unknown"
                    in_tok = int(rec.get("input_tokens") or 0)
                    out_tok = int(rec.get("output_tokens") or 0)
                    bucket = by_role.setdefault(role, {
                        "model": model, "input_tokens": 0, "output_tokens": 0, "calls": 0,
                    })
                    bucket["input_tokens"] += in_tok
                    bucket["output_tokens"] += out_tok
                    bucket["calls"] += 1
                    bucket["model"] = model  # last-seen model for this role (post-fallback, if any)
                    report["total_input_tokens"] += in_tok
                    report["total_output_tokens"] += out_tok
                    report["llm_call_count"] += 1
                elif event == "tts_usage":
                    tts_chars += int(rec.get("characters") or 0)
                    tts_provider_seen = rec.get("provider") or tts_provider_seen

        for role, bucket in by_role.items():
            cost = calculate_llm_cost(bucket["model"], bucket["input_tokens"], bucket["output_tokens"])
            report["total_cost_usd"] += cost
            report["cost_breakdown"].append({
                "stage": _ROLE_LABELS.get(role, role.title()),
                "engine": bucket["model"],
                "input_tokens": bucket["input_tokens"],
                "output_tokens": bucket["output_tokens"],
                "calls": bucket["calls"],
                "cost_usd": round(cost, 6),
            })

        if tts_chars > 0:
            tts_cost = calculate_tts_cost(tts_provider_seen or "edge-tts", tts_chars)
            report["total_cost_usd"] += tts_cost
            report["total_tts_characters"] = tts_chars
            report["cost_breakdown"].append({
                "stage": "Narration TTS",
                "engine": tts_provider_seen or "edge-tts",
                "characters": tts_chars,
                "cost_usd": round(tts_cost, 6),
            })

        report["total_cost_usd"] = round(report["total_cost_usd"], 6)
        # Stable, readable order: generation pipeline stages, then TTS last.
        order = {"Script generation": 0, "Storyboard / visual direction": 1,
                 "Manim code generation": 2, "Compile-repair (REPL)": 3, "Narration TTS": 4}
        report["cost_breakdown"].sort(key=lambda r: order.get(r["stage"], 99))
        if not report["cost_breakdown"]:
            report["data_source"] = "no_usage_recorded"
        return report
    except Exception:
        # Analytics must never break a render. An honest empty report beats a crash.
        report["data_source"] = "error"
        return report


def _make_llm_status(job_id: str):
    """Return a status sink that records safe LLM reliability metadata.

    Also logs a "llm_usage" job-log event for every SUCCESSFUL call (real
    token counts from the provider's own response — see LLMService.generate).
    This is the source of truth the completion-time cost audit reads back;
    written per-call rather than accumulated in-memory because a job spans TWO
    separate LLMService instances (script stage, then the render/continue
    stage) that share no process state.
    """

    def sink(event: dict) -> None:
        if event.get("usage"):
            _write_job_log(job_id, "llm_usage", {
                "provider": event.get("provider"),
                "model": event.get("model"),
                "role": event.get("role"),
                "input_tokens": event.get("input_tokens", 0),
                "output_tokens": event.get("output_tokens", 0),
            })
            return

        meta = {}
        for key in ("provider", "model", "role", "error_category"):
            if key in event:
                meta[key] = event[key]
        if event.get("fallback"):
            meta["fallback_model"] = event.get("model")
            meta["fallback_reason"] = event.get("fallback_reason")
        if event.get("cooldown"):
            meta["cooldown"] = True
            meta["cooldown_seconds"] = event.get("cooldown_seconds")
        note = event.get("note")
        # Only surface notable events to the log message; routine "using X" is
        # recorded to metadata but not spammed to the progress log.
        surface = bool(event.get("fallback") or event.get("cooldown") or event.get("error_category"))
        update_job_status(
            job_id,
            meta=meta or None,
            message=(f"[model] {note}" if (note and surface) else None),
        )

    return sink


# --------------------------------------------------------------------------- #
# Code post-processing
# --------------------------------------------------------------------------- #


def append_duration_sync(
    code_content: str,
    audio_duration: Optional[float],
    timing_path: Optional[Path] = None,
) -> str:
    """Align the scene to the narration length, and RECORD how much was padded.

    Exact audio/video sync is preserved (the remainder is still waited out), but
    the pre-pad animation length is written to ``timing_path`` so the pipeline can
    detect a frozen tail and regenerate the scene with more visual beats instead
    of silently shipping a still frame. Padding is the fallback, never the plan.
    """
    if not audio_duration:
        return code_content

    lines = code_content.splitlines()
    construct_line_idx = -1
    indentation = "        "
    for idx, line in enumerate(lines):
        if "def construct" in line:
            construct_line_idx = idx
            for next_idx in range(idx + 1, len(lines)):
                if lines[next_idx].strip():
                    leading = len(lines[next_idx]) - len(lines[next_idx].lstrip())
                    indentation = " " * leading
                    break
            break
    if construct_line_idx == -1:
        return code_content

    timing_literal = repr(str(timing_path).replace("\\", "/")) if timing_path else "None"
    sync_code = f"""
{indentation}# Programmatic duration sync to match audio exactly (records pad length)
{indentation}try:
{indentation}    _curr_time = self.renderer.time if (hasattr(self, 'renderer') and self.renderer) else self.time
{indentation}    _target = {audio_duration:.4f}
{indentation}    _pad = max(0.0, _target - _curr_time)
{indentation}    _timing_path = {timing_literal}
{indentation}    if _timing_path:
{indentation}        try:
{indentation}            import json as _json
{indentation}            with open(_timing_path, 'w', encoding='utf-8') as _tf:
{indentation}                _json.dump({{'animation_seconds': round(float(_curr_time), 3),
{indentation}                            'target_seconds': round(float(_target), 3),
{indentation}                            'pad_seconds': round(float(_pad), 3)}}, _tf)
{indentation}        except Exception:
{indentation}            pass
{indentation}    if _pad > 0:
{indentation}        self.wait(_pad)
{indentation}except Exception:
{indentation}    pass
"""
    insert_idx = len(lines)
    for idx in range(construct_line_idx + 1, len(lines)):
        if lines[idx].startswith("    def ") or lines[idx].startswith("def "):
            insert_idx = idx
            break
    lines.insert(insert_idx, sync_code)
    return "\n".join(lines)


def compute_scene_cache_key(
    text, animation, index, previous_context, provider, model, audio_duration,
    chapter=None, objective=None, explanation=None, visual_key=None,
) -> str:
    """Deterministic SHA-256 cache key over all scene inputs (+ cache version).

    ``visual_key`` incorporates the scene's storyboard visual direction so that
    changing the storyboard invalidates the cached render.
    """
    prev_serial = None
    if previous_context:
        prev_serial = {
            "text": previous_context.get("text"),
            "metaphor": previous_context.get("metaphor"),
            "ending_state": previous_context.get("ending_state"),
        }
    hash_data = {
        "cache_version": CACHE_VERSION,
        "text": text,
        "animation": animation,
        "index": index,
        "previous_context": prev_serial,
        "provider": provider,
        "model": model,
        "audio_duration": audio_duration,
        "chapter": chapter,
        "objective": objective,
        "explanation": explanation,
        "visual_key": visual_key,
    }
    serialized = json.dumps(hash_data, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Workflow: script generation
# --------------------------------------------------------------------------- #


def generate_script_workflow(
    job_id, topic, enable_tts, llm_provider, tts_provider=None, tts_voice=None,
    tts_rate=None, bypass_cache=False, bypass_scene_cache=False, target_duration=60,
):
    """Background worker: generate + validate the script, then await review."""
    try:
        ensure_base_dirs()
        JobWorkspace(job_id).create()
        target_duration = validate_target_duration(target_duration)

        update_job_status(job_id, status="running", progress=5, current_step="script",
                          message="Setting up LLM client...")

        try:
            selection = _job_selection(job_id, llm_provider)
        except ProviderUnavailableError as exc:
            update_job_status(job_id, status="failed", error=str(exc),
                              error_category="unavailable_model", message=f"Error: {exc}")
            return
        roles = selection.roles
        provider = selection.provider

        status_sink = _make_llm_status(job_id)
        service = LLMService(roles, policy=get_retry_policy(), status=status_sink,
                             strict=selection.strict)
        client = build_provider_client(provider)

        # Script cache (content-addressed, versioned).
        script_hash = {
            "cache_version": CACHE_VERSION,
            "topic": topic.strip().lower(),
            "provider": provider,
            "model": roles.script,
            "target_duration": target_duration,
        }
        script_cache_key = hashlib.sha256(
            json.dumps(script_hash, sort_keys=True).encode("utf-8")
        ).hexdigest()
        cached_script_json = SCRIPT_CACHE_DIR / f"{script_cache_key}.json"

        video_data = None
        cache_enabled = os.getenv("SCRIPT_CACHE_ENABLED", "true").lower() == "true"
        if cache_enabled and not bypass_cache and not bypass_scene_cache and cached_script_json.exists():
            try:
                with open(cached_script_json, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                # Re-validate cached content before trusting it.
                video_data = parse_script(cached).as_scene_dicts()
                update_job_status(job_id, meta={"cache": "hit"},
                                  message=f"Reused cached script (key {script_cache_key[:8]})")
            except (ScriptValidationError, Exception) as exc:
                print(f"  [WARNING] Ignoring invalid cached script: {exc}")
                video_data = None

        if not video_data:
            update_job_status(job_id, progress=10, current_step="script",
                              meta={"cache": "miss"},
                              message=f"Generating script with {provider} ({roles.script})...")
            video_data = animations.generate_script(
                service=service, topic_name=topic, provider=provider,
                client=client, target_duration=target_duration, status=status_sink,
            )
            try:
                SCRIPT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                with open(cached_script_json, "w", encoding="utf-8") as f:
                    json.dump(video_data, f, ensure_ascii=False, indent=2)
            except Exception as exc:
                print(f"  [WARNING] Failed to store script cache: {exc}")

        jobs.setdefault(job_id, {})["video_data"] = video_data
        update_job_status(job_id, status="awaiting_review", progress=25, current_step="script",
                          meta={"total_scenes": len(video_data)},
                          message="Script generated. Awaiting review and editing.")

    except (LLMError, ScriptValidationError) as exc:
        _fail_script_stage(job_id, llm_provider, exc)
    except Exception as exc:
        _fail_script_stage(job_id, llm_provider, exc)


def _fail_script_stage(job_id: str, llm_provider: str, exc: BaseException) -> None:
    """Fail the script stage with precise, actionable, secret-free diagnostics.

    Replaces the old generic "Could not generate script" with the selected
    model, stage, attempt/retry state, error category and the job log path.
    """
    job = jobs.get(job_id, {})
    selection = job.get("_model_selection")
    meta = job.get("metadata", {}) or {}
    category = getattr(exc, "category", None) or "invalid_output"
    model = getattr(exc, "model", None) or meta.get("script_model") or "unknown"
    provider = (selection.provider if selection else meta.get("provider")) or "unknown"
    strict = bool(selection.strict) if selection else False
    attempt = meta.get("attempt")
    ws = JobWorkspace(job_id)

    detail = {
        "job_id": job_id,
        "stage": "script_generation",
        "provider": provider,
        "model": model,
        "ui_selection": llm_provider,
        "strict_model": strict,
        "attempt": attempt,
        "max_attempts": meta.get("max_attempts"),
        "cooldown": meta.get("cooldown", False),
        "error_category": category,
        "error": str(exc)[:600],
        "log_file": str(ws.log_file()),
    }
    _write_job_log(job_id, "script_failed", detail)

    msg = (
        f"Script generation failed [{category}] using {provider}/{model}"
        + (f" after {attempt} attempt(s)" if attempt else "")
        + (" (strict mode: no fallback was used)" if strict else "")
        + f". Reason: {str(exc)[:220]}. Logs: {ws.log_file()}"
    )
    update_job_status(job_id, status="failed", error=msg, error_category=category,
                      meta={"failure": detail}, message=msg)


# --------------------------------------------------------------------------- #
# Workflow: rendering
# --------------------------------------------------------------------------- #


def _next_previous_context(scene: dict, storyboard_entry: Optional[dict]) -> dict:
    """Build a COMPACT summary of a scene for the next scene's continuity prompt.

    ``ending_state`` prefers the storyboard's OWN ending_state field — what the
    frame actually looks like when the scene stops. It previously fell straight
    through to primary_motion, which describes the scene's *motion*, not its
    final frame; the next scene was therefore told to match a hand-off that had
    never been described. primary_motion/composition remain as fallbacks for
    older stored scenes that predate the ending_state field.
    """
    if storyboard_entry:
        return {
            "text": scene.get("text", ""),
            "metaphor": storyboard_entry.get("visual_metaphor", ""),
            "ending_state": (storyboard_entry.get("ending_state")
                             or storyboard_entry.get("primary_motion")
                             or storyboard_entry.get("composition") or "clean end state"),
            "carry_forward": storyboard_entry.get("continuity_notes") or "",
        }
    return {
        "text": scene.get("text", ""),
        "metaphor": (scene.get("animation", "") or "")[:120],
        "ending_state": "clean end state",
    }


def _measure_core_prompt_chars() -> int:
    """Size of the always-on prompt core (everything except the routed slices).

    Computed once at import from a bare 'general' 2D prompt, so the per-scene
    audit can report a prompt size without rebuilding the whole prompt.
    """
    try:
        baseline = manim_generator._build_generation_prompt(
            text="", animation="", previous_context=None, audio_duration=None,
            chapter="", objective="", explanation="", storyboard_entry=None,
            global_style="", ledger_summary="",
        )
        routed = (len(domain_guidance.build_domain_section(["general"]))
                  + len(manim_generator._dimension_section("2d")))
        return max(0, len(baseline) - routed)
    except Exception:  # pragma: no cover - never block startup on a metric
        return 0


_CORE_PROMPT_CHARS = _measure_core_prompt_chars()


# Ceiling on the WHOLE compile+repair loop for one scene, regardless of how
# many of its 3 attempts are spent. 3 compile timeouts alone can already reach
# 3x the per-attempt timeout (see resolve_compile_timeout) before any repair
# call is even made, so this is set generously above that worst case rather
# than tightened — the point is a backstop, not a new bottleneck.
_SCENE_COMPILE_BUDGET_SECONDS = 900.0  # 15 minutes


def _is_timeout_error(compile_error: Optional[str]) -> bool:
    """Detect our own compile-timeout message (see concat_video.compile_video)."""
    return bool(compile_error) and "Timeout: compilation exceeded" in compile_error


def _domain_routing_audit(
    index, storyboard_entry, scene, audio_duration,
    previous_context, global_style, ledger_summary,
) -> dict:
    """Explain (and size) this scene's prompt routing. Never raises.

    Records which guidance modules were injected and why, so a wrongly-routed
    scene can be diagnosed from the job log without re-running generation.
    Contains no secrets: tags, dimension, sizes and a short reason only.
    """
    try:
        tags = manim_generator.resolve_domain_tags(storyboard_entry)
        dimension = (storyboard_entry or {}).get("dimension") or "2d"
        # Size the ROUTED parts only. Rebuilding the whole prompt here just to
        # measure it would double prompt-construction work on every scene; the
        # always-on core is a known constant, so the routed slices are what
        # actually explain a scene's prompt size.
        routed_chars = (
            len(domain_guidance.build_domain_section(tags))
            + len(manim_generator._dimension_section(dimension))
        )
        chars = _CORE_PROMPT_CHARS + routed_chars
        if storyboard_entry is None:
            reason = "no storyboard entry for this scene -> safe 'general' default"
        elif not storyboard_entry.get("primary_domain_tag"):
            reason = "storyboard entry predates domain routing -> safe 'general' default"
        else:
            reason = "tags selected by the storyboard model from this scene's intent"
        return {
            "scene": index,
            "primary_domain_tag": tags[0],
            "secondary_domain_tags": tags[1:],
            "dimension": dimension,
            "scene_kind": (storyboard_entry or {}).get("scene_kind") or "explanation",
            "narrative_role": (storyboard_entry or {}).get("narrative_role") or "standalone",
            "injected_modules": tags,
            "prompt_chars": chars,
            "prompt_tokens_estimate": chars // 4,  # ~4 chars/token, rough by design
            "routing_reason": reason,
        }
    except Exception as exc:  # observability must never break a render
        return {
            "scene": index, "primary_domain_tag": "general",
            "secondary_domain_tags": [], "dimension": "2d",
            "scene_kind": "explanation", "narrative_role": "standalone",
            "injected_modules": ["general"], "prompt_chars": 0,
            "prompt_tokens_estimate": 0,
            "routing_reason": f"audit failed: {type(exc).__name__}",
        }


def _generate_and_compile(
    ws, service, provider, client, index, total, job_id, scene, audio_duration,
    previous_context, storyboard_entry, global_style, ledger_summary, regen_feedback=None,
):
    """Generate Manim code (storyboard-directed) and run the compile-fix REPL.

    Returns ``(video_path or None, code, class_name, failure_info or None)``.
    """
    anim_model = getattr(getattr(service, "roles", None), "animation", None) or provider

    # Stage 1/6 — request started (exact model is always visible).
    update_job_status(
        job_id, current_step="code",
        meta={"scene": index, "scene_stage": "request_started", "model": anim_model},
        message=f"[INFO] [LLM] Scene {index}/{total}: Generating Manim code via {anim_model}...",
    )
    _write_job_log(job_id, "scene_stage", {
        "scene": index, "stage": "request_started",
        "provider": provider, "model": anim_model,
    })

    # Domain routing audit: why this scene got the guidance it did. Recorded
    # before the request so a failed/hung generation is still explainable.
    routing = _domain_routing_audit(index, storyboard_entry, scene, audio_duration,
                                    previous_context, global_style, ledger_summary)
    _write_job_log(job_id, "domain_routing", routing)
    update_job_status(
        job_id, meta={"scene": index, "domain_routing": routing},
        message=(f"[INFO] [ROUTE] Scene {index}/{total}: domain={routing['primary_domain_tag']}"
                 + (f" +{'+'.join(routing['secondary_domain_tags'])}"
                    if routing["secondary_domain_tags"] else "")
                 + f", dimension={routing['dimension']}, "
                   f"prompt~{routing['prompt_tokens_estimate']} tokens"),
    )

    manim_res = manim_generator.generate_manim_code(
        service=service, text=scene.get("text", ""), animation=scene.get("animation", ""),
        index=index, provider=provider, client=client, previous_context=previous_context,
        audio_duration=audio_duration, chapter=scene.get("chapter", ""),
        objective=scene.get("objective", ""), explanation=scene.get("explanation", ""),
        storyboard_entry=storyboard_entry, global_style=global_style,
        ledger_summary=ledger_summary, regen_feedback=regen_feedback,
        status=_make_llm_status(job_id),
    )
    if manim_res and manim_res.get("raw_received"):
        # Stage 2/6 — response received.
        update_job_status(
            job_id, meta={"scene": index, "scene_stage": "response_received"},
            message=f"[INFO] [LLM] Scene {index}/{total}: Response received from {anim_model}",
        )
    if not manim_res or not manim_res.get("content"):
        err_cat = manim_res.get("error_category") if manim_res else "invalid_output"
        err_msg = manim_res.get("error_message") if manim_res else "Manim code generation produced no code"
        model_name = manim_res.get("model") if manim_res else getattr(getattr(service, "roles", None), "animation", provider)
        val_errors = manim_res.get("validation_errors") if manim_res else None

        failure_info = {
            "scene": index,
            "stage": "manim_llm_generation",
            "provider": provider,
            "model": model_name,
            "error_category": err_cat,
            "error_message": err_msg,
            "validation_errors": val_errors,
            "fallback_used": manim_res.get("used_fallback", False) if manim_res else False,
            "fallback_reason": manim_res.get("fallback_reason") if manim_res else None,
            "job_log_path": f"/media/jobs/{job_id}/logs/job.log",
            "strict_model": getattr(service, "strict", False),
        }
        _write_job_log(job_id, "scene_failed", failure_info)
        update_job_status(
            job_id, meta={"scene": index, "scene_stage": "failed"},
            message=(f"[ERR] [LLM] Scene {index}/{total}: Failed at manim_llm_generation "
                     f"[{err_cat}] model={model_name}: {str(err_msg)[:160]}"),
        )
        return None, "", f"Scene{index}", failure_info

    current_code = manim_res.get("content", "")
    current_class = manim_res.get("class_name", f"Scene{index}")
    code_path = ws.scene_code(index)
    video_path = None
    compile_error = None

    # Stage 3/6 — validated.
    update_job_status(
        job_id, meta={"scene": index, "scene_stage": "validated"},
        message=f"[OK] [MANIM] Scene {index}/{total}: Code validated (class: {current_class})",
    )

    # Hard wall-clock ceiling for this scene's whole compile+repair loop,
    # independent of attempt count. A 60-minute Windows TCP-hang incident (fixed
    # separately via an HTTP timeout on the Gemini client) showed that bounding
    # attempts alone doesn't bound wall time if any single call can stall.
    scene_deadline = time.time() + _SCENE_COMPILE_BUDGET_SECONDS

    for iteration in range(3):
        if time.time() > scene_deadline:
            update_job_status(
                job_id, message=(f"[WARN] [REPL] Scene {index}/{total}: wall-clock budget "
                                 f"({_SCENE_COMPILE_BUDGET_SECONDS:.0f}s) exceeded, stopping retries"),
            )
            compile_error = compile_error or "scene compile budget exceeded"
            break
        timing_path = ws.scene_timing(index)
        try:
            timing_path.unlink()  # stale timing must not leak across attempts
        except OSError:
            pass
        synced = append_duration_sync(current_code, audio_duration, timing_path)
        code_path.write_text(synced, encoding="utf-8")
        # Stage 4/6 — source written.
        update_job_status(
            job_id, meta={"scene": index, "scene_stage": "source_written"},
            message=f"[INFO] [MANIM] Scene {index}/{total}: Source written -> {code_path.name}",
        )
        # Stage 5/6 — compiling.
        update_job_status(
            job_id,
            current_step="code",
            meta={"scene": index, "scene_stage": "compiling"},
            message=f"[INFO] [RENDER] Scene {index}/{total}: Compiling animation to MP4 (attempt {iteration + 1}/3)...",
        )
        # Per-scene media_dir: isolates Manim's content-hashed texts/images cache
        # so scenes can never clobber each other's cache entries.
        t_start = time.time()
        scene_is_3d = (storyboard_entry or {}).get("dimension") == "3d"
        video_path, compile_error = compile_video(
            code_path, current_class, ws.scene_media_dir(index), is_3d=scene_is_3d)
        t_elapsed = time.time() - t_start

        if video_path and os.path.exists(video_path):
            v_size_mb = os.path.getsize(video_path) / (1024.0 * 1024.0)
            update_job_status(
                job_id, meta={"scene": index, "scene_stage": "compiled"},
                message=f"[OK] [RENDER] Scene {index}/{total}: Compiled MP4 in {t_elapsed:.1f}s (size: {v_size_mb:.2f} MB, attempt {iteration + 1})",
            )
            print(f"[REPL] Scene {index} compiled on iteration {iteration + 1}")
            break
        if compile_error and iteration < 2:
            err_line = str(compile_error).strip().splitlines()[-1] if compile_error else "Unknown render error"
            err_short = err_line[:120]
            update_job_status(
                job_id, message=f"[WARN] [REPL] Scene {index}/{total}: Attempt {iteration + 1} failed ({err_short})"
            )
            fixed = manim_generator.fix_manim_code(
                service=service, original_code=current_code, error_message=compile_error,
                class_name=current_class, provider=provider, client=client,
                storyboard_entry=storyboard_entry,
                status=_make_llm_status(job_id),
            )
            if not fixed:
                break
            current_code = fixed.get("content", "")
            current_class = fixed.get("class_name", current_class)
            code_lines = len(current_code.splitlines())
            update_job_status(
                job_id, message=f"[INFO] [REPL] Scene {index}/{total}: Repair patch applied -> class: {current_class} ({code_lines} lines)"
            )
        else:
            break

    if not (video_path and os.path.exists(video_path)):
        failure_info = {
            "scene": index,
            "stage": "compilation",
            "provider": provider,
            "model": manim_res.get("model", getattr(getattr(service, "roles", None), "animation", provider)),
            "error_category": "render_failed",
            "error_message": f"Compilation failed: {compile_error or 'Unknown error'}",
            "validation_errors": None,
            "fallback_used": manim_res.get("used_fallback", False),
            "fallback_reason": manim_res.get("fallback_reason"),
            "job_log_path": f"/media/jobs/{job_id}/logs/scene_{index}_error.json",
        }
        return None, current_code, current_class, failure_info

    return video_path, current_code, current_class, None


def _apply_timing_flags(ws, index, report: dict, audio_duration, vcfg) -> None:
    """Fold the rendered scene's timing record into its QA report.

    Reads the sidecar written by the duration-sync shim to learn how much of the
    scene was a frozen tail, and flags ``static_end_padding`` / ``av_drift``.
    """
    try:
        path = ws.scene_timing(index)
        if not path.exists():
            return
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return

    pad = float(data.get("pad_seconds") or 0.0)
    anim = float(data.get("animation_seconds") or 0.0)
    target = float(data.get("target_seconds") or (audio_duration or 0.0))
    report["pad_seconds"] = round(pad, 2)
    report["animation_seconds"] = round(anim, 2)
    report["target_seconds"] = round(target, 2)

    max_pad = getattr(vcfg, "max_tail_pad_seconds", 0.75)
    if pad > max_pad:
        report["static_end_padding"] = True
        report.setdefault("flags", []).append(visual_qa.FLAG_STATIC_END_PADDING)
    # Animation materially shorter than the narration it must cover.
    if target > 0 and anim < target * 0.6:
        report.setdefault("flags", []).append(visual_qa.FLAG_AV_DRIFT)
    report["flags"] = sorted(set(report.get("flags", [])))


def _scene_qa(ws, index, video_path, vcfg, storyboard_entry) -> dict:
    """Run per-scene visual QA (frame extraction + heuristics). Never raises."""
    if not vcfg.visual_qa_enabled:
        return {"index": index, "flags": [], "blank": False, "frames": []}
    try:
        return visual_qa.analyze_scene(
            video_path=video_path, index=index,
            frame_paths_for_index=lambda k: ws.scene_frame(index, k),
            thumb_path=ws.scene_thumb(index), cfg=vcfg, storyboard_entry=storyboard_entry,
        )
    except Exception as exc:  # QA must never break rendering
        print(f"  [WARNING] Visual QA failed for scene {index}: {exc}")
        return {"index": index, "flags": ["qa_error"], "blank": False, "frames": []}


def _finalize_visual_qa(ws, job_id, qa_reports, scene_thumbs, vcfg, sb) -> None:
    """Cross-scene QA, contact sheet, QA report, and safe status metadata."""
    if not vcfg.visual_qa_enabled and not vcfg.contact_sheet_enabled:
        return

    ordered_indices = sorted(scene_thumbs.keys())
    ordered_thumbs = [scene_thumbs[i] for i in ordered_indices]
    report_by_index = {r.get("index"): r for r in qa_reports}

    # Near-identical consecutive scenes -> flag the later scene.
    if vcfg.visual_qa_enabled and len(ordered_thumbs) >= 2:
        try:
            for a, b in visual_qa.flag_near_identical_scenes(ordered_thumbs, vcfg):
                later = report_by_index.get(ordered_indices[b])
                if later is not None:
                    later.setdefault("flags", []).append(visual_qa.FLAG_NEAR_IDENTICAL)
                    later["flags"] = sorted(set(later["flags"]))
        except Exception as exc:
            print(f"  [WARNING] Cross-scene QA failed: {exc}")

    # Contact sheet.
    contact_url = None
    if vcfg.contact_sheet_enabled and ordered_thumbs:
        try:
            labels = [f"Scene {i}" for i in ordered_indices]
            if visual_qa.build_contact_sheet(ordered_thumbs, ws.contact_sheet(), cols=3, labels=labels):
                contact_url = f"/media/jobs/{job_id}/qa/contact_sheet.png"
        except Exception as exc:
            print(f"  [WARNING] Contact sheet failed: {exc}")

    # QA report file + safe status metadata.
    flags_by_scene = {r.get("index"): r.get("flags", []) for r in qa_reports if r.get("flags")}
    residual = getattr(sb, "_residual_violations", []) if sb else []
    qa_summary = {
        "scenes": qa_reports,
        "flagged_scenes": flags_by_scene,
        "diversity_notes": residual,
        "contact_sheet": "qa/contact_sheet.png" if contact_url else None,
    }
    try:
        visual_qa.save_qa_report(qa_summary, ws.visual_qa_file())
    except Exception as exc:
        print(f"  [WARNING] Could not save QA report: {exc}")

    meta = {
        "visual_flags": flags_by_scene,
        "flagged_scene_count": len(flags_by_scene),
        "visual_qa_url": f"/media/jobs/{job_id}/qa/visual_qa.json",
    }
    if contact_url:
        meta["contact_sheet_url"] = contact_url
    msg = (f"Visual QA complete: {len(flags_by_scene)} scene(s) flagged for review"
           if flags_by_scene else "Visual QA complete: no issues flagged")
    update_job_status(job_id, meta=meta, message=msg)


def _render_scene(
    ws, service, provider, client, index, total, scene, previous_context,
    audio_duration, bypass_scene_cache, job_id, provider_model,
    storyboard_entry, global_style, ledger_summary, vcfg,
):
    """Generate + compile one scene (storyboard-directed) with QA + one visual repair.

    Returns ``(scene_video_path or None, new_previous_context, qa_report)``.
    """
    visual_key = None
    if storyboard_entry:
        visual_key = json.dumps(storyboard_entry, sort_keys=True, ensure_ascii=False)

    cache_key = compute_scene_cache_key(
        text=scene.get("text", ""), animation=scene.get("animation", ""), index=index,
        previous_context=previous_context, provider=provider, model=provider_model,
        audio_duration=audio_duration, chapter=scene.get("chapter", ""),
        objective=scene.get("objective", ""), explanation=scene.get("explanation", ""),
        visual_key=visual_key,
    )
    cache_mp4 = SCENE_CACHE_DIR / f"{cache_key}.mp4"
    cache_py = SCENE_CACHE_DIR / f"{cache_key}.py"
    cache_json = SCENE_CACHE_DIR / f"{cache_key}.json"
    scene_cache_enabled = os.getenv("SCENE_CACHE_ENABLED", "true").lower() == "true"
    next_ctx = _next_previous_context(scene, storyboard_entry)
    dest = ws.scene_video(index)

    # --- cache hit (QA for reporting only; no repair) ------------------- #
    if scene_cache_enabled and not bypass_scene_cache and cache_mp4.exists() and cache_py.exists():
        try:
            shutil.copyfile(cache_mp4, dest)
            update_job_status(job_id, meta={"cache": "hit", "scene": index, "total_scenes": total},
                              message=f"[OK] [CACHE] Scene {index}/{total}: Reused cached video render (key: {cache_key[:8]})")
            report = _scene_qa(ws, index, str(dest), vcfg, storyboard_entry)
            return str(dest), next_ctx, report
        except Exception as exc:
            print(f"  [WARNING] Failed to use cached scene {index}: {exc}")

    update_job_status(job_id, meta={"cache": "miss", "scene": index, "total_scenes": total},
                      message=f"→ Scene {index}/{total} (generating storyboard-directed code)")

    video_path, code, cls, failure_info = _generate_and_compile(
        ws, service, provider, client, index, total, job_id, scene, audio_duration,
        previous_context, storyboard_entry, global_style, ledger_summary,
    )
    if not (video_path and os.path.exists(video_path)):
        if failure_info:
            error_log_file = ws.logs / f"scene_{index}_error.json"
            try:
                error_log_file.parent.mkdir(parents=True, exist_ok=True)
                error_log_file.write_text(json.dumps(failure_info, indent=2), encoding="utf-8")
            except Exception as exc:
                print(f"  [WARNING] Could not write scene error log: {exc}")
        return None, next_ctx, {"index": index, "flags": ["render_failed"], "blank": True, "failure_info": failure_info}

    shutil.copyfile(video_path, dest)
    report = _scene_qa(ws, index, str(dest), vcfg, storyboard_entry)
    _apply_timing_flags(ws, index, report, audio_duration, vcfg)

    # --- one scene-level visual repair when the render is blank OR ends in a
    # long frozen tail (the measured cause of static-looking videos) --------- #
    needs_repair = report.get("blank") or report.get("static_end_padding")
    if needs_repair and vcfg.visual_repair_attempts > 0 and vcfg.visual_qa_enabled:
        for attempt in range(vcfg.visual_repair_attempts):
            if report.get("blank"):
                why = "the previously rendered frame was blank/near-empty"
                feedback = ("the previously rendered frame was blank/near-empty; ensure "
                            "clearly visible, well-framed content and motion")
            else:
                pad = report.get("pad_seconds", 0.0)
                anim = report.get("animation_seconds", 0.0)
                why = f"it froze for {pad:.1f}s at the end"
                feedback = (
                    f"your animation only filled {anim:.1f}s of the required "
                    f"{audio_duration:.1f}s, so the last {pad:.1f}s was a FROZEN still "
                    "frame. Add more meaningful visual beats spread across the whole "
                    "duration (a change every 2-4s) so the scene keeps progressing to "
                    "the end. Do NOT pad with a long self.wait()."
                )
            update_job_status(
                job_id, meta={"scene": index, "visual_repair": attempt + 1},
                message=(f"Scene {index}/{total} — {why}; regenerating with more "
                         f"visual beats (repair {attempt + 1}/{vcfg.visual_repair_attempts})"),
            )
            rp_video, rp_code, rp_cls, rp_failure = _generate_and_compile(
                ws, service, provider, client, index, total, job_id, scene, audio_duration,
                previous_context, storyboard_entry, global_style, ledger_summary,
                regen_feedback=feedback,
            )
            if rp_video and os.path.exists(rp_video):
                shutil.copyfile(rp_video, dest)
                new_report = _scene_qa(ws, index, str(dest), vcfg, storyboard_entry)
                _apply_timing_flags(ws, index, new_report, audio_duration, vcfg)
                video_path, code, cls, report = rp_video, rp_code, rp_cls, new_report
                if not (new_report.get("blank") or new_report.get("static_end_padding")):
                    break

    # --- store to shared scene cache ------------------------------------ #
    if scene_cache_enabled:
        try:
            SCENE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(dest, cache_mp4)
            cache_py.write_text(code, encoding="utf-8")
            cache_json.write_text(json.dumps({
                "cache_version": CACHE_VERSION, "index": index, "class_name": cls,
                "audio_duration": audio_duration, "provider": provider, "model": provider_model,
                "flags": report.get("flags", []),
            }, indent=2), encoding="utf-8")
        except Exception as exc:
            print(f"  [WARNING] Failed to cache scene {index}: {exc}")

    flag_note = f" [{', '.join(report.get('flags', []))}]" if report.get("flags") else ""
    update_job_status(job_id, meta={"scene": index, "scene_stage": "rendered"},
                      message=f"[OK] [RENDER] Scene {index}/{total}: Rendered successfully{flag_note}")
    return str(dest), next_ctx, report


def generate_rendering_workflow(job_id):
    """Background worker: TTS + Manim + concat + mux + validation."""
    try:
        job = jobs.get(job_id)
        if not job:
            return

        ws = JobWorkspace(job_id).create()
        topic = job.get("topic")
        enable_tts = job.get("enable_tts", True)
        llm_provider = job.get("llm_provider", "auto")
        tts_provider = job.get("tts_provider")
        tts_voice = job.get("tts_voice")
        tts_rate = job.get("tts_rate")
        bypass_cache = job.get("bypass_cache", False)
        bypass_scene_cache = job.get("bypass_scene_cache", False)
        video_data = job.get("video_data", [])

        # Re-validate the (possibly user-edited) script before rendering.
        try:
            video_data = parse_script(video_data).as_scene_dicts()
        except ScriptValidationError as exc:
            update_job_status(job_id, status="failed", error=str(exc),
                              error_category="invalid_output",
                              message=f"Script validation failed: {exc}")
            return

        try:
            # Reuse the SAME canonical selection created at job start so the
            # user's explicit model choice survives the review/continue step.
            selection = _job_selection(job_id, llm_provider)
            roles = selection.roles
            validate_animation_model(roles)
        except ProviderUnavailableError as exc:
            update_job_status(job_id, status="failed", error=str(exc),
                              error_category="unavailable_model",
                              message=f"Configuration error: {exc}")
            return

        provider = selection.provider
        target_duration = int(job.get("target_duration", 60) or 60)
        status_sink = _make_llm_status(job_id)
        service = LLMService(roles, policy=get_retry_policy(), status=status_sink,
                             strict=selection.strict)
        client = build_provider_client(provider)
        vcfg = get_visual_config()

        # --- Visual direction: storyboard + ledger + primitives ------------ #
        # Copy the optional primitives helper into the job's code dir so
        # generated scenes may `from visual_primitives import *`.
        try:
            shutil.copyfile(PROJECT_ROOT / "src" / "visual_primitives.py",
                            ws.visual_primitives_copy())
        except Exception as exc:
            print(f"  [WARNING] Could not stage visual primitives: {exc}")

        # TTS depends only on the narration text, and the storyboard depends only
        # on the scene list — neither needs the other. Start TTS now so it runs
        # while the storyboard model is thinking; the result is collected below.
        # Identical inputs and outputs, just overlapped instead of back-to-back.
        _tts_pool = None
        _tts_future = None
        if enable_tts:
            update_job_status(job_id, progress=30, current_step="tts",
                              meta={"tts_status": "generating"},
                              message="Generating audio with TTS...")
            _tts_client = openai.OpenAI(api_key=openai_api_key()) if openai_api_key() else None
            _tts_pool = ThreadPoolExecutor(max_workers=1)
            _tts_future = _tts_pool.submit(
                generate_complete_audio,
                client=_tts_client, video_data=video_data,
                output_dir=ws.audio, output_path=ws.final_audio(),
                list_file=ws.audio / "audio_concat.txt",
                tts_provider=tts_provider,
                tts_model=os.getenv("TTS_MODEL", "tts-1"),
                voice=os.getenv("VOICE", "alloy"),
                tts_voice=tts_voice, tts_rate=tts_rate, bypass_cache=bypass_cache,
                status_callback=lambda progress, message: update_job_status(
                    job_id, progress=progress, current_step="tts", message=message),
            )

        sb = None
        global_style = vcfg.global_style
        if vcfg.storyboard_enabled:
            update_job_status(job_id, progress=27, current_step="script",
                              meta={"stage": "storyboard", "storyboard_model": roles.for_role("storyboard")},
                              message="Directing scenes (visual storyboard)...")
            try:
                sb = storyboard_mod.generate_storyboard(
                    service=service, topic=topic, scenes=video_data, provider=provider,
                    target_duration=target_duration, global_style=global_style,
                    client=client, status=status_sink,
                )
                storyboard_mod.save_storyboard(sb, ws.storyboard_file())
                if getattr(sb, "global_style", None):
                    gs = sb.global_style
                    global_style = (
                        f"{global_style}\nPalette: {', '.join(gs.palette) or 'restrained accents'}. "
                        f"Typography: {gs.typography}. Pacing: {gs.pacing}. Spacing: {gs.spacing}."
                    )
                residual = getattr(sb, "_residual_violations", [])
                update_job_status(
                    job_id, meta={
                        "storyboard": "ready",
                        "storyboard_url": f"/media/jobs/{job_id}/logs/storyboard.json",
                        "distinct_visual_approaches": len({
                            (s.visual_metaphor or "").strip().lower() for s in sb.scenes}),
                        "diversity_notes": residual,
                        "continuity_mode": getattr(sb, "_continuity_mode", "varied"),
                        "semantic_colors": getattr(sb, "_semantic_colors", []),
                    },
                    message=f"Storyboard ready: {len(sb.scenes)} scenes, "
                            f"{len({(s.visual_metaphor or '').strip().lower() for s in sb.scenes})} distinct approaches, "
                            f"continuity={getattr(sb, '_continuity_mode', 'varied')}",
                )
            except (LLMError, ScriptValidationError) as exc:
                # Visual direction must NEVER be silently dropped — that is a
                # quality regression. Fail loudly with precise diagnostics.
                category = getattr(exc, "category", "invalid_output")
                sb_model = selection.model_for("storyboard")
                detail = {
                    "job_id": job_id, "stage": "storyboard",
                    "provider": provider, "model": sb_model,
                    "strict_model": selection.strict,
                    "error_category": category, "error": str(exc)[:600],
                    "log_file": str(ws.log_file()),
                }
                _write_job_log(job_id, "storyboard_failed", detail)
                msg = (
                    f"Visual storyboard failed [{category}] using {provider}/{sb_model}"
                    + (" (strict mode: no fallback was used)" if selection.strict else "")
                    + f". Reason: {str(exc)[:220]}. "
                    "Set STORYBOARD_ENABLED=false to render without visual direction. "
                    f"Logs: {ws.log_file()}"
                )
                update_job_status(job_id, status="failed", error=msg,
                                  error_category=category,
                                  meta={"storyboard": "failed", "failure": detail},
                                  message=msg)
                # Don't orphan the in-flight TTS worker started above.
                if _tts_pool is not None:
                    _tts_pool.shutdown(wait=True)
                return

        ledger = VisualLedger()

        # --- TTS (collect the run started before the storyboard) ------------- #
        audio_path = None
        audio_durations = {}
        if enable_tts:
            try:
                audio_path, audio_durations = _tts_future.result()
            except Exception as exc:
                print(f"  [WARNING] TTS failed: {exc}")
                audio_path, audio_durations = None, {}
            finally:
                _tts_pool.shutdown(wait=True)
            has_audio = bool(audio_path and os.path.exists(audio_path))
            update_job_status(job_id, progress=40, current_step="tts",
                              meta={"tts_status": "ok" if has_audio else "failed"},
                              message="Audio generated" if has_audio else "TTS skipped (failed)")
            if has_audio and audio_durations:
                # Real character count for cost reporting: only the scenes TTS
                # actually synthesized (audio_durations' keys), not the full
                # scene list — a scene that failed TTS was never billed.
                synthesized_chars = sum(
                    len(scene.get("text", "")) for i, scene in enumerate(video_data, 1)
                    if i in audio_durations
                )
                _write_job_log(job_id, "tts_usage", {
                    "provider": tts_provider or "edge-tts",
                    "characters": synthesized_chars,
                    "scenes": len(audio_durations),
                })
        else:
            update_job_status(job_id, progress=40, current_step="code",
                              meta={"tts_status": "disabled"}, message="Skipping TTS (disabled)")

        # --- Manim per scene (storyboard-directed) -------------------------- #
        update_job_status(job_id, progress=45, current_step="code",
                          message="Generating storyboard-directed Manim code...")
        total = len(video_data)
        generated_videos = []
        previous_context = None
        qa_reports = []
        scene_thumbs = {}
        scene_failures = []

        for index, scene in enumerate(video_data, 1):
            progress = 45 + (index / max(1, total)) * 30
            # entry_for() returns None when the storyboard has fewer entries
            # than the (possibly user-edited) scene list — never dereference it
            # blindly, that previously crashed the whole rendering workflow.
            sb_entry = None
            if sb:
                _entry = sb.entry_for(index)
                if _entry is not None:
                    sb_entry = _entry.model_dump()
                    # continuity_mode / semantic_colors are VIDEO-LEVEL decisions
                    # canonicalized once across all scenes (see
                    # storyboard.canonical_continuity_mode) — overwrite this
                    # scene's own values so every scene agrees even if the model
                    # was slightly inconsistent scene-to-scene.
                    sb_entry["continuity_mode"] = getattr(sb, "_continuity_mode", "varied")
                    sb_entry["semantic_colors"] = getattr(sb, "_semantic_colors", [])
                else:
                    _write_job_log(job_id, "storyboard_entry_missing",
                                   {"scene": index, "storyboard_scenes": len(sb.scenes)})
            ledger_summary = ledger.compact_summary()

            update_job_status(job_id, progress=progress, current_step="code",
                              meta={"scene": index, "total_scenes": total},
                              message=f"Processing scene {index}/{total}...")
            scene_video, previous_context, report = _render_scene(
                ws, service, provider, client, index, total, scene, previous_context,
                audio_durations.get(index), bypass_scene_cache, job_id, roles.animation,
                sb_entry, global_style, ledger_summary, vcfg,
            )

            if report.get("failure_info"):
                scene_failures.append(report["failure_info"])

            # Ledger-based repeat flags (computed against PRIOR scenes only).
            if sb_entry:
                if ledger.metaphor_used(sb_entry.get("visual_metaphor", "")):
                    report.setdefault("flags", []).append(visual_qa.FLAG_REPEAT_METAPHOR)
                if ledger.composition_overused(sb_entry.get("composition", "")):
                    report.setdefault("flags", []).append(visual_qa.FLAG_REPEAT_LAYOUT)
                ledger.record_from_storyboard(sb_entry)
            else:
                ledger.record(LedgerEntry(index=index, metaphor=scene.get("animation", "")[:80]))

            report["flags"] = sorted(set(report.get("flags", [])))
            qa_reports.append(report)
            if ws.scene_thumb(index).exists():
                scene_thumbs[index] = ws.scene_thumb(index)
            if scene_video:
                generated_videos.append(scene_video)

        ledger.save(ws.visual_ledger_file())

        if scene_failures:
            update_job_status(job_id, meta={"scene_failures": scene_failures})

        if not generated_videos:
            if scene_failures:
                failure_msgs = [
                    f"Scene {f['scene']} ({f['stage']}): [{f['error_category']}] {f['error_message']}"
                    for f in scene_failures
                ]
                summary = f"Rendering failed (0/{total} scenes compiled). " + "; ".join(failure_msgs)
                primary_cat = scene_failures[0]["error_category"]
            else:
                summary = "Rendering failed: no scenes were produced."
                primary_cat = "render_failed"

            update_job_status(job_id, status="failed", error=summary,
                              error_category=primary_cat,
                              meta={"scene_failures": scene_failures},
                              message=summary)
            return

        # --- Visual QA aggregation + contact sheet -------------------------- #
        _finalize_visual_qa(ws, job_id, qa_reports, scene_thumbs, vcfg, sb)

        # --- Concatenate ---------------------------------------------------- #
        update_job_status(job_id, progress=80, current_step="video",
                          message=f"[INFO] [SYSTEM] Concatenating {len(generated_videos)} scene videos via FFmpeg demuxer...")
        silent = ws.silent_video()
        if not concatenate_videos(generated_videos, silent, ws.concat_list()):
            update_job_status(job_id, status="failed", error="Video concatenation failed",
                              error_category="render_failed",
                              message="[ERR] [SYSTEM] Failed to concatenate scenes. Intermediate assets preserved.")
            return

        # --- Mux ------------------------------------------------------------ #
        final = ws.final_video()
        expect_audio = bool(enable_tts and audio_path and os.path.exists(audio_path))
        if expect_audio:
            update_job_status(job_id, progress=90, current_step="video",
                              message="[INFO] [SYSTEM] Muxing audio track with video stream...")
            if not merge_video_and_audio(silent, audio_path, final):
                shutil.copyfile(silent, final)
                expect_audio = False
        else:
            shutil.copyfile(silent, final)

        # --- Validate output ------------------------------------------------ #
        update_job_status(job_id, progress=96, current_step="video",
                          message="[INFO] [SYSTEM] Validating final output stream...")
        expected_duration = sum(audio_durations.values()) if audio_durations else None
        ok, reason, info = validate_output(
            final, expect_audio=expect_audio, expected_duration=expected_duration,
        )
        if not ok:
            update_job_status(
                job_id, status="failed", error=f"Output validation failed: {reason}",
                error_category="invalid_output",
                meta={"validation": "failed", "validation_reason": reason},
                message=f"[ERR] [SYSTEM] Final video failed validation: {reason}. Assets preserved for inspection.",
            )
            return

        video_url = f"/media/jobs/{job_id}/final/{final.name}"
        f_size_mb = os.path.getsize(final) / (1024.0 * 1024.0) if final.exists() else 0.0
        dur_str = f"{info.duration:.1f}s" if info else "N/A"
        cost_report = _aggregate_cost_report(job_id, scene_count=len(generated_videos))
        update_job_status(
            job_id, status="completed", progress=100, current_step="video",
            meta={"validation": "passed",
                  "output_duration": round(info.duration, 2) if info else None,
                  "output_size_mb": round(f_size_mb, 2),
                  "has_audio": bool(info and info.has_audio),
                  "scene_count": len(generated_videos),
                  "total_cost_usd": cost_report["total_cost_usd"],
                  "total_input_tokens": cost_report["total_input_tokens"],
                  "total_output_tokens": cost_report["total_output_tokens"],
                  "total_tts_characters": cost_report["total_tts_characters"],
                  "llm_call_count": cost_report["llm_call_count"],
                  "cost_breakdown": cost_report["cost_breakdown"],
                  "cost_data_source": cost_report["data_source"]},
            message=f"[OK] [SYSTEM] Workflow complete! Output: {final.name} (duration: {dur_str}, size: {f_size_mb:.2f} MB)",
            video_url=video_url,
        )

    except (LLMError, FFmpegError) as exc:
        category = getattr(exc, "category", "render_failed")
        update_job_status(job_id, status="failed", error=str(exc),
                          error_category=category, message=f"Error: {exc}")
    except Exception as exc:
        update_job_status(job_id, status="failed", error=str(exc),
                          error_category="other", message=f"Error: {exc}")


# --------------------------------------------------------------------------- #
# Public entry points (unchanged signatures for main.py compatibility)
# --------------------------------------------------------------------------- #


def start_video_generation(
    topic, enable_tts=True, llm_provider="auto", tts_provider=None, tts_voice=None,
    tts_rate=None, bypass_cache=False, bypass_scene_cache=False, target_duration=60,
):
    """Start script generation in a background thread; returns the job id."""
    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "job_id": job_id,
        "topic": topic,
        "status": "queued",
        "progress": 0,
        "current_step": "script",
        "message": "Job queued",
        "enable_tts": enable_tts,
        "llm_provider": llm_provider,
        "tts_provider": tts_provider,
        "tts_voice": tts_voice,
        "tts_rate": tts_rate,
        "bypass_cache": bypass_cache,
        "bypass_scene_cache": bypass_scene_cache,
        "target_duration": validate_target_duration(target_duration),
        "metadata": {},
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    }
    threading.Thread(
        target=generate_script_workflow,
        args=(job_id, topic, enable_tts, llm_provider, tts_provider, tts_voice,
              tts_rate, bypass_cache, bypass_scene_cache, jobs[job_id]["target_duration"]),
        daemon=True,
    ).start()
    return job_id


def continue_video_generation(job_id, video_data):
    """Resume rendering with (possibly edited) validated scene data."""
    if job_id not in jobs:
        return False
    job = jobs[job_id]
    if job["status"] != "awaiting_review":
        return False

    job["video_data"] = video_data
    job["status"] = "queued"
    job["progress"] = 25
    job["current_step"] = "tts"
    job["message"] = "Rendering pipeline started with custom script"
    job["updated_at"] = datetime.now().isoformat()

    threading.Thread(target=generate_rendering_workflow, args=(job_id,), daemon=True).start()
    return True


def get_job_status(job_id):
    """Return the current job state dict (or None)."""
    return jobs.get(job_id)
