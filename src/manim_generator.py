"""Manim code generation + REPL-style repair.

Generates Manim ``Scene`` code for each fragment via the centralized
:class:`~llm_service.LLMService` using the *animation* role (a high-quality
model such as Gemini 3.6 Flash) and repairs compilation failures using the
*repair* role. All responses are validated with the :class:`~schemas.ManimCode`
model before being written to disk.
"""

from __future__ import annotations

from typing import Any, Optional

from dotenv import load_dotenv

from domain_guidance import build_domain_section, normalize_tags
from llm_service import LLMError, LLMService, StatusCallback
from schemas import ManimCode, ScriptValidationError, parse_manim_code_from_text

load_dotenv()

_GEN_SYSTEM = (
    "You are an expert Manim Community Edition (v0.19.1) animator who designs "
    "educational explanations in the spirit of 3Blue1Brown: the visual carries the "
    "teaching, ideas evolve continuously on screen, and objects transform into one "
    "another rather than being deleted and rebuilt. You write clean, correct, "
    "runnable Python. NEVER use self.camera.frame in Scene. NEVER use MathTex, Tex, "
    "or any LaTeX-based rendering. Always respond in valid JSON format."
)
_FIX_SYSTEM = (
    "You are an expert debugger for Manim Community Edition (v0.19.1). You make the "
    "smallest change that fixes the error while PRESERVING the scene's visual "
    "meaning, motion and teaching intent. Never reduce a broken animated scene to a "
    "static text slide or a generic diagram just to make it compile. Always respond "
    "in valid JSON format."
)


def _context_section(previous_context: Optional[dict], continuity_mode: str = "varied") -> str:
    """Compact previous-scene summary (NOT the full prior source code).

    ``continuity_mode`` (a video-wide choice — see schemas.CONTINUITY_MODES)
    changes how strongly this scene is told to carry the previous one forward.
    In "cumulative" mode the SAME visual world should persist by default;
    "varied" keeps the existing judgement-call framing.
    """
    if previous_context:
        if continuity_mode == "cumulative":
            continuity_rule = """CONTINUITY MODE: cumulative — this video is building ONE coherent visual
world across its scenes (a graph, vector space, field, circuit, geometric
object, or physical system), not a new picture per scene.
- DEFAULT to opening this scene by re-showing and TRANSFORMING the previous
  scene's ending object — keep the SAME visual world alive, evolving it rather
  than replacing it, as long as it is still teaching something.
- Only drop the current visual world and start a new one when the IDEA itself
  has genuinely changed and the old representation would stop teaching
  anything if kept — a deliberate transition, not a reset for variety's sake.
- Preserve object identity where it matters: the same curve/vector/shape that
  represented a quantity earlier should still represent it now, evolved."""
        else:
            continuity_rule = """CONTINUITY (make this feel like ONE film, not a slideshow):
- If the previous scene ended with a concrete object (a curve, a vector, a shape,
  an equation), strongly prefer OPENING this scene by re-showing or TRANSFORMING
  that object into this scene's first element — a visible hand-off, not a cut.
- Only start from a blank canvas when this scene is a genuinely new idea that needs
  a fresh metaphor. If it continues the SAME thread, carry the object across.
- Use judgement: continuity should serve understanding, never force an awkward link."""
        carry = previous_context.get("carry_forward") or ""
        carry_line = f"\n- Element intended to carry forward: {carry}" if carry else ""
        return f"""
PREVIOUS SCENE SUMMARY (for continuity — carry/transform an element forward, do not reset):
- Previous narration: {previous_context.get('text', 'N/A')}
- Previous visual metaphor: {previous_context.get('metaphor', 'N/A')}
- Previous ENDING FRAME (what was on screen as that scene stopped): {previous_context.get('ending_state', 'N/A')}{carry_line}

MATCH-CUT THE JOIN (this is what makes consecutive scenes feel like one film):
- Open this scene with the carried-over element at approximately the POSITION,
  SCALE and COLOUR it had in the previous ending frame above — then move it to
  where THIS scene needs it as your first beat. The viewer's eye stays locked on
  one object across the cut instead of re-finding a new layout.
- Concretely: place it first (.move_to/.scale to match the described ending
  frame), add it to the scene, and let the first self.play(...) carry it into
  this scene's arrangement. Do NOT fade the old element out and fade a fresh one
  in at a different size somewhere else — that is the cut this rule exists to
  avoid.
- If this scene genuinely starts a NEW visual idea, a clean break is correct —
  but then make the break deliberate and complete, not a half-match.

{continuity_rule}
"""
    return "\nCONTEXT: This is the FIRST scene of the video. Establish the visual language.\n"


def _format_beats(beats) -> str:
    """Render storyboard visual beats as an explicit timed checklist."""
    if not beats:
        return "    (none supplied — invent 2+ meaningful beats spread across the scene)"
    lines = []
    for i, beat in enumerate(beats, 1):
        if not isinstance(beat, dict):
            continue
        at = beat.get("at_seconds")
        when = f"~{float(at):.1f}s" if isinstance(at, (int, float)) else "spread evenly"
        objs = ", ".join(str(o) for o in (beat.get("objects") or []))
        lines.append(f"    {i}. [{when}] {beat.get('action', '')}"
                     + (f"  (objects: {objs})" if objs else ""))
    return "\n".join(lines) or "    (none supplied — invent 2+ meaningful beats)"


_NARRATIVE_ROLE_NOTES = {
    "hook": (
        "This is the video's OPENING HOOK. Present ONE concrete, motivating "
        "question, puzzle or observation — never a generic 'today we will "
        "learn about X'. End on that open question, unresolved: the payoff "
        "comes later, in the resolution scene."
    ),
    "setup": (
        "This scene BUILDS THE MACHINERY the video needs before it can answer "
        "its opening hook — establish the objects/vocabulary the later scenes "
        "will reuse and transform, not the answer itself yet."
    ),
    "development": (
        "This scene DEVELOPS THE MECHANISM introduced earlier, one step at a "
        "time. Build directly on what the previous scene established."
    ),
    "misconception": (
        "This scene shows a PLAUSIBLE WRONG mental model, then makes its "
        "failure VISIBLE on screen (not just stated), then transitions to the "
        "correct model. Do not soften the wrong model into something obviously "
        "silly — it must be a real, plausible mistake."
    ),
    "resolution": (
        "This scene RETURNS TO THE VIDEO'S OPENING HOOK and visibly ANSWERS "
        "it using the machinery built in earlier scenes — the payoff must be "
        "SHOWN via the visual, not merely restated in narration."
    ),
    "recap": (
        "This is a closing RECAP — briefly bring back the key visual objects "
        "from earlier scenes into one composed summary."
    ),
}


# Injected ONLY for scene_kind == "worked_problem". Deliberately domain-neutral:
# the same staging serves a trig distance question, a suvat problem, a titration
# or a capacitor calculation, so it lives here rather than inside one domain
# module. It is also the one place the "avoid text on screen" rule is relaxed —
# a calculation that never shows its working teaches nothing.
_WORKED_PROBLEM_STAGING = """SCENE KIND: WORKED PROBLEM — stage this scene as a CALCULATION, not as a
metaphor. Work these stages in order, each as a real visual beat:
1. GIVEN — draw the diagram/situation and mark the KNOWN values ON it (value +
   unit beside the thing they measure, angle values inside their arc). The
   numbers belong on the picture, not in a separate list.
2. ASKED — mark the unknown ON the same diagram with a clearly styled "x" or
   "?" in an accent colour, so the goal is visible from the start.
3. CHOOSE — make the CHOICE OF METHOD visible, because that is the actual skill
   being taught: dim the quantities not involved (.animate.set_opacity(0.3))
   so the ones that remain lit are what select the relation/formula. Name the
   relation only after the quantities have selected it.
4. SUBSTITUTE — THE KEY MOVE: the numbers must TRAVEL FROM THE DIAGRAM INTO THE
   EQUATION using TransformFromCopy(label_on_diagram, equation). The viewer
   WATCHES the 5 cm on the triangle become the 5 in the formula. Never simply
   fade in a finished equation — that is the difference between showing a
   calculation and asserting one.
5. SOLVE — TransformFromCopy each line of working into the next so the
   rearrangement is continuous, keeping a STABLE colour per quantity. One or
   two steps is plenty; do not show every algebraic micro-step.
6. ANSWER — send the result BACK to the diagram, replacing the "x"/"?" it was
   asked for, WITH ITS UNITS, and restore anything that was dimmed. The scene
   ends on the answered picture, not on a floating number.
RULES SPECIFIC TO THIS SCENE KIND:
- The diagram STAYS ON SCREEN throughout. Never cut away to a full-screen wall
  of algebra — put the working beside the diagram and keep both visible.
- An equation on screen is REQUIRED here and does NOT count as a "text slide";
  the general preference for minimal on-screen text is relaxed for the working
  itself. Everything else (no headings, no bullet lists, no title cards) still
  applies.
- Use real, sensible numbers and real units. A worked problem with invented or
  dimensionally inconsistent quantities is a failed scene even if it renders.
- Keep quantity colours consistent between the diagram and the equation — that
  correspondence is what makes the substitution readable."""


