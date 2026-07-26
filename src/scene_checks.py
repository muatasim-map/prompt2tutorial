"""Static (AST) checks on generated Manim scene code, run BEFORE rendering.

The animation prompt asks, at length, for objects to be *carried forward and
transformed* rather than deleted and rebuilt. Measured across 645 generated
scenes on disk, that instruction is ignored most of the time: ~69% of scenes use
``FadeIn``, ~47% use ``FadeOut``, but only ~10% use ``ReplacementTransform`` and
~13% ``TransformFromCopy`` — 60% contain no morph verb at all.

Prose has demonstrably failed to move that number, so this module turns the
preference into a deterministic gate. It is intentionally cheap and pure: no
LLM, no rendering, no I/O — just an AST walk — so it can run on every scene and
be unit-tested directly.

Findings are ADVISORY. A flagged scene is still perfectly renderable; the caller
may spend at most one targeted revision on it and must ship the original if that
revision fails. Nothing here ever rejects a scene outright.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Set

# Verbs that carry an existing mobject forward into a new form. Kept as a module
# constant so the gate can be tuned without touching the walker.
#
# NOTE: bare ``.animate`` is deliberately EXCLUDED. It mutates an object in
# place (shift/scale/set_opacity) and is near-universal in generated scenes, so
# counting it would make the check fire almost never — which is precisely the
# blind spot this module exists to close.
MORPH_VERBS: Set[str] = {
    "Transform",
    "ReplacementTransform",
    "TransformFromCopy",
    "MoveAlongPath",
    "always_redraw",
}

# A scene needs at least this many FadeOut calls before "no morph" reads as
# delete-and-rebuild rather than a simple scene that legitimately has little to
# transform. One closing FadeOut is normal and healthy.
MIN_FADEOUTS_FOR_FLAG = 2

FLAG_NO_MORPH = "no_morph_delete_and_rebuild"
FLAG_TEXT_MORPH = "text_to_text_morph"

# Morphs that interpolate one mobject's points into another's. Applied to two
# Text objects with different strings, Manim pairs glyphs by index and tweens
# between unrelated letterforms, so the middle of the animation is an unreadable
# overprinted smear. Measured across the 13-run benchmark this appeared in every
# run containing an equation scene, and because scenes also freeze on their
# final state the smear is frequently what the viewer stares at: one scene held
# a garbled frame for 5 of its 9 seconds.
#
# NOTE both names are listed deliberately. ReplacementTransform is NOT a fix for
# this — it has identical interpolation behaviour and merely swaps which object
# survives.
POINT_INTERPOLATING_MORPHS: Set[str] = {"Transform", "ReplacementTransform"}

# Mobjects whose rendered form is glyphs. TransformMatchingTex would be the
# normal escape hatch but LaTeX is not installed in this environment, so the
# supported answers are a cut (FadeOut + FadeIn) or TransformMatchingShapes.
GLYPH_MOBJECTS: Set[str] = {"Text", "Paragraph", "MarkupText"}

_GROUP_MOBJECTS: Set[str] = {"VGroup", "Group", "VDict"}


@dataclass(frozen=True)
class SceneCodeFacts:
    """What the AST walk found in one generated scene (pure data)."""

    morph_verbs_used: List[str] = field(default_factory=list)
    fadeout_count: int = 0
    fadein_count: int = 0
    # Names passed as the first argument to FadeOut(...), where recoverable.
    # These are the objects the scene throws away, and are what a targeted
    # revision message should name.
    discarded_names: List[str] = field(default_factory=list)
    play_call_count: int = 0
    parse_error: Optional[str] = None
    # (verb, left, right) for every point-interpolating morph between two
    # glyph-bearing mobjects — the unreadable-smear pattern.
    text_morphs: List[tuple] = field(default_factory=list)

    @property
    def uses_any_morph(self) -> bool:
        return bool(self.morph_verbs_used)

    @property
    def has_text_morph(self) -> bool:
        return bool(self.text_morphs)


def _callee_name(node: ast.Call) -> Optional[str]:
    """Best-effort name of the thing being called (``Foo(..)``/``a.Foo(..)``)."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _first_arg_name(node: ast.Call) -> Optional[str]:
    """Readable identifier for a call's first positional argument, if simple."""
    if not node.args:
        return None
    arg = node.args[0]
    if isinstance(arg, ast.Name):
        return arg.id
    # FadeOut(self.title) / FadeOut(group[0]) -> "title" / "group"
    if isinstance(arg, ast.Attribute):
        return arg.attr
    if isinstance(arg, ast.Subscript) and isinstance(arg.value, ast.Name):
        return arg.value.id
    return None


