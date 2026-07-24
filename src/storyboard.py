"""Video-level visual-direction (storyboard) pass.

After the script is validated, one additional LLM request produces a complete,
Pydantic-validated storyboard that directs the *visual* design of every scene —
without writing any Manim Python. Diversity rules are enforced: a violated
storyboard triggers one repair request; residual violations are reported (not
fatal) so a job never fails purely for a heuristic layout repeat.
"""

from __future__ import annotations

import json
from typing import List, Optional

from llm_service import LLMService, StatusCallback
from schemas import (
    Storyboard,
    ScriptValidationError,
    gemini_storyboard_schema,
    parse_storyboard_from_text,
)
from collections import Counter

from domain_guidance import tag_menu_for_prompt
from visual_ledger import normalize

_SYSTEM = (
    "You are a senior visual director and information-design expert for animated "
    "educational videos. You plan bespoke, visually distinct scenes where the "
    "VISUAL carries the teaching. You never output code — only a JSON storyboard. "
    "Match the language of the script."
)


def required_distinct_approaches(target_duration: int, scene_count: int) -> int:
    """Minimum number of genuinely distinct visual approaches required.

    Anchored by the spec: a 60s video needs >=5, a 120s video needs >=8, with a
    linear ramp in between. Clamped to the number of scenes available.
    """
    target = int(target_duration or 60)
    if target <= 60:
        base = 5
    elif target >= 120:
        base = 8
    else:
        base = 5 + round((target - 60) / 20.0)  # 60->5 ... 120->8
    return max(1, min(base, max(1, scene_count)))


def canonical_continuity_mode(storyboard: Storyboard) -> str:
    """The video's single continuity mode, from a majority vote across scenes.

    ``continuity_mode`` is genuinely a video-level decision, but the flat
    ``list[StoryboardScene]`` schema Gemini's structured output actually
    returns (see :func:`schemas.gemini_storyboard_schema`) has no video-level
    slot to put it in, so it is asked for per-scene and canonicalized here.
    Ties, or an empty storyboard, resolve to "varied" — the safe default that
    matches the pre-existing diversity-first behavior.
    """
    scenes = storyboard.scenes
    if not scenes:
        return "varied"
    counts = Counter(s.continuity_mode for s in scenes)
    if counts.get("cumulative", 0) > counts.get("varied", 0):
        return "cumulative"
    return "varied"


def canonical_semantic_colors(storyboard: Storyboard) -> List[dict]:
    """The video's semantic color contract: first-wins per concept, order-stable."""
    from schemas import MAX_SEMANTIC_COLORS

    seen: dict = {}
    for scene in storyboard.scenes:
        for entry in scene.semantic_colors:
            key = normalize(entry.concept)
            if key and key not in seen:
                seen[key] = {"concept": entry.concept, "color": entry.color}
    return list(seen.values())[:MAX_SEMANTIC_COLORS]


def narrative_arc_notes(storyboard: Storyboard) -> List[str]:
    """Report-only observations about narrative-arc completeness (never fatal).

    A "hook" without a "resolution" is a question the video never answers —
    worth surfacing, but not worth failing a job over: many good videos are
    legitimately "standalone" throughout.
    """
    roles = [s.narrative_role for s in storyboard.scenes]
    notes: List[str] = []
    if "hook" in roles and "resolution" not in roles:
        notes.append(
            "scene(s) tagged 'hook' but no 'resolution' scene - the opening "
            "question may go unanswered"
        )
    if "misconception" in roles:
        idx_misc = roles.index("misconception")
        if not any(r in ("development", "resolution") for r in roles[idx_misc + 1:]):
            notes.append(
                "a 'misconception' scene has no following development/resolution "
                "scene to establish the correct model"
            )
    return notes