def _scene_kind_section(storyboard_entry: Optional[dict]) -> str:
    """Layer B: staging guidance for a non-default scene form.

    Silent for the default "explanation" kind — most scenes need no extra
    direction here and must not pay for it.
    """
    if not storyboard_entry:
        return ""
    if (storyboard_entry.get("scene_kind") or "explanation") == "worked_problem":
        return "\n" + _WORKED_PROBLEM_STAGING + "\n"
    return ""


def _narrative_role_section(storyboard_entry: Optional[dict]) -> str:
    """Guidance for this scene's place in the video's narrative arc.

    Empty for "standalone" (the default) — most scenes need no arc framing at
    all, and this must stay silent for them rather than adding noise.
    """
    if not storyboard_entry:
        return ""
    role = storyboard_entry.get("narrative_role") or "standalone"
    note = _NARRATIVE_ROLE_NOTES.get(role)
    if not note:
        return ""
    return f"\nNARRATIVE ROLE — {role.upper()}: {note}\n"


def _semantic_color_section(storyboard_entry: Optional[dict]) -> str:
    """This video's concept -> color contract, if one was chosen.

    Empty when no contract was set (the correct choice for a video with no
    recurring distinction worth coloring) — never invents one.
    """
    if not storyboard_entry:
        return ""
    colors = storyboard_entry.get("semantic_colors") or []
    if not colors:
        return ""
    lines = []
    for entry in colors:
        if isinstance(entry, dict) and entry.get("concept") and entry.get("color"):
            lines.append(f"  - {entry['concept']}: {entry['color']}")
    if not lines:
        return ""
    return (
        "\nSEMANTIC COLOR CONTRACT (this video's stable concept->color bindings — "
        "use these EXACT colors for these EXACT concepts, every time they appear; "
        "do not use these colors for anything else):\n" + "\n".join(lines) + "\n"
    )


def _visual_direction_section(
    storyboard_entry: Optional[dict],
    global_style: Optional[str],
    ledger_summary: Optional[str],
) -> str:
    if not storyboard_entry and not global_style and not ledger_summary:
        return ""
    continuity_mode = (storyboard_entry or {}).get("continuity_mode") or "varied"
    parts = ["\n================ VISUAL DIRECTION (follow this precisely) ================"]
    if global_style:
        parts.append(f"\nGLOBAL STYLE CONTRACT (shared by every scene, but scenes must NOT look identical):\n{global_style}")
    if storyboard_entry:
        objs = ", ".join(str(o) for o in (storyboard_entry.get("primary_objects") or []))
        if continuity_mode == "cumulative":
            anti_rep_line = (
                "Anti-repetition notes: N/A in cumulative mode — this video "
                "intentionally carries ONE visual world across scenes; see "
                "CONTINUITY MODE above, not this field"
            )
        else:
            anti_rep_line = (
                f"Anti-repetition notes (MUST differ from earlier scenes): "
                f"{storyboard_entry.get('anti_repetition_notes', 'N/A')}"
            )
        parts.append(f"""
THIS SCENE'S STORYBOARD (the visual is the teaching; text only labels it):
- Learning goal: {storyboard_entry.get('learning_goal', 'N/A')}
- Key concept: {storyboard_entry.get('key_concept', 'N/A')}
- Visual metaphor (make it concrete + unique): {storyboard_entry.get('visual_metaphor', 'N/A')}
- Composition / layout: {storyboard_entry.get('composition', 'N/A')}
- Primary objects: {objs or 'N/A'}
- Primary motion / transformation: {storyboard_entry.get('primary_motion', 'N/A')}
- Color role: {storyboard_entry.get('color_role', 'N/A')}
- Camera / framing: {storyboard_entry.get('camera_plan') or 'default framing'}
- Transition from previous scene: {storyboard_entry.get('transition_from_prev', 'N/A')}
- On-screen text (labels only, keep short): {storyboard_entry.get('on_screen_text') or 'minimal'}
- Visual complexity: {storyboard_entry.get('visual_complexity', 'medium')}
- DIMENSION for this scene: {storyboard_entry.get('dimension') or '2d (default — write a normal Scene unless the concept is unavoidably spatial)'}
- CONTINUITY MODE for this video: {continuity_mode}
- {anti_rep_line}
- Opening visual state: {storyboard_entry.get('opening_state') or 'establish the scene naturally'}
- Ending visual state: {storyboard_entry.get('ending_state') or 'a clean, settled frame'}
- Meaningful transformations: {', '.join(storyboard_entry.get('transformations') or []) or 'derive from the primary motion'}
- Continuity to next scene: {storyboard_entry.get('continuity_notes') or 'n/a'}
- TIMED VISUAL BEATS (implement each as a real animation at about this time):
{_format_beats(storyboard_entry.get('visual_beats'))}""")
        kind_section = _scene_kind_section(storyboard_entry)
        if kind_section:
            parts.append(kind_section)
        role_section = _narrative_role_section(storyboard_entry)
        if role_section:
            parts.append(role_section)
        color_section = _semantic_color_section(storyboard_entry)
        if color_section:
            parts.append(color_section)
    if ledger_summary and continuity_mode != "cumulative":
        parts.append(f"""
ALREADY-USED VISUAL CHOICES (DO NOT repeat these metaphors/layouts; be distinct):
{ledger_summary}""")
    parts.append("========================================================================\n")
    return "\n".join(parts)


_PRIMITIVES_NOTE = """OPTIONAL TOOLKIT (a helper module is importable — use it ONLY where it helps, never to make scenes look the same):
`from visual_primitives import *` provides: PALETTE (color roles), styled_title, body_text,
caption, make_node, make_box, connect, token_chip, prob_bar, highlight, row, column, grid,
fit_to_frame, morph, reveal, clear_scene. These are low-level building blocks; compose them
into a BESPOKE scene — do not fall back to a generic diagram. Writing everything from scratch
with plain Manim is equally acceptable."""