def _string_literal(node: ast.AST) -> Optional[str]:
    """The literal str a node evaluates to, when that is knowable statically."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    # Text('a' + 'b') / implicit concatenation of adjacent literals.
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _string_literal(node.left)
        right = _string_literal(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _unwrap_chained(node: ast.AST) -> ast.AST:
    """Peel builder-style method chains back to the constructing call.

    Generated code overwhelmingly writes ``VGroup(...).arrange(DOWN)`` or
    ``Text('x').next_to(y)``, so the assigned value is a call on an Attribute
    and the constructor name is buried one or more levels down.
    """
    seen = 0
    while (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
           and seen < 10):
        node = node.func.value
        seen += 1
    return node


def _glyph_text_of(node: ast.AST) -> Optional[str]:
    """The rendered string of a glyph mobject, when statically knowable.

    ``None`` means "not knowable", never "not a glyph mobject" — use
    :func:`_is_glyph_expr` for that question.
    """
    node = _unwrap_chained(node)
    if not isinstance(node, ast.Call):
        return None
    name = _callee_name(node)
    if name in GLYPH_MOBJECTS:
        return _string_literal(node.args[0]) if node.args else None
    if name in _GROUP_MOBJECTS:
        parts = [_glyph_text_of(a) for a in node.args if _is_glyph_expr(a)]
        if parts and all(p is not None for p in parts):
            return " ".join(parts)
    return None


def _is_glyph_expr(node: ast.AST) -> bool:
    """Whether this expression builds something whose visual form is glyphs.

    A group counts when text is the MAJORITY of its children: a
    ``VGroup(Rectangle, Text, Text)`` card smears its two labels under a point
    interpolation exactly as a pure text group does, while a diagram like
    ``VGroup(Axes, Text)`` is half text at most and is left alone — morphing
    those is legitimate and common.
    """
    node = _unwrap_chained(node)
    if not isinstance(node, ast.Call):
        return False
    name = _callee_name(node)
    if name in GLYPH_MOBJECTS:
        return True
    if name in _GROUP_MOBJECTS and node.args:
        glyphs = sum(1 for a in node.args if _is_glyph_expr(a))
        return glyphs * 2 > len(node.args)
    return False


class _SceneVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.morph: List[str] = []
        self.fadeouts = 0
        self.fadeins = 0
        self.discarded: List[str] = []
        self.plays = 0
        # name -> statically-known string content (or None when unknowable).
        # Populated as assignments are walked; a morph can only reference a
        # variable that was assigned earlier in the source, so one pass suffices.
        self.glyph_vars: dict = {}
        self.text_morphs: List[tuple] = []

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802 (ast API)
        if _is_glyph_expr(node.value):
            content = _glyph_text_of(node.value)
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.glyph_vars[target.id] = content
        self.generic_visit(node)

    def _resolve_glyph(self, node: ast.AST):
        """(is_glyph, known_text, label) for one morph argument."""
        if isinstance(node, ast.Name):
            if node.id in self.glyph_vars:
                return True, self.glyph_vars[node.id], node.id
            return False, None, node.id
        # eq_group[4] — one element of a text group is itself text, and its
        # content is not knowable statically, so it is treated as differing.
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            base = node.value.id
            if base in self.glyph_vars:
                return True, None, f"{base}[...]"
            return False, None, base
        if _is_glyph_expr(node):
            return True, _glyph_text_of(node), "Text(...)"
        return False, None, None

    def _check_text_morph(self, node: ast.Call, verb: str) -> None:
        if len(node.args) < 2:
            return
        left_is, left_txt, left_lbl = self._resolve_glyph(node.args[0])
        right_is, right_txt, right_lbl = self._resolve_glyph(node.args[1])
        if not (left_is and right_is):
            return
        # Morphing a string into an identical string is a no-op visually and
        # harmless — only differing (or unknown) content smears.
        if left_txt is not None and right_txt is not None and left_txt == right_txt:
            return
        self.text_morphs.append((verb, left_lbl, right_lbl))

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802 (ast API)
        name = _callee_name(node)
        if name:
            if name in POINT_INTERPOLATING_MORPHS:
                self._check_text_morph(node, name)
            if name in MORPH_VERBS and name not in self.morph:
                self.morph.append(name)
            elif name == "FadeOut":
                self.fadeouts += 1
                ident = _first_arg_name(node)
                if ident and ident not in self.discarded:
                    self.discarded.append(ident)
            elif name == "FadeIn":
                self.fadeins += 1
            elif name == "play":
                self.plays += 1
        self.generic_visit(node)


def analyze_scene_code(code: str) -> SceneCodeFacts:
    """Extract morph/fade facts from Manim source (pure, never raises)."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        # Unparseable code is the compile loop's problem, not ours. Report it
        # as a fact and let the caller skip the motion gate entirely.
        return SceneCodeFacts(parse_error=str(exc))

    visitor = _SceneVisitor()
    visitor.visit(tree)
    return SceneCodeFacts(
        morph_verbs_used=visitor.morph,
        fadeout_count=visitor.fadeouts,
        fadein_count=visitor.fadeins,
        discarded_names=visitor.discarded,
        play_call_count=visitor.plays,
        text_morphs=visitor.text_morphs,
    )