def check_diversity(storyboard: Storyboard, target_duration: int) -> List[str]:
    """Return a list of diversity-rule violations (empty == compliant).

    In "cumulative" continuity mode, intentional repetition of the SAME visual
    world across scenes is the correct behavior, not a violation — the
    adjacent-repeat and composition-reuse checks are skipped, and the
    distinct-approaches requirement drops to "at least one coherent visual
    world" rather than several distinct ones.
    """
    scenes = storyboard.scenes
    n = len(scenes)
    mode = canonical_continuity_mode(storyboard)
    metaphors = [normalize(s.visual_metaphor) for s in scenes]
    compositions = [normalize(s.composition) for s in scenes]
    violations: List[str] = []

    required = 1 if mode == "cumulative" else required_distinct_approaches(target_duration, n)
    distinct = len(set(m for m in metaphors if m))
    if distinct < required:
        violations.append(
            f"needs at least {required} distinct visual approaches, found {distinct}"
        )

    if mode == "cumulative":
        return violations

    for i in range(1, n):
        if metaphors[i] and metaphors[i] == metaphors[i - 1]:
            violations.append(
                f"scenes {i} and {i + 1} repeat the same visual metaphor"
            )
        if compositions[i] and compositions[i] == compositions[i - 1]:
            violations.append(
                f"scenes {i} and {i + 1} repeat the same composition/layout"
            )

    seen: dict = {}
    for c in compositions:
        if not c:
            continue
        seen[c] = seen.get(c, 0) + 1
    for comp, count in seen.items():
        if count > 2:
            violations.append(
                f"composition '{comp}' is used {count} times (max 2 unless the lesson requires it)"
            )

    return violations


# A video long enough to have several scenes usually has at least one that reads
# better with depth ordering. Below this length, an all-flat storyboard is
# unremarkable and gets no note.
_MIN_SCENES_FOR_DEPTH_NOTE = 4


def dimension_notes(storyboard: Storyboard) -> List[str]:
    """Report-only observations about dimensional variety.

    Deliberately NOT part of :func:`check_diversity`: these are observations,
    not rule violations, so they never trigger a repair request and can never
    fail a job. They are recorded alongside the storyboard so the effect of the
    2.5D guidance is visible in the artifacts.

    2.5D layering is cheap and render-safe (z-index + scale + opacity inside a
    normal Scene) and was historically never chosen, because the guidance used
    to push everything flat. An all-flat storyboard is still perfectly valid.
    """
    scenes = storyboard.scenes
    if len(scenes) < _MIN_SCENES_FOR_DEPTH_NOTE:
        return []
    dims = [(s.dimension or "2d") for s in scenes]
    if any(d in ("2.5d", "3d") for d in dims):
        return []
    return [
        f"all {len(scenes)} scenes are flat 2d — consider 2.5d layering for any "
        f"scene where depth ordering, stacking or overlay would genuinely clarify "
        f"the relationship (optional: leave flat if none would benefit)"
    ]


def _diversity_rules_block(target_duration: int, scene_count: int) -> str:
    required = required_distinct_approaches(target_duration, scene_count)
    return f"""SCENE DIVERSITY RULES (apply when you choose CONTINUITY MODE = varied,
see below; a video-wide choice of "cumulative" replaces these with the
CONTINUITY MODE guidance instead — that is correct, not a violation):
- Provide at least {required} genuinely DISTINCT visual approaches across the {scene_count} scenes.
- Never repeat the same visual metaphor or composition in ADJACENT scenes.
- Do not reuse the same composition/layout more than twice in the whole video.
- Do NOT plan generic floating cards, bullet lists, random arrows, static
  diagrams, decorative particles, or title screens as the MAIN explanation.
- Avoid text-dominant scenes unless the concept is a formula/definition/code.
- Every scene must show a meaningful transformation, comparison, flow,
  construction, decomposition, or interaction — the visual teaches, text only labels.
- When concepts are related, CARRY or TRANSFORM an object from the previous scene
  into the next instead of resetting to unrelated visuals."""