# Every construct below was render-verified in this exact environment (Manim
# 0.19.1) before being permitted. Fragile/reactive APIs are excluded on purpose:
# reliability outranks expressiveness.
_ANIMATION_VOCABULARY = """ANIMATION VOCABULARY (use what the IDEA needs — none of these are mandatory):

RELIABLE CORE — always available:
  Write, Create, DrawBorderThenFill, FadeIn, FadeOut, GrowFromCenter,
  Transform, ReplacementTransform, and .animate (e.g. obj.animate.shift(RIGHT))

EXPRESSIVE ADDITIONS — all verified to work here; use where they carry meaning:
  - FadeIn(obj, shift=UP) / FadeOut(obj, shift=DOWN) — directional entry/exit
    reads as motion rather than a hard cut.
  - LaggedStart(a, b, c, lag_ratio=0.2) — sequential reveal of RELATED items;
    ideal for accumulation, composition, ordering, or listing parts of a whole.
  - AnimationGroup(a, b) — coordinated simultaneous motion showing a relationship.
  - TransformFromCopy(src, dst) — a value/expression/element BECOMES its next
    form while the original stays. The single best move for derivations.
  - Indicate(obj) / Circumscribe(obj) — brief emphasis on the ONE thing being
    explained right now.
  - Flash(point) / ShowPassingFlash(line_copy) — a signal, pulse, propagation or
    connection travelling; use for events and flow.
  - GrowArrow(arrow) — an arrow that means direction, causality or flow.
  - MoveAlongPath(dot, path) — something travelling through a process/route.
  - obj.animate.move_to(p) along an ArcBetweenPoints(start, end, angle=..) path,
    or MoveAlongPath(obj, ArcBetweenPoints(...)) — a short CURVED move reads as
    flow, trajectory or transfer far better than a straight line when that is
    literally what is happening (a value moving between stores, a transfer
    between representations). Use it because the motion IS curved, never as
    decoration on an otherwise straight-line move.
  - Rotate(obj, angle=PI) — ONLY when rotation, cycling, orientation or
    periodicity is genuinely part of the concept.
  - Succession(a, b) — strictly staged actions representing a real sequence.
  - rate_func — match pacing to what's happening: smooth (default, natural),
    linear (constant/mechanical motion), there_and_back (temporary emphasis),
    rush_into (building toward a result), rush_from (a result unfolding fast).

GRAPHING (Axes/plots — for math, physics, statistics; no reactive tracking needed).
THESE ARE THE ONLY Axes METHODS THAT EXIST. Do not invent others — a guessed
method name is the #1 cause of a failed render:
  - axes = Axes(x_range=[a,b], y_range=[c,d], x_length=.., y_length=..)
  - axes.plot(lambda x: ..., x_range=[a,b], color=..)      -> a curve
  - axes.plot_parametric_curve(lambda t: [x(t), y(t), 0], t_range=[a,b])
  - axes.c2p(x, y)    coords -> screen point   (use this to PLACE objects)
  - axes.p2c(point)   screen point -> coords
  - axes.i2gp(x, graph)  x-value -> the point ON that graph
  - axes.get_area(graph, x_range=[a,b], color=.., opacity=..)
  - axes.get_riemann_rectangles(graph, x_range=[a,b], dx=.., stroke_width=1)
  - axes.get_graph_label(graph, label=Text(".."), x_val=.., direction=UR)
  - axes.get_axis_labels(x_label=Text(".."), y_label=Text(".."))
  - axes.get_vertical_line(point)      axes.get_secant_slope_group(...)
  Usage notes:
  - Animate a curve appearing with Create(graph) — it draws itself over run_time.
  - Transform one axes.plot(...) into another to show a function CHANGING.
  - Place every dot/line/label via axes.c2p(...) so it lands in graph space.
  - A dot that continuously FOLLOWS a moving point can use MoveAlongPath (no
    tracker needed), OR the narrow CONTROLLED CONTINUOUS MOTION pattern below
    when something else must stay coupled to it (a readout, a tangent, a
    changing area) — see that section before reaching for a tracker.
  KEEP GRAPH SCENES MOVING — a plot that just draws itself and then freezes is a
  slideshow. After Create(graph), the scene MUST keep evolving. Verified patterns
  (all render correctly — pick what the concept needs, do not use all at once):
  - Trace the curve: dot = Dot().move_to(axes.i2gp(x0, graph)); then
    self.play(MoveAlongPath(dot, graph), run_time=..) — reads as motion ALONG
    the function (rate of change, a particle's path, "as x increases...").
  - Sweep a read-out line across: play Create on successive
    axes.get_vertical_line(axes.i2gp(x_i, graph)) at a few x values to show how
    the value changes.
  - Rate of change: Create a wide secant via get_secant_slope_group(x, graph,
    dx=1.0, ...), then Transform it into a narrow one (dx=0.01) — the gradient
    "settling" onto the tangent.
  - Accumulation: Transform a coarse get_riemann_rectangles into a finer one
    (smaller dx), or grow get_area from x_range=[a,a] out to [a,b].
  A moving dot changes few pixels but is genuinely engaging — do not mistake a
  small, purposeful motion for "nothing happening".

2.5D LAYERING (stay in a normal Scene — this is NOT a 3D scene):
  - obj.set_z_index(n) — higher n draws in front; use for foreground/background.
  - Combine with scale (nearer = larger), opacity (farther = dimmer), and
    placement to imply depth ONLY when ordering/occlusion clarifies a relationship.
  - Animate one layer sliding over/under another to reveal ordering.

CONTROLLED CONTINUOUS MOTION (ValueTracker + always_redraw — narrow, verified,
NOT a general license for reactive code). Use ONLY when continuous coupled
change teaches a relationship that a discrete Transform/MoveAlongPath genuinely
cannot — e.g. a readout, tangent, or guide line that must stay ATTACHED to a
moving point as it moves. AT MOST ONE ValueTracker per scene, driving up to a
few always_redraw() mobjects — that is still ONE controlled pattern, not
several. Render-verified patterns (pick the ONE this scene needs):
  - Point + coupled readout/guide: a Dot tracking a curve while a
    get_vertical_line-style guide and/or a Text readout move WITH it —
    `t = ValueTracker(x0)`; `dot = always_redraw(lambda: Dot(ax.i2gp(t.get_value(), graph)))`;
    animate with `self.play(t.animate.set_value(x1), run_time=...)`.
  - Changing tangent/secant: an always_redraw Line recomputed from the
    tracker's value at each frame, sliding along a curve to show slope
    changing continuously (the discrete alternative — Transform between two
    fixed secants — is often CLEARER for a single before/after comparison;
    prefer the tracker only when the continuous sweep itself is the point).
  - A parameter reshaping a curve/vector/construction: `k = ValueTracker(k0)`
    driving `always_redraw(lambda: axes.plot(lambda x: k.get_value() * f(x)))`
    so the viewer sees the shape respond to the value changing (measured
    render cost is meaningfully higher than a static plot — keep this to ONE
    reactive plot per scene, and prefer a shorter run_time as a result).
  - Rotating mechanism with a coupled effect: an angle ValueTracker driving
    both the rotating object (via always_redraw or repeated .animate) and one
    coupled readout (e.g. a bar showing an induced/derived quantity respond to
    the angle) — for a rotating coil and induced current, or similar
    continuously-linked mechanism/effect pairs.
  - Wave phase / superposition advancing continuously: an
    always_redraw(lambda: axes.plot(lambda x: sin(x + phase.get_value()))) so
    the wave visibly propagates rather than jumping between static frames.
  COST (measured here, 480p15): reactive GEOMETRY is cheap — several
  always_redraw shapes over a 3s animation render in ~5s. But an always_redraw
  that rebuilds a **Text** every frame costs roughly 3x more than the entire
  rest of the scene (glyphs are re-rasterised each frame), and several separate
  always_redraws that each recompute the same geometry multiply that again — a
  careless version of the same scene measured 40s against 3.6s for the lean one.
  So: prefer to let the GEOMETRY carry the value (a projection line's length,
  a bar's height) with static labels beside it; if a changing NUMBER is truly
  the teaching point, update it a few times instead of every frame; and build
  one figure's parts inside ONE always_redraw returning a VGroup rather than
  several that duplicate the same work. Keep reactive stretches to ~3s.
  Hard limits (reliability): the lambda passed to always_redraw must ONLY read
  a ValueTracker via .get_value() and construct/return a mobject — no
  callbacks, no event handlers, no state mutation, no external data, no
  branching on time.time() or frame count. Never wire two ValueTrackers to the
  same mobject family in one scene. If a discrete Transform/MoveAlongPath/
  TransformFromCopy would teach the SAME relationship, prefer it — it is more
  reliable and this capability should not become the default choice.

OUT OF SCOPE this phase (do NOT use — reliability first):
  add_updater/remove_updater directly (use always_redraw as shown above, never
  a raw updater function), callbacks, event handlers, lambdas bound to
  anything other than a single ValueTracker's .get_value(), more than one
  ValueTracker per scene, reactive geometry beyond the patterns above, external
  assets/images, physics simulation, and any Manim API not listed above."""


