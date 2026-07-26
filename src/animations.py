"""Script/scene generation.

Builds the educational-script prompt, calls the LLM through the centralized
:class:`~llm_service.LLMService` (Gemini reliability, provider routing), and
validates every response with the Pydantic :class:`~schemas.VideoScript` model
before it is allowed downstream. On a validation failure exactly one controlled
repair request is issued using the current provider; if that also fails, a clear
error is raised so the job fails safely rather than rendering garbage.
"""

from __future__ import annotations

import json
from typing import Any, List, Optional

from dotenv import load_dotenv

from llm_service import LLMService, StatusCallback
from schemas import (
    TTS_WORDS_PER_SECOND,
    Scene,
    ScriptValidationError,
    VideoScript,
    estimate_narration_seconds,
    parse_script_from_text,
)

load_dotenv()

_SYSTEM_INSTRUCTION = (
    "You are an expert in creating educational video scripts. You always respond "
    "in valid JSON format without additional text. IMPORTANT: Match the language "
    "of the topic exactly - if the topic is in Spanish, write in Spanish; if in "
    "English, write in English."
)


# A scene of ~9s is a good pedagogical beat: long enough to show a real
# transformation, short enough to hold attention.
_SECONDS_PER_SCENE = 9.0


def _duration_profile(target_duration: int) -> dict:
    """Derive scene/word budgets from the requested duration.

    Video length is decided *entirely* by how much narration the model writes,
    so the budget is expressed in WORDS. Models reason poorly about "seconds of
    speech" but reliably about word counts, and the previous wording ("MAXIMUM
    120 seconds") read as a ceiling, pushing the model to under-deliver — a 120s
    request produced 58s of narration.
    """
    target = max(10, int(target_duration or 60))
    wps = TTS_WORDS_PER_SECOND  # ~2.6 words/second of speech

    scenes = max(3, min(22, round(target / _SECONDS_PER_SCENE)))
    lo_scenes = max(3, scenes - 1)
    hi_scenes = min(25, scenes + 2)

    per_scene_seconds = target / scenes
    words = round(per_scene_seconds * wps)
    lo_words = max(12, words - 4)
    hi_words = words + 5

    total_words = round(target * wps)
    chapters = max(2, min(5, round(scenes / 2.5)))

    return {
        "scene_count": f"exactly {lo_scenes}-{hi_scenes} scenes",
        "chapters": f"~{chapters} logical chapters",
        "per_scene": f"approximately {per_scene_seconds:.0f} seconds",
        "time_rest": f"as close as possible to {target} seconds",
        "words_per_scene": f"{lo_words}-{hi_words} words",
        "total_words": total_words,
        "target_seconds": target,
        "scenes_target": scenes,
    }


