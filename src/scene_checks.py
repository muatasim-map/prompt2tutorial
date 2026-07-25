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

    @property
    def uses_any_morph(self) -> bool:
        return bool(self.morph_verbs_used)


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


class _SceneVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.morph: List[str] = []
        self.fadeouts = 0
        self.fadeins = 0
        self.discarded: List[str] = []
        self.plays = 0

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802 (ast API)
        name = _callee_name(node)
        if name:
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