def build_text_morph_feedback(facts: SceneCodeFacts) -> str:
    """Targeted instruction to replace glyph-smearing morphs with a clean cut."""
    pairs = facts.text_morphs[:3]
    listed = "\n".join(
        f"- {verb}({left}, {right})" for verb, left, right in pairs
    )
    more = ""
    if len(facts.text_morphs) > len(pairs):
        more = f"\n(and {len(facts.text_morphs) - len(pairs)} more like it)"

    return (
        "TEXT MORPH REVISION — this scene morphs one piece of text directly "
        "into different text:\n"
        f"{listed}{more}\n\n"
        "Manim interpolates the two mobjects point by point, pairing glyphs by "
        "index, so the middle of that animation is an unreadable smear of "
        "overlapping letterforms — and because the scene then holds its final "
        "state, the smear is often what the viewer looks at longest.\n\n"
        "Replace EACH of those calls with one of these, and change nothing "
        "else:\n"
        "- FadeOut(old) followed by FadeIn(new) — or both inside one "
        "AnimationGroup for a crossfade. This is the safe default.\n"
        "- TransformMatchingShapes(old, new) ONLY when the two strings share "
        "most of their characters (e.g. adding a term to an equation), so the "
        "shared glyphs genuinely travel.\n"
        "- If the point is that one value becomes another, keep the label "
        "static and animate only the part that changes.\n\n"
        "Do NOT swap Transform for ReplacementTransform — they interpolate "
        "identically and produce the same smear. Do not use MathTex or Tex "
        "(LaTeX is not installed). Preserve the class name, the run_times, the "
        "total duration and every other beat."
    )


def needs_motion_revision(facts: SceneCodeFacts) -> bool:
    """Whether this scene looks like delete-and-rebuild rather than transformation.

    Deliberately conservative — it fires only on the unambiguous pattern (no
    morph verb anywhere AND repeated discarding), so a legitimately simple
    scene is not dragged into a needless extra LLM round-trip.
    """
    if facts.parse_error:
        return False
    if facts.uses_any_morph:
        return False
    return facts.fadeout_count >= MIN_FADEOUTS_FOR_FLAG


def build_motion_feedback(facts: SceneCodeFacts) -> str:
    """One specific, actionable instruction for the revision pass.

    Names the actual discarded objects rather than restating the general rule —
    the general rule is already in the generation prompt and did not work.
    """
    named: Sequence[str] = facts.discarded_names[:3]
    if named:
        objects = ", ".join(f"`{n}`" for n in named)
        subject = (
            f"This scene calls FadeOut on {objects} and then builds replacements "
            f"from scratch."
        )
    else:
        subject = (
            f"This scene calls FadeOut {facts.fadeout_count} times and then builds "
            f"replacements from scratch."
        )

    return (
        "MOTION REVISION — the scene compiles, but it deletes and rebuilds "
        "instead of transforming.\n"
        f"{subject} It uses no Transform, ReplacementTransform, "
        "TransformFromCopy, MoveAlongPath or always_redraw anywhere, so the "
        "viewer sees a slideshow of unrelated frames rather than one idea "
        "evolving.\n\n"
        "Make this ONE change and keep everything else intact:\n"
        "- Pick the object above that is conceptually the SAME thing as what "
        "replaces it, and connect them with ReplacementTransform (or "
        "TransformFromCopy if the original should stay on screen).\n"
        "- Delete the FadeOut/re-create pair for that object only.\n"
        "- Preserve the scene's total duration, its class name, its other "
        "beats, and every existing run_time. Do not restructure the scene, do "
        "not add new concepts, and do not turn it into a static diagram.\n"
        "If NO two objects genuinely represent the same thing, return the code "
        "unchanged — a forced transform between unrelated shapes is worse than "
        "a clean cut."
    )


def check_scene_motion(code: str) -> Optional[str]:
    """Convenience wrapper: feedback string if a revision is warranted, else None."""
    facts = analyze_scene_code(code)
    if needs_motion_revision(facts):
        return build_motion_feedback(facts)
    return None