def _build_prompt(topic_name: str, target_duration: int) -> str:
    p = _duration_profile(target_duration)
    return f"""Develop an educational script for this topic: {topic_name}

INSTRUCTIONS:
- Create an engaging and educational script about the topic
- Divide the script into logical scenes/fragments ({p['scene_count']})
- Group the scenes into {p['chapters']} (e.g. "Chapter 1: Introduction", "Chapter 2: The Core Concept", "Chapter 3: Real-World Example/Summary")
- For each scene, provide:
  1. The chapter title it belongs to
  2. The script text (narration) - BRIEF and CONCISE
  3. A detailed description of the Manim animation that should accompany that text
  4. A clear learning objective for this scene
  5. A brief educational explanation of the core concept shown in this scene
- Avoid using commercial logos (like ChatGPT, OpenAI, etc.)
- I DON'T want Python Manim code, just the description of what you want to visualize
- Animations should be specific and detailed so they can be implemented in Manim

LESSON STRUCTURE (follow this arc across the scenes):
- HOOK: open with the concrete problem or question this topic answers (never a generic
  "Welcome to..." or "In this video we will...").
- INTUITION: the plain-language mental model before any formalism.
- MECHANISM: how it actually works, step by step.
- EXAMPLE / APPLICATION: one concrete worked case or real use.
- RECAP: the single most important takeaway (only if the duration allows).

NARRATION QUALITY (this is spoken aloud — write for the ear):
- Give each scene ONE clear learning goal and ONE main idea. Never cram several ideas
  into a single scene.
- Use clear, natural, accurate educational language. Be specific and concrete.
- REMOVE: filler, hedging, vague claims, marketing tone, generic intros/outros, and any
  restatement of what a previous scene already said.
- Do NOT repeat the same sentence or idea in two scenes.
- Preserve exact factual meaning and correct mathematical/technical terminology.
- NEVER invent facts, numbers, citations, or claims you are not certain of.
- Keep terminology and capitalization consistent across the whole video (same concept =
  same word every time).

NARRATION LENGTH (must match real speech pace of ~2.6 words/second):
- Each scene's "text" must be sized so it can be spoken comfortably within that scene's
  share of the total duration. Aim for roughly 2-3 words per second of scene time.
- Prefer short, complete sentences over long subordinate clauses.

VISUAL/NARRATION ALIGNMENT:
- Every narration sentence must correspond to something the viewer can SEE happening:
  a reveal, a transformation, a comparison, a movement, or a value updating.
- Do not narrate a concept that is not on screen at that moment.
- The "animation" field must describe visible, progressive change — not a static picture.
- For MATHS or PHYSICS topics, make the "animation" field describe the SPECIFIC
  visual reasoning, not a vague picture: e.g. "a tangent line sliding along the curve
  as its gradient is read off", "the unit square transforming under the matrix",
  "force arrows resolving into components", "two waves adding into their sum",
  "a Riemann sum refining as the rectangles get thinner". Name the concrete objects
  (axes, vectors, arrows, curve, grid) and how they move or change.

LANGUAGE REQUIREMENT:
- The script, chapters, objectives, explanations, and animations MUST be in the SAME LANGUAGE as the topic
- Match the language exactly
- Write real Unicode characters directly (accents, Greek letters, arrows, math symbols).
  Never emit escaped or corrupted encodings.

CRITICAL LENGTH BUDGET (this decides the final video length — get it right):
- The finished video must run {p['time_rest']}. This is a TARGET to HIT, not a
  ceiling to stay under. A video that is too SHORT is a FAILURE.
- Produce {p['scene_count']}.
- Each scene should play for {p['per_scene']}.
- WORD BUDGET (most important): each scene's "text" must contain
  {p['words_per_scene']}. Narration is spoken at about 2.6 words per second, so
  fewer words = a shorter video than requested.
- The whole script should total roughly {p['total_words']} words across all scenes.
- Write 2-3 complete, substantive sentences per scene — enough to genuinely
  explain the idea, never one short clause.
- Do NOT pad with filler or repetition to reach the count: add real explanatory
  content, concrete detail, or a worked step.

OUTPUT FORMAT (JSON):
Respond ONLY with a valid JSON array, where each element has this structure:
{{
  "chapter": "Chapter title (e.g., Chapter 1: Introduction)",
  "text": "script text for this scene (BRIEF, 2-3 sentences maximum)",
  "animation": "detailed description of the specific animation for this fragment",
  "objective": "specific pedagogical learning objective of this scene",
  "explanation": "educational conceptual explanation of what is shown/taught in this scene"
}}

IMPORTANT: Respond ONLY with the JSON array, without any additional text before or after."""


def _build_repair_prompt(topic_name: str, target_duration: int, raw_output: str, error: str) -> str:
    truncated = raw_output[:2000]
    return f"""Your previous response was NOT valid according to the required schema.

VALIDATION ERROR:
{error}

Your previous (invalid) output was:
{truncated}

Please regenerate a CORRECT educational script for the topic: {topic_name}

REQUIREMENTS (all fields are REQUIRED and must be non-empty strings):
- "chapter", "text", "animation", "objective", "explanation"
- Respond ONLY with a valid JSON array of scene objects, nothing else.
- Keep it to {_duration_profile(target_duration)['scene_count']}.
- Match the language of the topic."""


def estimate_script_seconds(scenes: List[dict]) -> float:
    """Estimated spoken length of a whole script, at typical TTS pace."""
    return sum(estimate_narration_seconds(s.get("text", "")) for s in scenes)


def _build_length_repair_prompt(
    topic_name: str, target_duration: int, scenes: List[dict], estimated: float
) -> str:
    """Ask for a longer/shorter script, keeping the existing structure intact."""
    p = _duration_profile(target_duration)
    direction = "TOO SHORT" if estimated < target_duration else "TOO LONG"
    verb = ("EXPAND the narration" if estimated < target_duration
            else "TIGHTEN the narration")
    current = json.dumps(scenes, ensure_ascii=False, indent=1)[:6000]

    return f"""Your script for "{topic_name}" is {direction}.

MEASURED: about {estimated:.0f} seconds of narration.
REQUIRED: about {target_duration} seconds ({p['total_words']} words total).

{verb} so the script actually runs ~{target_duration} seconds:
- Keep the SAME scenes, order, chapters, objectives and animations wherever possible.
- Aim for {p['words_per_scene']} in each scene's "text".
- {'Add real explanatory substance: concrete detail, a worked step, an example, or the "why". Do NOT pad with filler, repetition, or restatement.' if estimated < target_duration else 'Remove filler and repetition first; preserve all factual content and terminology.'}
- Preserve the language, meaning and technical accuracy of the original.
- You may add or merge at most one or two scenes if that genuinely helps
  ({p['scene_count']} total).

CURRENT SCRIPT:
{current}

Respond ONLY with the corrected JSON array of scene objects."""