_DIMENSIONALITY = """CHOOSING THE DIMENSION (pick what TEACHES best — depth is a tool, not a reward):
- 2D suits most explanatory work: equations, graphs, number lines, 2D vectors,
  processes, probability, derivations, circuits and force diagrams.
- 2.5D (layered 2D, still a normal `class X(Scene)`) is under-used and often the
  best upgrade: z_index ordering, foreground/background separation, scale +
  opacity hierarchy and deliberate occlusion. Reach for it whenever layering,
  stacking, nesting, before/after overlays or "what sits on top of what"
  genuinely clarifies a relationship.
- TRUE 3D (`class X(ThreeDScene)`) is for when spatial structure IS the concept
  and 2D would misrepresent it — 3D geometry/volume, rotation about a spatial
  axis, vectors/planes in 3-space, orbital motion, a genuinely spatial field.
- The one hard rule: depth must EXPLAIN something. Depth added because it looks
  impressive is forbidden in every dimension.
- A DIMENSION is provided in the visual direction above. Honor it."""


# Sent when the storyboard selected 2d. Layering is deliberately still permitted
# here (see LIFT INTO DEPTH): the dimension tag marks a scene's centre of
# gravity, not a capability fence. Only the true-3D API is withheld.
_DIMENSIONALITY_2D = """DIMENSION FOR THIS SCENE: 2D (write a normal `class X(Scene)`).
- Build this scene in the picture plane. Do not use a three-dimensional scene
  type, spatial axes, solid bodies or camera-orientation calls — this scene does
  not need them.
- To emphasise, use object.animate.scale(...) or Indicate — never a camera move.

LIFT INTO DEPTH (2.5D is available here and is encouraged where it earns its place):
- A flat scene may LIFT into layered depth at the moment depth starts to explain
  something: give objects obj.set_z_index(n), then animate scale (nearer =
  larger) and opacity (farther = dimmer) so a flat arrangement visibly becomes a
  stack with a clear front and back.
- This flat -> layered move is especially strong for revealing that one thing
  sits ON or BEHIND another, for stacking cases/contributions, for separating a
  foreground example from background context, and for showing an overlay landing
  on top of what came before.
- Keep it purposeful: if the layering does not clarify a relationship, stay flat."""

# Sent when the storyboard selected 2.5d — layering IS the point of this scene.
_TWOFIVED_NOTE = """DIMENSION FOR THIS SCENE: 2.5D (layered 2D — still a normal `class X(Scene)`).
Layering is the POINT of this scene, not a garnish.
- Imply depth with obj.set_z_index(n) (higher draws in front), plus scale
  (nearer = larger) and opacity (farther = dimmer), and deliberate occlusion.
- Prefer to EARN the depth on screen: start the elements flat, then animate them
  into their layered arrangement so the viewer sees the stack form, rather than
  opening on a pre-stacked frame.
- Animate one layer sliding over/under another to reveal ordering; bring the
  layer under discussion forward and push the rest back.
- This is still a flat Scene: no three-dimensional scene type, spatial axes,
  solid bodies or camera-orientation calls. Fake depth as decoration is
  forbidden."""


# 3D constructs below were render-verified in this environment (cairo renderer,
# no OpenGL). Arrow3D is intentionally discouraged: it renders ~4x slower.
_THREED_RECIPE = """TRUE 3D RECIPE (only when the DIMENSIONALITY rule calls for 3D — copy this shape):
```python
from manim import *

class SpatialConcept(ThreeDScene):
    def construct(self):
        # ONE fixed camera orientation. NO ambient rotation, NO move_camera loops.
        self.set_camera_orientation(phi=65 * DEGREES, theta=45 * DEGREES)
        axes = ThreeDAxes(x_range=[-3, 3], y_range=[-3, 3], z_range=[-3, 3])
        self.play(Create(axes), run_time=1.5)
        # 3D vectors: use Line3D (fast), NOT Arrow3D (renders ~4x slower).
        v = Line3D(start=ORIGIN, end=axes.c2p(2, 1, 2), color=BLUE_D)
        self.play(Create(v), run_time=1.5)
        # Rotation about an axis where periodicity/orientation IS the concept:
        cube = Cube(side_length=1.2, fill_opacity=0.6, fill_color=TEAL_C)
        self.play(FadeIn(cube), run_time=1.0)
        self.play(Rotate(cube, angle=PI, axis=UP), run_time=2.5)
```
VERIFIED 3D API — use ONLY these, do not invent (a solid NOT on this list, e.g.
Cylinder/Cone/Torus/Prism, does not exist here — build the concept from Cube,
Sphere or Surface instead):
  ThreeDScene, self.set_camera_orientation(phi=.., theta=..), ThreeDAxes(x_range=..,
  y_range=.., z_range=..), axes.c2p(x, y, z), Line3D(start, end, color=..),
  Cube(side_length=.., fill_opacity=.., fill_color=..), Sphere(radius=..),
  Surface(lambda u, v: axes.c2p(u, v, f(u, v)), u_range=.., v_range=.., resolution=(N, N)),
  Rotate(obj, angle=.., axis=UP|RIGHT|OUT), Create, FadeIn, FadeOut, GrowFromCenter.
RENDER COST (this renders on CPU — an over-detailed mesh is the #1 cause of a
3D scene timing out and failing entirely):
  - Surface resolution MUST be (16, 16) or lower — e.g. resolution=(12, 12).
    Never go above (16, 16); each step up roughly QUADRUPLES render time and a
    scene that never finishes teaches nothing.
  - Use AT MOST one Surface per scene, and at most 2-3 solids (Cube/Sphere)
    total on screen at once.
3D RESTRICTIONS (reliability): NO begin_ambient_camera_rotation, NO move_camera
  animation, NO updaters/ValueTracker/always_redraw, NO Arrow3D for the main
  motion, NO Text3D. For 3D labels, use plain Text and self.add_fixed_in_frame_mobjects(label)."""


_COLOR_DIRECTION = """COLOR DIRECTION (disciplined, not decorative):
1. Built-in Manim color variants ARE available via `from manim import *`. USE
   ONLY these names — a plausible-sounding guess (AMBER, CYAN, LIME, ORANGE_D)
   does not exist and will crash the whole scene with a NameError:
     Base: WHITE, BLACK, GREY, GREY_A, GREY_B, GREY_C, GREY_D, GREY_E, GREY_BROWN
     Blue: BLUE, BLUE_A, BLUE_B, BLUE_C, BLUE_D, BLUE_E
     Teal: TEAL, TEAL_A, TEAL_B, TEAL_C, TEAL_D, TEAL_E
     Green: GREEN, GREEN_A, GREEN_B, GREEN_C, GREEN_D, GREEN_E
     Yellow: YELLOW, YELLOW_A, YELLOW_B, YELLOW_C, YELLOW_D, YELLOW_E
     Gold: GOLD, GOLD_A, GOLD_B, GOLD_C, GOLD_D, GOLD_E
     Red: RED, RED_A, RED_B, RED_C, RED_D, RED_E
     Maroon: MAROON, MAROON_A, MAROON_B, MAROON_C, MAROON_D, MAROON_E
     Purple: PURPLE, PURPLE_A, PURPLE_B, PURPLE_C, PURPLE_D, PURPLE_E
     Pink: PINK, LIGHT_PINK
     Orange: ORANGE
   If you want a color NOT on this list, use a hex code instead — e.g.
   color="#FF9F1C" — never invent a named constant.
2. Pick a SMALL, coherent palette per scene: roughly one base hue, one accent for
   the current focus, plus neutrals. Tonal variants (BLUE_D vs BLUE_E) are the
   preferred way to show hierarchy or grouping.
2b. REACH FOR VALUE (lightness) BEFORE REACHING FOR ANOTHER HUE. When you need to
   separate foreground from background, or show that several things belong to one
   group, vary LIGHTNESS within a hue rather than introducing a new colour:
   the letter suffix is a value scale — _A is lightest through _E darkest
   (BLUE_A > BLUE_B > BLUE_C > BLUE_D > BLUE_E). Foreground/subject takes the
   brighter value, receding context takes the darker one. This keeps the palette
   restrained while still giving the frame depth, and it composes naturally with
   2.5D layering (nearer = larger + brighter, farther = smaller + darker) and
   with dim-the-rest focus. Adding a new HUE should mean "this is a genuinely
   different KIND of thing", never merely "this is a different item".
3. Color must MEAN something: distinguish categories, mark correspondence between
   related objects, signal a change of state, or direct attention.
4. Keep an object's color STABLE for as long as it represents the same concept —
   across the whole scene, and across scenes where the concept persists.
5. NO rainbow palettes, no random recoloring, no decorative color noise.
6. Prefer colorblind-safe distinctions (e.g. blue/orange, blue/red with a shape
   or position cue) over a red-vs-green-only contrast when color alone carries
   the meaning."""