_NARRATIVE_ARC_GUIDANCE = """NARRATIVE ARC (use when the topic genuinely supports one; a survey of
several unrelated ideas can stay "standalone" throughout — do not force an arc):
- HOOK: open with ONE concrete, motivating question, puzzle, observation or real
  situation — never a generic "today we will learn about X" opener.
- SETUP: build the visual machinery/vocabulary needed to understand the hook.
- DEVELOPMENT: develop the mechanism step by step. May include MISCONCEPTION: show
  a plausible WRONG mental model, make its failure visibly break on screen, then
  replace it with the correct one — only when a real misconception exists for this
  topic; never invent one artificially for drama.
- RESOLUTION: return to the hook's exact question and visibly ANSWER it with the
  machinery just built — a genuine payoff, not a restated narration line.
- RECAP: optional short closing summary.
Tag each scene's narrative_role accordingly, or "standalone" if no arc applies to
that scene. For A-level topics, prefer a concrete example or physical situation
BEFORE the general abstraction, when that ordering helps understanding.

SCENE KIND (the FORM a scene takes — independent of its narrative_role):
- "explanation" (the default): the scene teaches an idea through a visual
  metaphor, construction or transformation. Most scenes are this.
- "worked_problem": the scene works a SPECIFIC question with REAL NUMBERS
  through to an answer — the A-level "now do one" scene. Choose this when the
  script's narration is actually solving something (a distance, a force, an
  angle, a concentration), not explaining what a thing is.
  When you plan a worked_problem scene, make it concrete in the storyboard:
  primary_objects should include the actual diagram AND the labelled known
  values; visual_beats should follow the stages of the calculation (mark what
  is given, mark what is asked, choose the relation, substitute, solve, put the
  answer back on the diagram) rather than being generic reveals.
- Aim for AT MOST 1-2 worked_problem scenes in a video, and only when the topic
  genuinely calls for one — a video that is all worked examples stops teaching
  the idea. If the user's request explicitly asks for a worked example / a
  problem / "solve", you SHOULD include one.

CONTINUITY MODE (a VIDEO-WIDE choice — pick the SAME value for every scene):
- "cumulative": ONE coherent visual world (a graph, vector space, field, circuit,
  geometric object, physical system) builds and TRANSFORMS across scenes rather
  than resetting. Choose this for a topic that is fundamentally one idea unfolding:
  deriving something step by step, building up a single graph, transforming a
  vector/matrix, explaining one mechanism, developing one physical model. Keep a
  visual idea alive while it is still teaching something; change the
  representation only when the idea itself changes. If a representation stops
  teaching, transition to a new one deliberately rather than keeping it as clutter.
- "varied": genuinely DISTINCT visual metaphors across scenes, per the SCENE
  DIVERSITY RULES above. Choose this for a survey of several loosely related
  ideas, a comparison of unrelated cases, or any topic where forcing one visual
  world would confuse rather than clarify.
Do not force cumulative onto every video, and do not force variety onto a topic
that is naturally one continuous idea.

SEMANTIC COLOR CONTRACT (a VIDEO-WIDE choice — the SAME list on every scene):
- Choose 0-6 concept -> color bindings ONLY where color would genuinely help the
  viewer track a distinction across the whole video — e.g. force vs velocity,
  electric field vs current, input vs output, variable vs constant, original vs
  transformed. Do not assign a color where it would not help; an empty list is
  correct for a video with no such recurring distinction.
- "color" must be a real Manim color NAME (e.g. GOLD_A, TEAL_D, BLUE_D) or a
  #RRGGBB hex code. Reuse the SAME binding in every scene where that concept
  appears — this is what makes a series feel authored rather than improvised."""