def _enforce_target_duration(
    scenes: List[dict],
    service: LLMService,
    topic_name: str,
    provider: str,
    client: Any,
    target_duration: int,
    status: StatusCallback = None,
    min_ratio: float = 0.80,
    max_ratio: float = 1.30,
) -> List[dict]:
    """Ensure the script can actually fill the requested duration.

    Video length is set purely by narration length, and nothing previously
    checked it — a 120s request shipped as a 58s video. If the estimate is well
    outside the target, make ONE repair request to resize the narration. The
    repaired script is kept only if it is genuinely closer to target, so this can
    never make the result worse.
    """
    target = int(target_duration or 60)
    estimated = estimate_script_seconds(scenes)
    ratio = estimated / target if target else 1.0
    print(f"[LENGTH] Script ~{estimated:.0f}s vs target {target}s ({ratio:.0%})")

    if min_ratio <= ratio <= max_ratio:
        return scenes

    if status:
        try:
            status({
                "stage": "script_length",
                "estimated_seconds": round(estimated, 1),
                "target_seconds": target,
                "note": (f"script is ~{estimated:.0f}s for a {target}s request; "
                         "asking the model to resize the narration"),
            })
        except Exception:
            pass

    print(f"[LENGTH] Outside tolerance — one repair to reach ~{target}s")
    try:
        repair = service.generate(
            role="repair",
            system=_SYSTEM_INSTRUCTION,
            prompt=_build_length_repair_prompt(topic_name, target, scenes, estimated),
            provider=provider,
            client=client,
            response_schema=list[Scene],
        )
        resized = parse_script_from_text(repair.text).as_scene_dicts()
    except (ScriptValidationError, Exception) as exc:
        print(f"[LENGTH] Resize failed, keeping original script: {exc}")
        return scenes

    new_estimated = estimate_script_seconds(resized)
    # Keep the resize only if it moved us closer to the target.
    if abs(new_estimated - target) < abs(estimated - target):
        print(f"[OK] Script resized: ~{estimated:.0f}s -> ~{new_estimated:.0f}s "
              f"({len(resized)} scenes)")
        return resized

    print(f"[LENGTH] Resize did not improve (~{new_estimated:.0f}s); keeping original")
    return scenes


def generate_script(
    service: LLMService,
    topic_name: str,
    provider: str,
    client: Any = None,
    target_duration: int = 60,
    status: StatusCallback = None,
) -> List[dict]:
    """Generate and validate an educational script.

    Returns:
        A list of validated scene dicts (JSON-serializable) ready for the review
        UI, TTS and Manim generation.

    Raises:
        ScriptValidationError: if the provider output cannot be validated even
            after one repair attempt.
        llm_service.LLMError: if generation fails at the provider level.
    """
    target_duration = int(target_duration or 60)
    prompt = _build_prompt(topic_name, target_duration)

    result = service.generate(
        role="script",
        system=_SYSTEM_INSTRUCTION,
        prompt=prompt,
        provider=provider,
        client=client,
        response_schema=list[Scene],
    )

    try:
        script: VideoScript = parse_script_from_text(result.text)
        print(f"[OK] Script validated: {len(script.scenes)} scenes (model: {result.model})")
        return _enforce_target_duration(
            script.as_scene_dicts(), service, topic_name, provider, client,
            target_duration, status,
        )
    except ScriptValidationError as first_error:
        print(f"[VALIDATION] Script invalid, attempting one repair: {first_error}")

    # One controlled repair attempt using the current provider/repair model.
    repair_prompt = _build_repair_prompt(
        topic_name, target_duration, result.text, str(first_error)
    )
    repair_result = service.generate(
        role="repair",
        system=_SYSTEM_INSTRUCTION,
        prompt=repair_prompt,
        provider=provider,
        client=client,
        response_schema=list[Scene],
    )
    try:
        script = parse_script_from_text(repair_result.text)
        print(f"[OK] Script repaired and validated: {len(script.scenes)} scenes")
        return _enforce_target_duration(
            script.as_scene_dicts(), service, topic_name, provider, client,
            target_duration, status,
        )
    except ScriptValidationError as repair_error:
        raise ScriptValidationError(
            f"Script generation failed validation after one repair attempt: {repair_error}"
        ) from repair_error