_VISUAL_QUALITY_RULES = """VISUAL QUALITY REQUIREMENTS (for crisp 1080p output):
1. Clear visual hierarchy: one focal element; supporting elements smaller/dimmer.
2. Keep ALL content inside safe margins (roughly x in [-6.5, 6.5], y in [-3.5, 3.5]);
   never let objects run off-screen. Use fit_to_frame() or scale down large groups.
3. NO overlapping objects or overlapping text. Separate elements spatially or in time.
4. Font sizes: titles 44-54, labels 28-36, captions 22-26. Avoid walls of text.
5. The visual demonstrates the concept; text is only a short label, not the explanation."""


_SCENE_CRAFT = """SCENE CRAFT (conventions that make a video feel designed — apply with judgement,
these are defaults to lean on, not rigid rules):
- FRAMING: keep a consistent, calm layout. A short title/label usually sits near
  the top (to_edge(UP)); the main action lives in the centre band; supporting
  labels/values go just beside or below the object they describe. Don't scatter
  elements to random corners scene to scene — a viewer should feel the frame is
  the same "stage" each time.
- LINE WEIGHT CARRIES HIERARCHY (cheap and very effective — Manim's uniform
  default stroke is the single biggest "untouched default" tell): set
  stroke_width deliberately. The SUBJECT of the scene gets a heavy stroke
  (~5-7); ordinary supporting geometry sits at the default (~4); scaffolding the
  viewer should read past — axes, gridlines, guides, construction lines,
  dashed helpers — drops to ~1.5-2.5. When focus moves to a different object,
  its weight can grow as the previous subject's returns to normal. Never draw
  every line at the same weight: the frame then has no foreground.
- ENTRANCES & EXITS: give objects a soft, directional entrance (FadeIn(obj,
  shift=UP/DOWN), GrowFromCenter, Create, Write) rather than a hard pop-in, and a
  matching soft exit (FadeOut(obj, shift=..)). Keep the style consistent within a
  scene. A key object may simply Transform instead of exiting — often better.
- LABEL TIMING: introduce a label only AFTER (or together with) the object it
  names — never a naked label floating before its object exists. When an object
  leaves, its label leaves with it. Never leave an orphaned label on screen.
- EMPHASIS BUDGET: Indicate / Flash / Circumscribe / FocusOn are seasoning, not
  the meal. Use at most 1-2 emphasis beats in a scene, only on the ONE thing being
  explained at that moment. If everything flashes, nothing stands out.
- DIM-THE-REST FOCUS: when the narration is about ONE object or relationship
  among several on screen, prefer dimming everything else
  (`others.animate.set_opacity(0.3)`) over flashing the active one — it reads
  as calmer, more deliberate direction. Restore opacity when attention should
  widen again. This is a safe .animate opacity change, not a new capability;
  use it in place of Indicate when several objects are already visible and one
  needs to lead. Avoid constant flashing/wiggling as decoration either way."""


_ON_SCREEN_TEXT_RULES = """ON-SCREEN TEXT RULES (label the visual, do NOT narrate on screen):
1. NEVER put the narration on screen. Text labels the visual; the voiceover explains it.
2. Use text only for: essential labels, short equations, key values, units, one-line
   definitions, or a final takeaway. Maximum 2-3 lines for any explanatory text.
3. NO generic headings ("Introduction", "Explanation", "Summary", "Overview") unless the
   word is itself the concept being taught.
4. Keep terminology, capitalization and symbols consistent with the narration and with
   earlier scenes (same term = same wording every time).
5. Preserve Unicode exactly: accents (é, ñ), arrows (->), Greek and math symbols. Write
   them directly as characters. NEVER emit corrupted sequences such as 'a†' or 'aœ'.
6. Text must never overlap another object, never be clipped, and must wrap before the
   safe margin."""


def _motion_contract(audio_duration: Optional[float]) -> str:
    """Build the purposeful-progression contract for this scene's real length.

    This is the direct fix for frozen/static scenes: it demands timed, meaningful
    beats across the WHOLE duration and forbids padding the end with a dead wait.
    """
    if audio_duration and audio_duration > 0:
        dur = float(audio_duration)
        # A meaningful change roughly every 2-3s, at least 3 for a normal scene.
        beats = max(3, int(round(dur / 2.5)))
        last_beat = max(1.0, dur - 1.5)
        length_line = (
            f"- This scene is EXACTLY {dur:.2f}s. Plan {beats} meaningful visual "
            f"beats spread across it, with the LAST beat starting no later than "
            f"~{last_beat:.1f}s.\n"
            f"- TIME BUDGET (do the arithmetic): sum every run_time= and self.wait() you "
            f"write. That sum MUST equal {dur:.2f}s, give or take 0.3s. If your beats only "
            f"add up to less than that, you are NOT DONE — add another beat. Never close the "
            f"gap with a bigger self.wait(); close it with more animation."
        )
    else:
        length_line = ("- Plan at least 3 meaningful visual beats spread evenly across "
                       "the scene, with run_time totalling the scene's real length.")

    return f"""PURPOSEFUL VISUAL PROGRESSION (MANDATORY — this is what makes the video good):
{length_line}
- Structure every scene as: OPENING STATE -> MEANINGFUL TRANSFORMATIONS -> CLEAN ENDING STATE.
- Something educationally meaningful must visibly change roughly every 2-3 seconds:
  reveal, transform, move, connect, split, combine, compare, update, highlight, or flow.
- EVERY movement must explain something. No decorative motion, no idle drifting, no
  spinning, no particles, no motion for its own sake.
- HOLD THE KEY MOMENT (this is direction, not dead air): immediately after the ONE
  most important reveal in the scene, a deliberate self.wait(0.6-1.0) lets the idea
  land. A good scene usually has exactly ONE such hold. The difference that matters:
  a HOLD follows a meaningful reveal and is followed by more animation; a FROZEN TAIL
  is time left over at the end because the animation ran out. Holds are good
  direction; frozen tails are the failure this contract exists to prevent.
- FORBIDDEN: ending the scene with a long frozen frame; a static text screen; a single
  unchanged diagram held for many seconds; generic title cards; random arrows; resetting
  to an unrelated visual; finishing early and leaving dead air.
- Do NOT pad the END with a long self.wait(). Fill remaining time with real beats; the
  FINAL self.wait() (after the last animation) must be <= 0.5s. This limit applies to
  the closing wait only — it does not forbid the mid-scene key-moment hold above.
- The objects on screen and the narration must refer to the SAME concept at the SAME
  moment. Introduce an object exactly when the narration mentions it.
- Carry forward / transform an object from the previous scene when the lesson connects,
  instead of clearing the screen and starting unrelated visuals.

CONTINUOUS MOTION (this is what separates a good scene from a slideshow):
- A scene is NOT "play, freeze, play, freeze". Motion should feel continuous:
  the viewer's eye should almost always have something evolving to follow.
- Give animations room to breathe: run_time is usually 1.5-4s when the motion is
  doing explanatory work. Snappy 0.3s flicks look nervous and teach nothing.
- OVERLAP related animations in ONE self.play(...) instead of firing them one at a
  time. Example: an object slides into place WHILE its label fades in and a
  connecting arrow grows — one coordinated beat, not three isolated ones.
- MOTION HIERARCHY — inside one coordinated beat, everything moving at the same
  speed reads flat and machine-made. Give the beat a LEAD and a SUPPORTING cast:
  the subject of the beat gets the longer, more prominent motion; labels, guides
  and context elements follow slightly behind and more quietly. Achieve it with
  DIFFERENT run_time values in the same self.play(...) (e.g. the curve transforms
  over 2.4s while its label fades in over 1.2s), and by giving supporting motion a
  gentler rate_func. One thing leads; the rest agrees with it.
- LAND, DON'T STOP DEAD — a motion that arrives and halts exactly on its mark looks
  mechanical. For an important arrival, let it settle: a slight overshoot then a
  small correction (a short follow-up .animate.scale(0.96) after arriving at 1.0),
  or rate_func=smooth on the main move with a brief settling beat after. Use this
  on the ONE key arrival in a scene, not on everything.
- REFRAME, DON'T PILE UP — when a new element must appear while existing elements
  stay, do not drop it on top of them or squeeze it into a gap. In the SAME
  self.play(...), shift/scale the existing elements to make room as the new one
  arrives. The frame reorganising itself reads as intentional composition; content
  landing on top of content reads as an accident. This is the preferred way to
  avoid overlap — better than placing everything far apart from the start.
- Use LaggedStart when several related items should arrive in order (parts of a
  whole, steps accumulating, items being compared). Scale lag_ratio to the COUNT:
  ~0.25-0.35 for 3-4 items (clearly sequential), ~0.1-0.15 for 8+ (a flowing
  cascade, not a slow queue). A large lag_ratio with many items drags badly.
- Do NOT insert self.wait() between beats just to separate them. Let one motion
  flow into the next. Use a short wait ONLY when the viewer genuinely needs a
  moment to read or absorb something — most often the single key-moment hold
  described above.
- Balance: continuous does NOT mean frantic. Intentional pacing with a little
  visual breathing room beats constant unrelated movement.
- VARY THE RHYTHM — do not make every beat the same length. A good scene has
  texture: a couple of quicker builds (run_time ~1.0-1.5s) to assemble a setup,
  then a slower, deliberate reveal (~2.5-3.5s) on the key moment the narration
  emphasises. Uniform 2s beats feel mechanical; changing pace directs attention
  to what matters. Still hit the total TIME BUDGET, just distribute it unevenly.

MOTION MUST DO EXPLANATORY WORK — pick what the lesson needs:
- a process -> flows along a path;           - a relationship -> connects via line/arrow/mapping;
- a comparison -> aligns, separates, or morphs between cases;
- a derivation -> builds from a copy of the previous form (TransformFromCopy);
- a structure -> assembles from parts, or decomposes into them;
- a quantity -> grows, shrinks, fills, or splits proportionally;
- a cycle/orientation -> rotates, but ONLY if periodicity is the point.
Emphasis (Indicate/Circumscribe/Flash) is for the ONE item under discussion right
now — never sprinkled decoratively.

AVOID REPETITIVE, GENERIC OUTPUT (AI-slop patterns to refuse):
- Do NOT default to "heading + rounded rectangle card + bullet text + arrows".
  If you have used that composition once, do not reuse it in this video.
- No generic title slides or static text screens unless a brief label is genuinely
  what the topic needs.
- No arrows, circles, highlights, flashes or rotations added without explanatory
  meaning. If you cannot say what a movement teaches, delete it.
- Choose the visual metaphor from the LESSON's purpose — comparison, process,
  transformation, cause/effect, hierarchy, quantity, probability, structure or
  derivation — not from a habitual layout.
- Visual novelty must come from the concept, not from arbitrary restyling."""