def _build_prompt(topic: str, scenes: List[dict], target_duration: int, global_style: str) -> str:
    tag_menu = tag_menu_for_prompt()
    scene_lines = []
    for i, s in enumerate(scenes, 1):
        scene_lines.append(
            f"Scene {i}: chapter='{s.get('chapter', '')}'; narration='{s.get('text', '')}'; "
            f"objective='{s.get('objective', '')}'; concept='{s.get('explanation', '')}'"
        )
    scenes_block = "\n".join(scene_lines)
    n = len(scenes)

    return f"""Design a complete VISUAL STORYBOARD for this {target_duration}s educational
video about: {topic}

GLOBAL VISUAL STYLE CONTRACT (all scenes must share this language, but must NOT look identical):
{global_style}

{_diversity_rules_block(target_duration, n)}

THE SCRIPT (one storyboard entry per scene, {n} scenes total):
{scenes_block}

For EACH scene produce a storyboard entry with these fields:
- index (1-based, matching the script order)
- learning_goal
- key_concept
- visual_metaphor  (a UNIQUE concrete metaphor; different from other scenes)
- composition       (how the frame is laid out)
- primary_objects   (list of the main on-screen objects)
- primary_motion    (the main transformation/animation the viewer sees)
- color_role        (how color carries meaning, within the palette)
- camera_plan       (optional; framing notes if relevant, else null)
- transition_from_prev (how this scene grows out of the previous scene)
- on_screen_text    (optional; only short labels that must appear, else null)
- anti_repetition_notes (explicitly how this scene differs from earlier scenes)
- visual_complexity (low | medium | high — match the narration length)
- dimension        (2d | 2.5d | 3d — see DIMENSIONALITY below; default 2d)
- primary_domain_tag    (EXACTLY ONE tag — see DOMAIN TAGS below)
- secondary_domain_tags (list of 0-2 further tags, or [] — see DOMAIN TAGS below)
- narrative_role   (hook | setup | development | misconception | resolution |
                    recap | standalone — see NARRATIVE ARC below; default standalone)
- scene_kind       (explanation | worked_problem — see SCENE KIND below;
                    default explanation)
- continuity_mode  (cumulative | varied — SAME value for every scene — see
                    CONTINUITY MODE below; default varied)
- semantic_colors  (list of 0-6 {{"concept": "...", "color": "..."}} objects, SAME
                    list for every scene — see SEMANTIC COLOR CONTRACT below)
- opening_state    (what is already on screen as the scene begins)
- visual_beats     (ORDERED list of the meaningful changes that make the scene progress;
                    each beat is {{"at_seconds": <number>, "action": "<what visibly
                    changes>", "objects": ["..."]}} — plan a beat roughly every 2-4
                    seconds so the scene NEVER freezes, and put the last beat near, but
                    not at, the end)
- transformations  (list of the meaningful transformations the viewer sees)
- ending_state     (the clean FINAL FRAME the scene settles into. Be concrete and
                    spatial — name the object(s) still on screen and WHERE and at
                    roughly what SIZE they sit, e.g. "the parabola remains, shifted
                    left and about half-size, its vertex labelled" — not just a mood
                    or a motion. The next scene is told to open by matching this
                    frame, so vague endings break the join between scenes)
- continuity_notes (which specific object carries forward into the next scene)

DOMAIN TAGS (these decide which specialist visual guidance the animator receives
for this scene, so choose from the scene's actual learning goal, narration,
visual metaphor and beats — not from surface words in the topic title):
{tag_menu}

Rules for tagging:
- Use EXACTLY ONE primary_domain_tag: the domain that governs what the viewer
  must SEE in this scene.
- REAL TOPICS OVERLAP, and this is the normal case — an A-level physics question
  is frequently maths-heavy, and a maths topic is frequently taught through a
  physical situation. Add a secondary tag whenever the scene GENUINELY draws on
  that domain's visual conventions too. Under-tagging a cross-domain scene is a
  worse mistake than adding one honest extra tag: the animator will simply lack
  the conventions it needs.
- The test for a secondary tag is: "does this change what has to be DRAWN?" If
  yes, include it. If the scene would look identical without it, leave it out.
- Up to 3 secondary tags (4 distinct tags total). Most scenes need 1-2 in all.
- Use "general" — alone, never combined — for an introduction, a recap, a
  framing question, or any scene that needs no specialist visual convention.
- Do NOT add a tag merely because a related word appears in the narration.
- Only use tags from the list above; any other value is invalid.

Worked examples:
- "A matrix rotates and stretches a vector" -> primary linear_algebra, secondary [geometry]
- "Deriving how the electric field varies along a wire" -> primary electricity, secondary [calculus]
- "Induction from a rotating coil" -> primary magnetism, secondary [electricity, mechanics]
- "Deriving SHM for a pendulum using the small-angle approximation and energy"
  -> primary mechanics, secondary [calculus, geometry] (all three are actually drawn)
- "Wave interference patterns" -> primary waves, secondary [] (add geometry only if
  the path-difference construction is actually drawn)
- "Introducing the lesson and its central question" -> primary general, secondary []

DIMENSIONALITY (choose what TEACHES best — depth is a tool, not a reward):
- 2d suits most explanatory work: graphs, equations, 2D vectors, force diagrams,
  circuits, processes, comparisons, probability and most abstract ideas.
- 2.5d (layered 2D) is UNDER-USED and is often the strongest upgrade available.
  It is a normal flat scene that uses depth ORDERING — z-index, foreground vs
  background, scale and opacity hierarchy, deliberate occlusion. Choose it when
  layering, stacking, nesting, overlaying, or "what sits on top of / behind
  what" genuinely clarifies the relationship. A flat scene that LIFTS into
  layered depth at the moment depth starts to explain something is both clearer
  and more engaging than staying flat throughout.
- 3d is for when spatial structure IS the concept and 2d would misrepresent it:
  3D geometry/volume, rotation about a spatial axis, vectors/planes in 3-space,
  orbital motion, a genuinely spatial field. Not for looks.
- ACROSS THE WHOLE VIDEO, aim for dimensional variety rather than a uniformly
  flat set of scenes: where the topic supports it, 1-2 scenes should use 2.5d.
  This is a guideline, not a quota — never push a scene into layered depth that
  does not benefit from it, and never choose 3d merely to add variety.
- The one hard rule: depth must EXPLAIN something. Depth added because it looks
  impressive is wrong in every dimension.

{_NARRATIVE_ARC_GUIDANCE}

Also produce a "global_style" object: palette (color names), typography, pacing, spacing, mood.

OUTPUT FORMAT: respond ONLY with a JSON object:
{{"global_style": {{...}}, "scenes": [ {{...}}, ... ]}}
Do NOT output any Manim or Python code. Keep each field concise (one or two sentences)."""