def _duration_section(audio_duration: Optional[float]) -> str:
    if audio_duration:
        return f"""
CRITICAL AUDIO SYNCHRONIZATION:
- This scene has an audio narration that lasts EXACTLY {audio_duration:.2f} seconds
- Your animation MUST last EXACTLY {audio_duration:.2f} seconds (not more, not less)
- Reach that length through MORE ANIMATION BEATS, not through self.wait(). See the
  TIME BUDGET rule below for exactly how to hit this number.
"""
    return """
TIMING GUIDANCE:
- This scene should last approximately 6-8 seconds
- Use run_time 1.5-3s per beat; reach the length with more beats, not self.wait()
"""


def resolve_domain_tags(storyboard_entry: Optional[dict]) -> list:
    """Tags routing this scene's guidance: primary first, then secondaries.

    Falls back to ``["general"]`` for a missing storyboard entry or an entry
    written before domain routing existed — routing must never block a render.
    """
    if not storyboard_entry:
        return ["general"]
    primary = storyboard_entry.get("primary_domain_tag") or "general"
    secondary = storyboard_entry.get("secondary_domain_tags") or []
    if isinstance(secondary, str):
        secondary = [secondary]
    return normalize_tags([primary, *secondary])


def _dimension_of(storyboard_entry: Optional[dict]) -> str:
    """Normalized dimension for this scene ("2d" when unset)."""
    if not storyboard_entry:
        return "2d"
    return (storyboard_entry.get("dimension") or "2d").strip().lower()


def _class_restriction(dimension: str) -> str:
    """Base-class rule for this scene's dimension (layer D, not a global rule).

    A 2D scene is told plainly to subclass Scene and never sees ThreeDScene or
    the camera-orientation API at all — mentioning them, even to forbid them,
    plants vocabulary a flat scene has no use for.
    """
    if dimension == "3d":
        return ("1. The class MUST inherit from ThreeDScene and follow the TRUE 3D RECIPE\n"
                "   exactly. Never MovingCameraScene.\n"
                "4. Set ONE fixed camera orientation; NO ambient rotation, NO move_camera\n"
                "   animation loops.")
    return ("1. The class MUST inherit from Scene (a plain 2D scene; layering with\n"
            "   set_z_index is still available). Never MovingCameraScene.\n"
            "4. Keep the camera fixed — build the explanation from the objects, not\n"
            "   from camera movement.")


def _dimension_section(dimension: str) -> str:
    """Layer D — only the dimension guidance this scene actually needs.

    A plain 2D scene never sees the ThreeDScene recipe: shipping 3D API to a 2D
    scene invites gratuitous 3D and wastes attention on rules it must not use.
    """
    if dimension == "3d":
        return f"{_DIMENSIONALITY}\n\n{_THREED_RECIPE}"
    if dimension == "2.5d":
        return f"{_DIMENSIONALITY}\n\n{_TWOFIVED_NOTE}"
    return _DIMENSIONALITY_2D


def _build_generation_prompt(
    text: str,
    animation: str,
    previous_context: Optional[dict],
    audio_duration: Optional[float],
    chapter: Optional[str],
    objective: Optional[str],
    explanation: Optional[str],
    storyboard_entry: Optional[dict] = None,
    global_style: Optional[str] = None,
    ledger_summary: Optional[str] = None,
    regen_feedback: Optional[str] = None,
) -> str:
    domain_tags = resolve_domain_tags(storyboard_entry)
    domain_section = build_domain_section(domain_tags)
    dimension = _dimension_of(storyboard_entry)
    dimension_section = _dimension_section(dimension)
    class_restriction = _class_restriction(dimension)
    continuity_mode = (storyboard_entry or {}).get("continuity_mode") or "varied"
    feedback_block = ""
    if regen_feedback:
        feedback_block = (
            f"\nIMPORTANT — PREVIOUS ATTEMPT PROBLEM (fix this): {regen_feedback}\n"
            "Produce a visually rich scene with clearly visible, well-framed content.\n"
        )
    return f"""{_context_section(previous_context, continuity_mode)}
{_visual_direction_section(storyboard_entry, global_style, ledger_summary)}
{feedback_block}
Generate Python code for Manim that implements this BESPOKE educational animation,
directed by the visual storyboard above.

CURRENT CONTENT:
- Chapter: {chapter or 'N/A'}
- Pedagogical Learning Objective: {objective or 'N/A'}
- Conceptual Explanation: {explanation or 'N/A'}
- Narrative text (voiceover): {text}
- Animation description to visualize: {animation}

{_duration_section(audio_duration)}

{_motion_contract(audio_duration)}

{dimension_section}

{domain_section}

{_VISUAL_QUALITY_RULES}

{_SCENE_CRAFT}

{_ON_SCREEN_TEXT_RULES}

{_PRIMITIVES_NOTE}

{_ANIMATION_VOCABULARY}

DO NOT INVENT API (the #1 cause of failed renders — measured):
1. Use ONLY method names and keyword arguments you are certain exist in Manim CE
   0.19.1. A plausible-sounding guess like `point_to_pycoords()` or a kwarg like
   `include_arrow=True` will crash the whole scene.
2. If unsure whether a constructor accepts a keyword, DON'T pass it — construct
   the object plainly, then set what you need afterwards:
   `a = Arrow(start, end); a.set_color(BLUE_D)` rather than an invented kwarg.
3. Safe, universally-available styling kwargs: color, fill_color, fill_opacity,
   stroke_width, stroke_color, font_size (text), radius (circle),
   side_length (square), width/height (rectangle).
4. Prefer a simpler construct you are SURE of over a fancier one you are guessing at.

IMPORTANT TECHNICAL RESTRICTIONS:
{class_restriction}
2. DO NOT use self.camera.frame (doesn't exist in Scene)
3. For zoom, use: object.animate.scale(factor) instead of camera.frame
5. If you need to emphasise, use scale/Indicate — not camera moves.
6. NEVER create empty Text or Paragraph objects (Text('') or Paragraph(''))
7. NEVER use positioning methods on empty Text/Paragraph objects
8. If you need placeholder text, use actual text like Text("Placeholder")
9. NEVER use MathTex, Tex, or any LaTeX-based rendering (LaTeX is not installed). Use standard Text or Paragraph objects for math (e.g. Text('C = 2 * pi * r') or Text('pi')).

{_COLOR_DIRECTION}

MANAGING THE CANVAS (avoid overlap WITHOUT killing continuity):
1. Prefer TRANSFORMING an existing object into the next one (Transform,
   ReplacementTransform, TransformFromCopy) over deleting it and building a new
   one from scratch. Seeing A BECOME B teaches; a cut does not.
2. Only FadeOut an object when it is genuinely finished with, or when the scene
   moves to a genuinely new idea that needs a fresh visual metaphor.
3. Position concurrent elements in DIFFERENT regions (UP/DOWN/LEFT/RIGHT) so they
   can coexist while a relationship is being shown.
4. Keep at most 2-3 text elements on screen at once; shapes/diagrams may be more.
5. Use self.clear() only when starting a genuinely unrelated visual.

RULES TO CONTROL TEXT WIDTH:
1. For LONG texts (>80 characters), use Paragraph() instead of Text()
2. Use the width parameter to limit width: Text("...", width=10) or Paragraph("...", width=11)
3. Appropriate font size: font_size=24-36 for long texts, 40-48 for short titles
4. Maximum recommended width is width=12

CODE STRUCTURE (shape only — invent the visual your lesson needs, do NOT copy this).
This example is for a 9-second scene: note the run_times are chosen so they SUM TO 9.0s
— that arithmetic is the part to copy, not the visuals:
```python
from manim import *

class ClassName(Scene):
    def construct(self):
        # OPENING STATE: establish the idea                          (run_time 2.0)
        core = Circle(radius=1.2, color=BLUE_D)
        label = Text('Concept', font_size=32, color=BLUE_E).next_to(core, DOWN)
        self.play(FadeIn(core, shift=UP), FadeIn(label, shift=UP), run_time=2.0)

        # BEAT: related elements arrive in order, and motion overlaps  (run_time 2.5)
        parts = VGroup(*[Dot(color=TEAL_C).shift(RIGHT * i) for i in range(3)])
        self.play(LaggedStart(*[GrowFromCenter(p) for p in parts], lag_ratio=0.25),
                  core.animate.shift(LEFT * 2), run_time=2.5)

        # BEAT: the object BECOMES its next form (teaches the relationship) (run_time 2.0)
        result = Square(side_length=1.6, color=GOLD_A).move_to(core)
        self.play(TransformFromCopy(core, result), Indicate(label), run_time=2.0)

        # BEAT: a consequence of that transformation, still moving      (run_time 2.0)
        note = Text('Result', font_size=28, color=GOLD_A).next_to(result, DOWN)
        self.play(FadeIn(note, shift=UP), result.animate.scale(1.15), run_time=2.0)

        # CLEAN ENDING STATE                                            (run_time 0.5)
        self.play(FadeOut(VGroup(core, label, parts, result, note), shift=DOWN), run_time=0.5)
        # total: 2.0 + 2.5 + 2.0 + 2.0 + 0.5 = 9.0s  <-- matches the scene length exactly
```

RESPONSE FORMAT (JSON):
{{
  "content": "complete Python code here (use single quotes inside the code)",
  "class_name": "ClassName"
}}

IMPORTANT:
- The code must be executable without errors
- Escape quotes correctly in the JSON
- Prefer transforming an object into the next one over deleting and rebuilding
"""