def _build_repair_prompt(base_prompt: str, prev_output: str, violations: List[str]) -> str:
    vlist = "\n".join(f"- {v}" for v in violations)
    return f"""Your previous storyboard violated the diversity rules:
{vlist}

Fix ONLY what is needed to satisfy every rule (make adjacent scenes use different
metaphors and compositions, increase the number of distinct visual approaches,
and avoid reusing any layout more than twice). Keep the good ideas; diversify the
rest.

{base_prompt}"""


def generate_storyboard(
    service: LLMService,
    topic: str,
    scenes: List[dict],
    provider: str,
    target_duration: int,
    global_style: str,
    client=None,
    status: StatusCallback = None,
) -> Storyboard:
    """Generate, validate, and diversity-check the storyboard (one repair max).

    Raises:
        ScriptValidationError: if the storyboard cannot be validated even after a
            repair attempt.
    """
    prompt = _build_prompt(topic, scenes, target_duration, global_style)
    result = service.generate(
        role="storyboard", system=_SYSTEM, prompt=prompt, provider=provider,
        client=client, response_schema=gemini_storyboard_schema(),
    )

    try:
        storyboard = parse_storyboard_from_text(result.text)
    except ScriptValidationError as first_error:
        print(f"[STORYBOARD] Invalid, attempting one repair: {first_error}")
        repair = service.generate(
            role="storyboard", system=_SYSTEM,
            prompt=_build_repair_prompt(prompt, result.text, [str(first_error)]),
            provider=provider, client=client, response_schema=gemini_storyboard_schema(),
        )
        storyboard = parse_storyboard_from_text(repair.text)  # may raise -> caller fails safely

    violations = check_diversity(storyboard, target_duration)
    if violations:
        print(f"[STORYBOARD] Diversity violations, one repair: {violations}")
        try:
            repair = service.generate(
                role="storyboard", system=_SYSTEM,
                prompt=_build_repair_prompt(prompt, result.text, violations),
                provider=provider, client=client, response_schema=gemini_storyboard_schema(),
            )
            repaired = parse_storyboard_from_text(repair.text)
            # Keep the repaired version only if it is at least as diverse.
            if len(check_diversity(repaired, target_duration)) <= len(violations):
                storyboard = repaired
                violations = check_diversity(storyboard, target_duration)
        except ScriptValidationError as exc:
            print(f"[STORYBOARD] Repair invalid, keeping original: {exc}")

    storyboard._residual_violations = violations  # type: ignore[attr-defined]
    # Report-only: recorded for visibility, never a repair trigger or a failure.
    dim_notes = dimension_notes(storyboard)
    storyboard._dimension_notes = dim_notes  # type: ignore[attr-defined]
    dims = {}
    for s in storyboard.scenes:
        key = s.dimension or "2d"
        dims[key] = dims.get(key, 0) + 1
    print(f"[STORYBOARD] Dimensions: "
          + ", ".join(f"{k}={v}" for k, v in sorted(dims.items()))
          + (f" | {dim_notes[0]}" if dim_notes else ""))

    # Video-level continuity mode + semantic colors, canonicalized from the
    # per-scene fields (see canonical_continuity_mode's docstring for why this
    # is derived rather than a top-level schema field). Report-only.
    mode = canonical_continuity_mode(storyboard)
    colors = canonical_semantic_colors(storyboard)
    arc_notes = narrative_arc_notes(storyboard)
    storyboard._continuity_mode = mode  # type: ignore[attr-defined]
    storyboard._semantic_colors = colors  # type: ignore[attr-defined]
    storyboard._narrative_arc_notes = arc_notes  # type: ignore[attr-defined]
    roles_used = sorted({s.narrative_role for s in storyboard.scenes} - {"standalone"})
    print(f"[STORYBOARD] Continuity mode: {mode}"
          + (f" | semantic colors: {', '.join(c['concept'] for c in colors)}" if colors else "")
          + (f" | narrative roles: {', '.join(roles_used)}" if roles_used else "")
          + (f" | {arc_notes[0]}" if arc_notes else ""))

    print(f"[OK] Storyboard: {len(storyboard.scenes)} scenes, "
          f"{len(set(normalize(s.visual_metaphor) for s in storyboard.scenes))} distinct metaphors, "
          f"{len(violations)} residual diversity notes (model: {result.model})")
    return storyboard


def save_storyboard(storyboard: Storyboard, path) -> None:
    """Persist the storyboard (with any residual diversity notes) to disk."""
    from pathlib import Path

    data = storyboard.as_dict()
    data["residual_diversity_notes"] = getattr(storyboard, "_residual_violations", [])
    data["dimension_notes"] = getattr(storyboard, "_dimension_notes", [])
    data["dimension_summary"] = {
        s.index: (s.dimension or "2d") for s in storyboard.scenes
    }
    data["domain_routing_summary"] = {
        s.index: s.domain_tags() for s in storyboard.scenes
    }
    data["continuity_mode"] = getattr(storyboard, "_continuity_mode", canonical_continuity_mode(storyboard))
    data["semantic_colors"] = getattr(storyboard, "_semantic_colors", canonical_semantic_colors(storyboard))
    data["narrative_arc_notes"] = getattr(storyboard, "_narrative_arc_notes", narrative_arc_notes(storyboard))
    data["narrative_role_summary"] = {
        s.index: s.narrative_role for s in storyboard.scenes
    }
    data["scene_kind_summary"] = {
        s.index: s.scene_kind for s in storyboard.scenes
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