def _build_fix_prompt(
    current_code: str,
    error_message: str,
    class_name: str,
    domain_tags: Optional[list] = None,
    scene_intent: Optional[dict] = None,
) -> str:
    """Repair prompt, carrying the SAME domain routing the scene was generated with.

    Without it a repair sees only code and a traceback, and the cheapest way to
    make a broken domain visual compile is to delete the domain visual. The
    scene's guidance and intent are what make "preserve the explanation"
    actionable rather than aspirational.
    """
    intent_block = ""
    if scene_intent:
        goal = scene_intent.get("learning_goal") or scene_intent.get("key_concept")
        metaphor = scene_intent.get("visual_metaphor")
        motion = scene_intent.get("primary_motion")
        role = scene_intent.get("narrative_role") or "standalone"
        continuity = scene_intent.get("continuity_mode") or "varied"
        lines = [f"- {k}: {v}" for k, v in (
            ("Learning goal", goal), ("Visual metaphor", metaphor),
            ("Primary motion", motion)) if v]
        if role != "standalone":
            role_note = _NARRATIVE_ROLE_NOTES.get(role)
            if role_note:
                lines.append(f"- Narrative role ({role}): {role_note}")
        if continuity == "cumulative":
            lines.append(
                "- Continuity mode: cumulative — this scene is part of ONE evolving "
                "visual world; keep carrying/transforming the same objects, do not "
                "reset to a fresh unrelated visual just to fix the error"
            )
        if (scene_intent.get("scene_kind") or "explanation") == "worked_problem":
            lines.append(
                "- Scene kind: WORKED PROBLEM — this scene stages a calculation "
                "(given -> asked -> choose -> substitute -> solve -> answer). KEEP "
                "the diagram on screen, KEEP the numbers transforming from the "
                "diagram into the equation (TransformFromCopy), and KEEP the answer "
                "returning to the diagram with its units. Flattening the working "
                "into a static wall of algebra, or dropping the diagram, is a "
                "FAILED repair even if it compiles. The equation on screen is "
                "REQUIRED here and is not a 'text slide'."
            )
        colors = scene_intent.get("semantic_colors") or []
        color_pairs = [f"{c['concept']}={c['color']}" for c in colors
                      if isinstance(c, dict) and c.get("concept") and c.get("color")]
        if color_pairs:
            lines.append(f"- Semantic colors to keep using: {', '.join(color_pairs)}")
        if lines:
            intent_block = (
                "\nWHAT THIS SCENE IS TEACHING (preserve this — it is the point of the scene):\n"
                + "\n".join(lines) + "\n"
            )

    domain_block = ""
    if domain_tags and normalize_tags(domain_tags) != ["general"]:
        domain_block = (
            "\n" + build_domain_section(domain_tags)
            + "\n\nUse the guidance above to keep the repaired scene FAITHFUL to its "
              "subject — it is the same guidance the scene was written against.\n"
        )

    # A compile TIMEOUT is not a code error — the generic "fix the error" framing
    # below leads the model to make arbitrary unrelated edits, since nothing is
    # actually broken syntactically. Give it the real, actionable cause instead:
    # a render that is too slow, almost always excess 3D mesh density.
    if "Timeout: compilation exceeded" in (error_message or ""):
        timeout_block = """
THIS IS A RENDER-SPEED TIMEOUT, NOT A CODE ERROR (the code is syntactically fine
— it is simply too slow to render within the time budget). Do NOT make unrelated
edits. Reduce render cost with ONE of, in order of preference:
1. If this is a ThreeDScene: cut every Surface(...) resolution to (12, 12) or
   lower (a high resolution like (32, 32)+ multiplies quad count and is the
   single most common cause of a 3D timeout on this renderer).
2. Reduce the number of distinct 3D solids on screen at once, or simplify their
   geometry (fewer Cube/Sphere/Surface instances, smaller Line3D groups).
3. If the scene's spatial structure is not actually essential to the concept,
   convert it to the flat/layered 2.5D equivalent (set_z_index + scale/opacity)
   described in the DIMENSION guidance below — this is the most reliable fix
   when 3D was not truly necessary for this concept.
Keep every other line of the code, the teaching intent, and the visual beats
unchanged — this is a performance fix, not a rewrite.
"""
    else:
        timeout_block = ""

    return f"""The following Manim code failed to compile with an error. Please fix the code.
{intent_block}{domain_block}{timeout_block}

CURRENT CODE:
```python
{current_code}
```

ERROR MESSAGE:
```
{error_message}
```

PRESERVE THE LESSON — this is a REPAIR, not a rewrite:
1. Make the SMALLEST technical fix that resolves the error.
2. KEEP the lesson goal, the visual metaphor, the object relationships, the
   intended visual beats, AND the scene's dimensionality choice (2D / 2.5D layered
   / true 3D). Keep every transformation, flow and continuous motion that was valid.
3. DO NOT "fix" the error by deleting the animation. Collapsing the scene into a
   static text slide, a generic rectangle/card layout, or an unrelated
   box-and-arrow diagram counts as a FAILED repair even if it compiles.
4. If one construct is genuinely broken, replace it with the nearest equivalent
   that preserves the same meaning and motion — do not drop the beat entirely.
   Do NOT downgrade a ThreeDScene to flat 2D unless the 3D itself is what broke.

TECHNICAL RULES:
5. Keep the same class base: a Scene stays a Scene; a ThreeDScene stays a
   ThreeDScene (fixed camera orientation, no ambient rotation). Never MovingCameraScene.
6. DO NOT use self.camera.frame (doesn't exist in Scene)
7. NEVER create empty Text or Paragraph objects
8. NEVER use MathTex, Tex, or LaTeX. Convert equations/symbols to Text or Paragraph objects.
9. Built-in color variants (BLUE_D, TEAL_C, GOLD_A, ...) and hex codes are allowed.
10. Allowed animations: Write, Create, DrawBorderThenFill, FadeIn/FadeOut (incl.
    shift=), GrowFromCenter, Transform, ReplacementTransform, TransformFromCopy,
    AnimationGroup, LaggedStart, Succession, Indicate, Circumscribe, Flash,
    ShowPassingFlash, GrowArrow, MoveAlongPath, Rotate, and .animate.
11. A SINGLE ValueTracker driving a FEW always_redraw() mobjects is allowed (see
    CONTROLLED CONTINUOUS MOTION in the vocabulary) — if the scene already uses
    this pattern correctly, PRESERVE it; do not flatten it to static geometry
    just because a tracker is present. Only remove it if the tracker/redraw
    itself is the broken part (e.g. a raw add_updater, a callback, more than
    one tracker, or a lambda referencing something other than one tracker's
    .get_value()) — in that case replace with the narrow verified pattern or a
    discrete Transform/MoveAlongPath equivalent, never delete the relationship
    outright. DO NOT introduce raw add_updater, callbacks, event handlers,
    external assets, or any untested API.
12. If the error is "object has no attribute X" or "unexpected keyword argument
    X", the previous attempt INVENTED an API that does not exist. Replace it with
    a construct you are certain of — do not guess a second name. For Axes, the
    real methods are: plot, plot_parametric_curve, c2p, p2c, i2gp, get_area,
    get_riemann_rectangles, get_graph_label, get_axis_labels, get_vertical_line,
    get_secant_slope_group. For an unknown kwarg, drop it and set the property
    after construction instead.
13. Verified 3D API (if this is a ThreeDScene): set_camera_orientation(phi=,theta=),
    ThreeDAxes, axes.c2p(x,y,z), Line3D (prefer over the slow Arrow3D), Cube,
    Sphere, Surface, Rotate(obj, angle=, axis=). No ambient rotation, no updaters.
    For 3D text use plain Text + self.add_fixed_in_frame_mobjects(label).

RESPONSE FORMAT (JSON):
{{
  "content": "complete fixed Python code here",
  "class_name": "{class_name}",
  "fix_explanation": "brief explanation of what was fixed"
}}

Respond ONLY with valid JSON."""


def generate_manim_code(
    service: LLMService,
    text: str,
    animation: str,
    index: int,
    provider: str,
    client: Any = None,
    previous_context: Optional[dict] = None,
    audio_duration: Optional[float] = None,
    chapter: Optional[str] = None,
    objective: Optional[str] = None,
    explanation: Optional[str] = None,
    storyboard_entry: Optional[dict] = None,
    global_style: Optional[str] = None,
    ledger_summary: Optional[str] = None,
    regen_feedback: Optional[str] = None,
    status: StatusCallback = None,
) -> Optional[dict]:
    """Generate validated Manim code for one scene, directed by the storyboard.

    Returns a dict with ``'content'`` and ``'class_name'`` on success, or a dict with
    ``content=''`` and error metadata (``error_category``, ``error_message``, ``model``,
    ``validation_errors``) on failure.
    """
    prompt = _build_generation_prompt(
        text, animation, previous_context, audio_duration, chapter, objective, explanation,
        storyboard_entry=storyboard_entry, global_style=global_style,
        ledger_summary=ledger_summary, regen_feedback=regen_feedback,
    )
    last_model = getattr(service.roles, "animation", "unknown") if hasattr(service, "roles") else "unknown"

    try:
        result = service.generate(
            role="animation",
            system=_GEN_SYSTEM,
            prompt=prompt,
            provider=provider,
            client=client,
            response_schema=ManimCode,
        )
        last_model = result.model
    except LLMError as exc:
        print(f"[ERROR] Scene {index} code generation failed ({exc.category}): {exc}")
        return {
            "content": "",
            "class_name": f"Scene{index}",
            "error_category": exc.category,
            "error_message": str(exc),
            "model": exc.model or last_model,
            "used_fallback": False,
            "fallback_reason": None,
            "validation_errors": None,
        }

    try:
        code: ManimCode = parse_manim_code_from_text(result.text)
        print(f"[OK] Manim code validated for scene {index} (model: {result.model})")
        return {
            "content": code.content,
            "class_name": code.class_name,
            "model": result.model,
            "used_fallback": getattr(result, "used_fallback", False),
            "fallback_reason": getattr(result, "fallback_reason", None),
            "error_category": None,
            "raw_received": True,
        }
    except ScriptValidationError as exc:
        print(f"[ERROR] Scene {index} produced invalid Manim code payload: {exc}")
        return {
            "content": "",
            "class_name": f"Scene{index}",
            "error_category": "invalid_output",
            "error_message": f"Validation failed: {exc}",
            "raw_received": True,
            "model": result.model,
            "used_fallback": getattr(result, "used_fallback", False),
            "fallback_reason": getattr(result, "fallback_reason", None),
            "validation_errors": str(exc),
        }


def fix_manim_code(
    service: LLMService,
    original_code: str,
    error_message: str,
    class_name: str,
    provider: str,
    client: Any = None,
    storyboard_entry: Optional[dict] = None,
    status: StatusCallback = None,
) -> Optional[dict]:
    """Repair Manim code that failed to compile.

    ``storyboard_entry`` (optional, backward compatible) supplies the same
    domain routing and scene intent used at generation time so the repair
    preserves the educational visual instead of flattening it.

    Returns ``{'content': str, 'class_name': str}`` or ``None`` if repair failed.
    """
    prompt = _build_fix_prompt(
        original_code, error_message, class_name,
        domain_tags=resolve_domain_tags(storyboard_entry),
        scene_intent=storyboard_entry,
    )
    try:
        result = service.generate(
            role="repair",
            system=_FIX_SYSTEM,
            prompt=prompt,
            provider=provider,
            client=client,
            response_schema=ManimCode,
        )
    except LLMError as exc:
        print(f"[REPL] Fix request failed ({exc.category}): {exc}")
        return None

    try:
        code: ManimCode = parse_manim_code_from_text(result.text)
        if code.fix_explanation:
            print(f"[REPL] Fix applied: {code.fix_explanation}")
        return {"content": code.content, "class_name": code.class_name}
    except ScriptValidationError as exc:
        print(f"[REPL] Fix response invalid: {exc}")
        return None
