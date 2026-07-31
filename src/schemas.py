"""Pydantic schemas for LLM-generated script/scene data and Manim code.

Every provider response is validated through these models before it is allowed
to reach TTS, Manim code generation, or rendering. Raw ``json.loads`` output is
never trusted directly.

The models are deliberately permissive about *future* fields (``model_config``
uses ``extra="allow"``) so upcoming visual template / style / transition fields
can be added without breaking older cached payloads — but the core educational
fields are strictly validated.
"""

from __future__ import annotations

import ast
import json
import re
from typing import Any, List, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

# --------------------------------------------------------------------------- #
# Bounds — tuned to keep one video in the "1-2 minute" range and to reject
# obviously abusive / malformed payloads.
# --------------------------------------------------------------------------- #
MAX_SCENES = 40
MIN_SCENES = 1
MAX_FIELD_CHARS = 4000  # per free-text field
MAX_TEXT_CHARS = 2000  # narration text
MIN_SCENE_DURATION = 0.5
MAX_SCENE_DURATION = 60.0
MIN_TARGET_DURATION = 5
MAX_TARGET_DURATION = 600

_WS_RE = re.compile(r"[ \t\f\v]+")


def _normalize_ws(value: str) -> str:
    """Collapse runs of horizontal whitespace and trim, preserving newlines.

    Unicode / non-Latin content is preserved unchanged; only ASCII spacing is
    normalized so Spanish, Arabic, CJK, etc. survive intact.
    """
    # Normalize horizontal whitespace within each line, keep line breaks.
    lines = [_WS_RE.sub(" ", ln).strip() for ln in value.replace("\r\n", "\n").split("\n")]
    return "\n".join(lines).strip()


class Scene(BaseModel):
    """A single narrated + animated scene of the educational video."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=False)

    chapter: str = Field(..., description="Chapter title the scene belongs to")
    text: str = Field(..., description="Narration text (kept short)")
    animation: str = Field(..., description="Manim animation description")
    objective: str = Field(..., description="Pedagogical learning objective")
    explanation: str = Field(..., description="Conceptual explanation")
    duration: Optional[float] = Field(
        default=None, description="Optional target duration in seconds"
    )

    @field_validator("chapter", "text", "animation", "objective", "explanation", mode="before")
    @classmethod
    def _coerce_and_validate_text(cls, value: Any) -> str:
        if value is None:
            raise ValueError("field must not be null")
        if not isinstance(value, str):
            value = str(value)
        # Repair encoding corruption (âœ“ / â†') before it can reach TTS or the
        # screen. Legitimate Unicode (é, ñ, π, →, Σ) is preserved untouched.
        value = repair_mojibake(value)
        cleaned = _normalize_ws(value)
        if not cleaned:
            raise ValueError("field must not be blank")
        return cleaned

    @field_validator("text")
    @classmethod
    def _text_length(cls, value: str) -> str:
        if len(value) > MAX_TEXT_CHARS:
            raise ValueError(f"narration text exceeds {MAX_TEXT_CHARS} characters")
        return value

    @field_validator("chapter", "animation", "objective", "explanation")
    @classmethod
    def _field_length(cls, value: str) -> str:
        if len(value) > MAX_FIELD_CHARS:
            raise ValueError(f"field exceeds {MAX_FIELD_CHARS} characters")
        return value

    @field_validator("duration", mode="before")
    @classmethod
    def _validate_duration(cls, value: Any) -> Optional[float]:
        if value is None or value == "":
            return None
        try:
            num = float(value)
        except (TypeError, ValueError):
            raise ValueError("duration must be a number")
        if num <= 0:
            return None  # treat non-positive as "unknown"; TTS derives real duration
        if num < MIN_SCENE_DURATION:
            num = MIN_SCENE_DURATION
        if num > MAX_SCENE_DURATION:
            raise ValueError(
                f"scene duration {num:.1f}s exceeds maximum {MAX_SCENE_DURATION:.0f}s"
            )
        return num


class VideoScript(BaseModel):
    """Top-level validated script: an ordered list of scenes."""

    model_config = ConfigDict(extra="ignore")

    scenes: List[Scene] = Field(..., min_length=MIN_SCENES, max_length=MAX_SCENES)

    @field_validator("scenes")
    @classmethod
    def _bounds(cls, scenes: List[Scene]) -> List[Scene]:
        if len(scenes) < MIN_SCENES:
            raise ValueError(f"script must contain at least {MIN_SCENES} scene")
        if len(scenes) > MAX_SCENES:
            raise ValueError(f"script contains too many scenes (max {MAX_SCENES})")
        return scenes

    def as_scene_dicts(self) -> List[dict]:
        """Return scenes as plain JSON-serializable dicts (for the review UI)."""
        return [s.model_dump() for s in self.scenes]


_VISUAL_PRIMITIVE_NAMES = {
    "TYPE_SCALE", "MIN_READABLE_FONT_SIZE", "PALETTE",
    "styled_title", "body_text", "caption", "make_node", "make_box", "connect",
    "token_chip", "prob_bar", "highlight", "row", "column", "grid",
    "fit_to_frame", "focus_on", "restore_focus", "morph", "reveal",
    "clear_scene", "make_code_terminal", "make_array_grid",
}
_KNOWN_INVALID_MANIM_NAMES = {
    "CYAN", "AMBER", "MAGENTA", "LIME", "ORANGE_D", "TRANSPARENT", "Right",
    "Animate", "GrowFromBottom",
}
_KNOWN_INVALID_MANIM_KEYWORDS = {"DIRECTION"}
_KNOWN_INVALID_MANIM_METHODS = {"stretch_in_place", "scale_in_place"}
_QUALIFIED_RATE_FUNCTIONS = {
    "ease_in_cubic", "ease_out_cubic", "ease_in_out_cubic",
}


class ManimCode(BaseModel):
    """Validated Manim code payload returned by the animation model."""

    model_config = ConfigDict(extra="ignore")

    content: str = Field(..., description="Complete Python/Manim source")
    class_name: str = Field(..., description="Scene subclass name")
    fix_explanation: Optional[str] = Field(default=None)

    @field_validator("content")
    @classmethod
    def _content_not_blank(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("manim code content must not be blank")
        if "class " not in value:
            raise ValueError("manim code must define a class")
        return value

    @field_validator("class_name")
    @classmethod
    def _class_name_valid(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("class_name must be a string")
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value or ""):
            raise ValueError("class_name must be a valid Python identifier")
        return value

    @model_validator(mode="after")
    def _source_is_importable_manim_scene(self) -> "ManimCode":
        try:
            tree = ast.parse(self.content)
        except SyntaxError as exc:
            raise ValueError(
                f"manim code must be valid Python (line {exc.lineno}: {exc.msg})"
            ) from exc

        imports_manim = any(
            (isinstance(node, ast.Import) and any(alias.name == "manim" for alias in node.names))
            or (isinstance(node, ast.ImportFrom) and node.module == "manim")
            for node in tree.body
        )
        if not imports_manim:
            raise ValueError("manim code must import manim")

        declared = next(
            (
                node for node in tree.body
                if isinstance(node, ast.ClassDef) and node.name == self.class_name
            ),
            None,
        )
        if declared is None:
            raise ValueError(f"manim code must define class {self.class_name}")
        base_names = [
            base.id if isinstance(base, ast.Name)
            else base.attr if isinstance(base, ast.Attribute)
            else ""
            for base in declared.bases
        ]
        if not any(name.endswith("Scene") for name in base_names):
            raise ValueError(f"class {self.class_name} must inherit from a Manim Scene")

        names_used = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        }
        invalid_names = sorted(names_used & _KNOWN_INVALID_MANIM_NAMES)
        if invalid_names:
            raise ValueError(
                "unsupported Manim name(s): "
                + ", ".join(invalid_names)
                + "; use a verified Manim built-in (or #RRGGBB for colors)"
            )

        invalid_keywords = sorted({
            keyword.arg
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for keyword in node.keywords
            if keyword.arg in _KNOWN_INVALID_MANIM_KEYWORDS
        })
        invalid_methods = sorted({
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _KNOWN_INVALID_MANIM_METHODS
        })
        if invalid_keywords or invalid_methods:
            problems = invalid_keywords + invalid_methods
            raise ValueError(
                "unsupported Manim keyword/method(s): "
                + ", ".join(problems)
                + "; remove the invented API before rendering"
            )

        list_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.List):
                list_names.update(
                    target.id for target in node.targets
                    if isinstance(target, ast.Name)
                )
            elif isinstance(node, ast.AnnAssign) and isinstance(node.value, ast.List):
                if isinstance(node.target, ast.Name):
                    list_names.add(node.target.id)
        list_add_calls = sorted({
            node.func.value.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in list_names
        })
        if list_add_calls:
            raise ValueError(
                "Python list(s) use .add(): "
                + ", ".join(list_add_calls)
                + "; use .append() for a list"
            )

        bare_easing = sorted(names_used & _QUALIFIED_RATE_FUNCTIONS)
        if bare_easing:
            qualified = ", ".join(f"rate_functions.{name}" for name in bare_easing)
            raise ValueError(
                f"CSS-style easing must be qualified in this environment: {qualified}"
            )

        helpers_used = names_used & _VISUAL_PRIMITIVE_NAMES
        imported_helpers = set()
        imports_all_helpers = False
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module == "visual_primitives":
                for alias in node.names:
                    if alias.name == "*":
                        imports_all_helpers = True
                    else:
                        imported_helpers.add(alias.asname or alias.name)
        missing_helpers = sorted(
            helpers_used - imported_helpers if not imports_all_helpers else set()
        )
        if missing_helpers:
            raise ValueError(
                "visual_primitives helper(s) used without import: "
                + ", ".join(missing_helpers)
                + "; add `from visual_primitives import ...`"
            )
        return self


# --------------------------------------------------------------------------- #
# Visual-direction (storyboard) models
# --------------------------------------------------------------------------- #
MAX_STORYBOARD_FIELD_CHARS = 800
MAX_PRIMARY_OBJECTS = 8

_VALID_COMPLEXITY = {"low", "medium", "high"}

# --------------------------------------------------------------------------- #
# Domain-tag taxonomy — drives which slice of DOMAIN VISUAL REASONING guidance
# is injected into the Manim-generation prompt (see manim_generator.py). A
# bounded, controlled vocabulary: the LLM picks from this list, it never
# free-texts a category. "general" is the safe fallback for anything that
# doesn't need specialist guidance (intros, recaps, ambiguous/interdisciplinary
# scenes).
# --------------------------------------------------------------------------- #
DOMAIN_TAGS = (
    "general",
    "algebra",
    "geometry",
    "linear_algebra",
    "calculus",
    "probability_statistics",
    "discrete_algorithms",
    "mechanics",
    "waves",
    "electricity",
    "magnetism",
    "optics",
    "thermal",
    "quantum_nuclear",
    "astrophysics",
)
_DOMAIN_TAG_SET = set(DOMAIN_TAGS)
# A real A-level scene can legitimately span several domains (deriving SHM for a
# pendulum is mechanics + calculus + algebra + geometry), so the ceiling is set
# to be generous rather than stingy. Even 4 modules is ~1.2k tokens against the
# ~5.2k all-domain block this replaced, so headroom is not the constraint.
MAX_SECONDARY_DOMAIN_TAGS = 3
MAX_TOTAL_DOMAIN_TAGS = 4

# Optional, finer routing within A-level Mathematics. Domain tags remain the
# cross-subject visual vocabulary; this field selects the exact syllabus skill.
A_LEVEL_MATH_TOPICS = (
    "algebra_functions",
    "graphs",
    "coordinate_geometry",
    "sequences_series",
    "trigonometry",
    "exponentials_logarithms",
    "differentiation",
    "integration",
    "numerical_methods",
    "vectors",
    "statistics",
)
_A_LEVEL_MATH_TOPIC_SET = set(A_LEVEL_MATH_TOPICS)


def _clean_optional(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    cleaned = _normalize_ws(value)
    return cleaned or None


class GlobalStyle(BaseModel):
    """The video-wide visual style contract shared by every scene.

    A single coherent visual language (restrained palette, readable typography,
    consistent pacing/spacing) that keeps scenes unified without making them
    look identical.
    """

    model_config = ConfigDict(extra="ignore")

    palette: List[str] = Field(default_factory=list, description="Restrained color roles/names")
    typography: str = Field(default="Clean sans-serif, generous sizes, high contrast")
    pacing: str = Field(default="Calm, deliberate; consistent run_times")
    spacing: str = Field(default="Generous safe margins; no crowding")
    mood: str = Field(default="Clear, modern, educational")

    @field_validator("palette", mode="before")
    @classmethod
    def _coerce_palette(cls, value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [p for p in re.split(r"[,\n]", value) if p.strip()]
        if not isinstance(value, list):
            raise ValueError("palette must be a list of color names")
        cleaned = [_normalize_ws(str(v)) for v in value if _normalize_ws(str(v))]
        return cleaned[:12]

    @field_validator("typography", "pacing", "spacing", "mood", mode="before")
    @classmethod
    def _clean_style_field(cls, value: Any) -> str:
        cleaned = _clean_optional(value)
        if not cleaned:
            raise ValueError("style field must not be blank")
        if len(cleaned) > MAX_STORYBOARD_FIELD_CHARS:
            raise ValueError(f"style field exceeds {MAX_STORYBOARD_FIELD_CHARS} characters")
        return cleaned


# --------------------------------------------------------------------------- #
# Narrative arc + continuity mode + semantic color contract
# --------------------------------------------------------------------------- #
# A video is more than a list of independent scenes: it can open on a concrete
# question, build machinery, develop a mechanism, and return to visibly answer
# the question. NARRATIVE_ROLES tags each scene's place in that arc (or marks
# it as "standalone" when no arc applies — the safe default). CONTINUITY_MODES
# is a video-level choice between one evolving visual world ("cumulative") and
# genuinely distinct representations per scene ("varied") — both closed,
# bounded vocabularies, following the same pattern as DOMAIN_TAGS.
NARRATIVE_ROLES = (
    "standalone",     # default: no video-level arc, or this scene doesn't need one
    "hook",           # opens with a concrete question/puzzle/observation
    "setup",          # builds the visual machinery needed to understand the hook
    "development",    # develops the mechanism step by step
    "misconception",  # shows a plausible wrong model, then visibly breaks it
    "resolution",      # returns to the hook and visibly answers it
    "recap",          # closing summary
)
_NARRATIVE_ROLE_SET = set(NARRATIVE_ROLES)

CONTINUITY_MODES = ("varied", "cumulative")
_CONTINUITY_MODE_SET = set(CONTINUITY_MODES)

# What FORM a scene takes — orthogonal to NARRATIVE_ROLES (which says where a
# scene sits in the video's arc). A "worked_problem" scene is staged as a
# calculation (given -> choose -> substitute -> solve -> answer) rather than as
# a conceptual metaphor, and it is the one scene form where an equation on
# screen is required rather than discouraged. Deliberately only two values:
# these are the forms that genuinely need different direction, and the taxonomy
# is not extended speculatively.
SCENE_KINDS = ("explanation", "worked_problem")
_SCENE_KIND_SET = set(SCENE_KINDS)

MAX_SEMANTIC_COLORS = 6


class SemanticColorEntry(BaseModel):
    """One concept -> color binding in a video's semantic color contract.

    e.g. concept="force" color="GOLD_A" — kept stable for as long as that
    concept persists, across every scene that shows it.
    """

    model_config = ConfigDict(extra="ignore")

    concept: str = Field(..., description="The concept/role this color represents")
    color: str = Field(..., description="A Manim color name or hex code")

    @field_validator("concept", mode="before")
    @classmethod
    def _concept_not_blank(cls, value: Any) -> str:
        cleaned = _clean_optional(value)
        if not cleaned:
            raise ValueError("semantic color concept must not be blank")
        return cleaned[:120]

    @field_validator("color", mode="before")
    @classmethod
    def _color_valid(cls, value: Any) -> str:
        """A Manim color NAME (letters/digits/underscore) or a #RRGGBB hex code.

        Rejecting anything else here — before it ever reaches a generation
        prompt — stops a hallucinated or malformed color value from being
        echoed back into every scene's semantic color contract.
        """
        cleaned = _clean_optional(value)
        if not cleaned:
            raise ValueError("semantic color value must not be blank")
        if re.fullmatch(r"#[0-9A-Fa-f]{6}", cleaned):
            return cleaned.upper()
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", cleaned):
            return cleaned.upper()
        raise ValueError(
            f"'{cleaned}' is not a valid Manim color name or #RRGGBB hex code"
        )


class VisualBeat(BaseModel):
    """One meaningful visual change at a point in time inside a scene.

    Beats are how a scene *progresses* instead of freezing: each beat is an
    educationally meaningful reveal/transform/move/compare, not decoration.
    """

    model_config = ConfigDict(extra="ignore")

    at_seconds: Optional[float] = Field(
        default=None, description="Approximate start time within the scene")
    action: str = Field(..., description="The meaningful visual change")
    objects: List[str] = Field(
        default_factory=list, description="Objects involved in this beat")
    narration_cue: Optional[str] = Field(
        default=None,
        description="Short narration phrase that should trigger this visual change",
    )
    focus_object: Optional[str] = Field(
        default=None,
        description="The single object that should lead the viewer's attention",
    )
    emphasis: Optional[str] = Field(
        default=None,
        description="Attention level: primary, secondary, context, or none",
    )

    @field_validator("action", mode="before")
    @classmethod
    def _action_not_blank(cls, value: Any) -> str:
        cleaned = _clean_optional(value)
        if not cleaned:
            raise ValueError("beat action must not be blank")
        return cleaned[:MAX_STORYBOARD_FIELD_CHARS]

    @field_validator("objects", mode="before")
    @classmethod
    def _coerce_beat_objects(cls, value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [p for p in re.split(r"[,\n]", value) if p.strip()]
        if not isinstance(value, list):
            return []
        return [_normalize_ws(str(v)) for v in value if _normalize_ws(str(v))][:6]

    @field_validator("narration_cue", "focus_object", mode="before")
    @classmethod
    def _clean_beat_optional_text(cls, value: Any) -> Optional[str]:
        cleaned = _clean_optional(value)
        if not cleaned:
            return None
        return cleaned[:MAX_STORYBOARD_FIELD_CHARS]

    @field_validator("emphasis", mode="before")
    @classmethod
    def _coerce_emphasis(cls, value: Any) -> Optional[str]:
        cleaned = (_clean_optional(value) or "").lower()
        if not cleaned:
            return None
        return cleaned if cleaned in {"primary", "secondary", "context", "none"} else None

    @field_validator("at_seconds", mode="before")
    @classmethod
    def _coerce_at(cls, value: Any) -> Optional[float]:
        if value is None or value == "":
            return None
        try:
            num = float(value)
        except (TypeError, ValueError):
            return None
        return max(0.0, num)


class StoryboardScene(BaseModel):
    """The directed visual plan for a single scene.

    This is *direction* only — never Manim Python. It tells the animation model
    what to build, how it moves, how it connects to the prior scene, and how to
    stay distinct from earlier scenes.
    """

    model_config = ConfigDict(extra="ignore")

    index: int = Field(..., ge=1, description="1-based scene index")
    learning_goal: str = Field(..., description="What the viewer should learn")
    key_concept: str = Field(..., description="The single core concept of the scene")
    visual_metaphor: str = Field(..., description="Unique concrete visual metaphor")
    composition: str = Field(..., description="Layout / composition of the frame")
    primary_objects: List[str] = Field(..., description="Main on-screen objects")
    primary_motion: str = Field(..., description="Main motion/transformation")
    color_role: str = Field(..., description="How color is used to carry meaning")
    camera_plan: Optional[str] = Field(default=None, description="Framing/camera plan if relevant")
    transition_from_prev: str = Field(..., description="How this scene follows the previous one")
    on_screen_text: Optional[str] = Field(default=None, description="Text that may appear on screen")
    anti_repetition_notes: str = Field(..., description="How this scene differs from prior scenes")
    visual_complexity: str = Field(default="medium", description="low | medium | high")
    # Adaptive dimensionality. Optional + backward compatible: when omitted the
    # generation pass infers it (defaulting to 2D). Normalized to 2d | 2.5d | 3d.
    dimension: Optional[str] = Field(
        default=None, description="Visual dimension: 2d (default) | 2.5d | 3d")

    # Controlled multi-tag domain routing (backward compatible: missing on older
    # stored/reviewed scenes -> safe "general" default, never a validation error).
    # An LLM-supplied but UNKNOWN tag string, by contrast, is a validation error —
    # the taxonomy is closed, not free text.
    primary_domain_tag: str = Field(
        default="general", description="Single primary domain tag from DOMAIN_TAGS")
    secondary_domain_tags: List[str] = Field(
        default_factory=list,
        description="0-3 secondary domain tags from DOMAIN_TAGS, distinct from the primary")
    a_level_math_topic: Optional[str] = Field(
        default=None,
        description="Optional exact A-level Mathematics syllabus topic",
    )

    # Video-level narrative arc + continuity mode + semantic colors. All
    # optional/defaulted for backward compatibility with scenes stored before
    # this existed. narrative_role and semantic_colors are genuinely per-scene
    # fields (repeated per-scene since the flat list[StoryboardScene] schema is
    # what Gemini's structured output actually returns — see
    # gemini_storyboard_schema); continuity_mode is a video-level decision that
    # storyboard.py canonicalizes across scenes after parsing.
    narrative_role: str = Field(
        default="standalone", description="This scene's role in the video's narrative arc")
    scene_kind: str = Field(
        default="explanation",
        description="Form of the scene: explanation (default) | worked_problem")
    continuity_mode: str = Field(
        default="varied", description="cumulative (one evolving visual world) | varied")
    semantic_colors: List["SemanticColorEntry"] = Field(
        default_factory=list,
        description="0-6 concept->color bindings, stable across the video")

    @field_validator("narrative_role", mode="before")
    @classmethod
    def _norm_narrative_role(cls, value: Any) -> str:
        cleaned = _clean_optional(value)
        if not cleaned:
            return "standalone"
        role = cleaned.lower().replace("-", "_").replace(" ", "_")
        if role not in _NARRATIVE_ROLE_SET:
            raise ValueError(
                f"unknown narrative_role '{cleaned}'; must be one of: {', '.join(NARRATIVE_ROLES)}"
            )
        return role

    @field_validator("scene_kind", mode="before")
    @classmethod
    def _norm_scene_kind(cls, value: Any) -> str:
        """Absent/blank -> "explanation" (backward compatible); unknown -> error."""
        cleaned = _clean_optional(value)
        if not cleaned:
            return "explanation"
        kind = cleaned.lower().replace("-", "_").replace(" ", "_")
        if kind not in _SCENE_KIND_SET:
            raise ValueError(
                f"unknown scene_kind '{cleaned}'; must be one of: {', '.join(SCENE_KINDS)}"
            )
        return kind

    @field_validator("continuity_mode", mode="before")
    @classmethod
    def _norm_continuity_mode(cls, value: Any) -> str:
        cleaned = _clean_optional(value)
        if not cleaned:
            return "varied"
        mode = cleaned.lower().replace("-", "_").replace(" ", "_")
        if mode not in _CONTINUITY_MODE_SET:
            raise ValueError(
                f"unknown continuity_mode '{cleaned}'; must be one of: {', '.join(CONTINUITY_MODES)}"
            )
        return mode

    @field_validator("semantic_colors", mode="before")
    @classmethod
    def _coerce_semantic_colors(cls, value: Any) -> list:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("semantic_colors must be a list")
        return value[:MAX_SEMANTIC_COLORS]

    # --- Visual progression (all OPTIONAL + backward compatible) ----------- #
    # These drive purposeful motion. They must never block rendering: reviewed
    # frontend scenes and older cached storyboards omit them entirely.
    opening_state: Optional[str] = Field(
        default=None, description="What is on screen when the scene begins")
    visual_beats: List["VisualBeat"] = Field(
        default_factory=list, description="Ordered, timed, meaningful visual changes")
    transformations: List[str] = Field(
        default_factory=list, description="Meaningful transformations during the scene")
    ending_state: Optional[str] = Field(
        default=None, description="Clean state the scene settles into")
    continuity_notes: Optional[str] = Field(
        default=None, description="What carries forward to the next scene")

    @field_validator("opening_state", "ending_state", "continuity_notes", mode="before")
    @classmethod
    def _optional_progression_text(cls, value: Any) -> Optional[str]:
        cleaned = _clean_optional(value)
        if cleaned and len(cleaned) > MAX_STORYBOARD_FIELD_CHARS:
            return cleaned[:MAX_STORYBOARD_FIELD_CHARS]
        return cleaned

    @field_validator("transformations", mode="before")
    @classmethod
    def _coerce_transformations(cls, value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [p for p in re.split(r"[,\n]", value) if p.strip()]
        if not isinstance(value, list):
            return []
        return [_normalize_ws(str(v)) for v in value if _normalize_ws(str(v))][:8]

    @field_validator(
        "learning_goal", "key_concept", "visual_metaphor", "composition",
        "primary_motion", "color_role", "transition_from_prev", "anti_repetition_notes",
        mode="before",
    )
    @classmethod
    def _required_text(cls, value: Any) -> str:
        cleaned = _clean_optional(value)
        if not cleaned:
            raise ValueError("field must not be blank")
        if len(cleaned) > MAX_STORYBOARD_FIELD_CHARS:
            raise ValueError(f"field exceeds {MAX_STORYBOARD_FIELD_CHARS} characters")
        return cleaned

    @field_validator("camera_plan", "on_screen_text", mode="before")
    @classmethod
    def _optional_text(cls, value: Any) -> Optional[str]:
        cleaned = _clean_optional(value)
        if cleaned and len(cleaned) > MAX_STORYBOARD_FIELD_CHARS:
            raise ValueError(f"field exceeds {MAX_STORYBOARD_FIELD_CHARS} characters")
        return cleaned

    @field_validator("primary_objects", mode="before")
    @classmethod
    def _coerce_objects(cls, value: Any) -> List[str]:
        if value is None:
            raise ValueError("primary_objects must not be null")
        if isinstance(value, str):
            value = [p for p in re.split(r"[,\n]", value) if p.strip()]
        if not isinstance(value, list):
            raise ValueError("primary_objects must be a list")
        cleaned = [_normalize_ws(str(v)) for v in value if _normalize_ws(str(v))]
        if not cleaned:
            raise ValueError("primary_objects must not be empty")
        return cleaned[:MAX_PRIMARY_OBJECTS]

    @field_validator("visual_complexity", mode="before")
    @classmethod
    def _norm_complexity(cls, value: Any) -> str:
        cleaned = (_clean_optional(value) or "medium").lower()
        for level in _VALID_COMPLEXITY:
            if level in cleaned:
                return level
        return "medium"

    @field_validator("primary_domain_tag", mode="before")
    @classmethod
    def _norm_primary_tag(cls, value: Any) -> str:
        """Exactly one tag from the closed taxonomy.

        Absent/blank -> "general" (backward compatibility for stored scenes).
        Present but unknown -> ValueError: the taxonomy is closed, and silently
        swallowing a bogus tag would hide a broken storyboard pass.
        """
        cleaned = _clean_optional(value)
        if not cleaned:
            return "general"
        tag = cleaned.lower().replace("-", "_").replace(" ", "_")
        if tag not in _DOMAIN_TAG_SET:
            raise ValueError(
                f"unknown domain tag '{cleaned}'; must be one of: {', '.join(DOMAIN_TAGS)}"
            )
        return tag

    @field_validator("a_level_math_topic", mode="before")
    @classmethod
    def _norm_a_level_math_topic(cls, value: Any) -> Optional[str]:
        cleaned = _clean_optional(value)
        if not cleaned:
            return None
        topic = cleaned.lower().replace("&", "and")
        topic = re.sub(r"[^a-z0-9]+", "_", topic).strip("_")
        aliases = {
            "algebra_and_functions": "algebra_functions",
            "sequences_and_series": "sequences_series",
            "exponentials_and_logarithms": "exponentials_logarithms",
        }
        topic = aliases.get(topic, topic)
        if topic not in _A_LEVEL_MATH_TOPIC_SET:
            raise ValueError(
                "unknown A-level mathematics topic "
                f"'{cleaned}'; must be one of: {', '.join(A_LEVEL_MATH_TOPICS)}"
            )
        return topic

    @field_validator("secondary_domain_tags", mode="before")
    @classmethod
    def _norm_secondary_tags(cls, value: Any) -> List[str]:
        """0-2 known tags, de-duplicated deterministically (first occurrence wins)."""
        if value is None:
            return []
        if isinstance(value, str):
            value = [p for p in re.split(r"[,\n]", value) if p.strip()]
        if not isinstance(value, list):
            raise ValueError("secondary_domain_tags must be a list")

        out: List[str] = []
        for raw in value:
            cleaned = _clean_optional(raw)
            if not cleaned:
                continue
            tag = cleaned.lower().replace("-", "_").replace(" ", "_")
            if tag not in _DOMAIN_TAG_SET:
                raise ValueError(
                    f"unknown domain tag '{cleaned}'; must be one of: {', '.join(DOMAIN_TAGS)}"
                )
            if tag not in out:  # deterministic de-dup: keep first occurrence
                out.append(tag)
        if len(out) > MAX_SECONDARY_DOMAIN_TAGS:
            raise ValueError(
                f"at most {MAX_SECONDARY_DOMAIN_TAGS} secondary domain tags "
                f"(got {len(out)}: {', '.join(out)})"
            )
        return out

    @model_validator(mode="after")
    def _tags_coherent(self) -> "StoryboardScene":
        """Primary must not repeat in secondaries; total distinct tags bounded.

        Runs after both field validators so it sees normalized values.
        """
        if self.primary_domain_tag in self.secondary_domain_tags:
            self.secondary_domain_tags = [
                t for t in self.secondary_domain_tags if t != self.primary_domain_tag
            ]
        # "general" carries no specialist guidance, so pairing it with a real
        # domain is contradictory rather than additive — drop it as a secondary.
        if self.secondary_domain_tags:
            self.secondary_domain_tags = [
                t for t in self.secondary_domain_tags if t != "general"
            ]
        total = 1 + len(self.secondary_domain_tags)
        if total > MAX_TOTAL_DOMAIN_TAGS:
            raise ValueError(
                f"at most {MAX_TOTAL_DOMAIN_TAGS} total domain tags (got {total})"
            )
        return self

    def domain_tags(self) -> List[str]:
        """Ordered, de-duplicated tags for this scene: primary first."""
        return [self.primary_domain_tag] + list(self.secondary_domain_tags)

    @field_validator("dimension", mode="before")
    @classmethod
    def _norm_dimension(cls, value: Any) -> Optional[str]:
        """Normalize free-text dimensionality to 2d | 2.5d | 3d, else None.

        Order matters: check 2.5d before 2d/3d so 'two-and-a-half' / '2.5D' win.
        """
        cleaned = _clean_optional(value)
        if not cleaned:
            return None
        low = cleaned.lower()
        if "2.5" in low or "two-and-a-half" in low or "2.5d" in low or "layered" in low:
            return "2.5d"
        if "3" in low or "three" in low:
            return "3d"
        if "2" in low or "two" in low or "flat" in low:
            return "2d"
        return None


class Storyboard(BaseModel):
    """The complete validated visual storyboard for a video."""

    model_config = ConfigDict(extra="ignore")

    global_style: GlobalStyle = Field(default_factory=GlobalStyle)
    scenes: List[StoryboardScene] = Field(..., min_length=MIN_SCENES, max_length=MAX_SCENES)

    def entry_for(self, index: int) -> Optional[StoryboardScene]:
        for scene in self.scenes:
            if scene.index == index:
                return scene
        # Fall back to positional match when indices are missing/misaligned.
        if 1 <= index <= len(self.scenes):
            return self.scenes[index - 1]
        return None

    def as_dict(self) -> dict:
        return self.model_dump()


class ScriptValidationError(ValueError):
    """Raised when provider output cannot be parsed/validated as a VideoScript."""


def validate_target_duration(value: Any) -> int:
    """Clamp/validate a requested target duration (seconds)."""
    try:
        num = int(float(value))
    except (TypeError, ValueError):
        return 60
    if num < MIN_TARGET_DURATION:
        return MIN_TARGET_DURATION
    if num > MAX_TARGET_DURATION:
        return MAX_TARGET_DURATION
    return num


def _extract_json(raw: str) -> Any:
    """Best-effort JSON extraction, stripping markdown fences if present."""
    text = raw.strip()
    if "```json" in text:
        m = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
        if m:
            text = m.group(1)
    elif "```" in text:
        m = re.search(r"```\s*(.*?)\s*```", text, re.DOTALL)
        if m:
            text = m.group(1)
    return json.loads(text)


def _readable_errors(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ())) or "(root)"
        parts.append(f"{loc}: {err.get('msg')}")
    return "; ".join(parts)


def parse_script(data: Any) -> VideoScript:
    """Validate already-decoded data (list of scenes or ``{"scenes": [...]}``).

    Raises:
        ScriptValidationError: with a readable message on any validation failure.
    """
    if isinstance(data, dict) and "scenes" in data:
        payload = data
    elif isinstance(data, list):
        payload = {"scenes": data}
    else:
        raise ScriptValidationError(
            "expected a JSON array of scenes or an object with a 'scenes' array"
        )
    try:
        return VideoScript.model_validate(payload)
    except ValidationError as exc:
        raise ScriptValidationError(_readable_errors(exc)) from exc


def parse_script_from_text(raw: str) -> VideoScript:
    """Decode raw provider text then validate it as a :class:`VideoScript`."""
    try:
        decoded = _extract_json(raw)
    except json.JSONDecodeError as exc:
        raise ScriptValidationError(f"invalid JSON: {exc}") from exc
    return parse_script(decoded)


def parse_manim_code(data: Any) -> ManimCode:
    """Validate a decoded Manim code payload."""
    try:
        return ManimCode.model_validate(data)
    except ValidationError as exc:
        raise ScriptValidationError(_readable_errors(exc)) from exc


def parse_manim_code_from_text(raw: str) -> ManimCode:
    """Decode raw provider text then validate it as :class:`ManimCode`."""
    try:
        decoded = _extract_json(raw)
    except json.JSONDecodeError as exc:
        raise ScriptValidationError(f"invalid JSON: {exc}") from exc
    return parse_manim_code(decoded)


def extract_manim_candidate_from_text(raw: str) -> dict:
    """Decode an unvalidated Manim payload so malformed source can be repaired."""
    try:
        decoded = _extract_json(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(decoded, dict):
        return {}
    content = decoded.get("content")
    class_name = decoded.get("class_name")
    return {
        "content": content if isinstance(content, str) else "",
        "class_name": class_name if isinstance(class_name, str) else "",
    }


# --------------------------------------------------------------------------- #
# Narration quality helpers (Part 4)
# --------------------------------------------------------------------------- #

# Typical neural-TTS pace, words per second. Used to sanity-check that a scene's
# narration actually fits its intended duration.
TTS_WORDS_PER_SECOND = 2.6

# A UTF-8 multi-byte sequence always starts with a byte in 0xC2-0xEF. Decoded
# through a single-byte codepage those bytes become U+00C2..U+00EF, so any
# mojibake string necessarily contains one of these characters. This is a cheap
# pre-filter; the round trip below is the decisive test.
_MOJIBAKE_LEADS = {chr(c) for c in range(0xC2, 0xF0)}
_REPLACEMENT_CHAR = "�"


def _mojibake_roundtrip(text: str) -> Optional[str]:
    """Re-encode ``text`` as a single-byte codepage and decode it as UTF-8.

    Returns the recovered string when that round-trip yields *different*, valid
    text — the signature of mojibake. Returns ``None`` for clean text.

    Both cp1252 and latin-1 are tried because the two produce different garbage
    for the same bytes (cp1252 maps 0x86 to '†', latin-1 leaves it a control
    character), and real corruption is seen in both forms.
    """
    for encoding in ("cp1252", "latin-1"):
        try:
            candidate = text.encode(encoding, errors="strict").decode("utf-8", errors="strict")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if candidate != text and _REPLACEMENT_CHAR not in candidate:
            return candidate
    return None


def detect_mojibake(text: str) -> List[str]:
    """Return encoding-corruption markers found in ``text`` (empty == clean).

    Legitimate Unicode is preserved: real accents (é, ñ), arrows (→) and maths
    symbols (π, Σ, ≈) are never flagged, because they cannot survive the
    single-byte re-encode/UTF-8-decode round trip that defines mojibake.
    """
    if not text:
        return []
    leads = sorted({c for c in text if c in _MOJIBAKE_LEADS})
    if not leads:
        return []
    if _mojibake_roundtrip(text) is None:
        return []
    return leads


def repair_mojibake(text: str) -> str:
    """Recover the original UTF-8 text; safe no-op when nothing is corrupted.

    Applies the round trip repeatedly to undo double-encoding, stopping as soon
    as the text is clean.
    """
    if not text:
        return text
    current = text
    for _ in range(3):  # bounded: handles single and double encoding
        if not detect_mojibake(current):
            break
        recovered = _mojibake_roundtrip(current)
        if recovered is None or recovered == current:
            break
        current = recovered
    return current


def narration_word_count(text: str) -> int:
    return len([w for w in re.split(r"\s+", text or "") if w])


def estimate_narration_seconds(text: str, wps: float = TTS_WORDS_PER_SECOND) -> float:
    """Estimated spoken duration of ``text`` at typical TTS pace."""
    return narration_word_count(text) / max(0.1, wps)


def check_narration_pace(text: str, target_seconds: float, tolerance: float = 0.5) -> Optional[str]:
    """Return a human-readable warning if narration badly over/under-fills its slot."""
    if not target_seconds or target_seconds <= 0:
        return None
    est = estimate_narration_seconds(text)
    if est > target_seconds * (1 + tolerance):
        return (f"narration is ~{est:.1f}s of speech for a {target_seconds:.1f}s scene "
                f"({narration_word_count(text)} words) — too long")
    if est < target_seconds * (1 - tolerance) and target_seconds >= 4:
        return (f"narration is only ~{est:.1f}s of speech for a {target_seconds:.1f}s scene "
                f"({narration_word_count(text)} words) — too sparse")
    return None


def _normalize_for_similarity(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", (text or "").lower()).strip()


def find_duplicate_narration(texts: List[str], threshold: float = 0.82) -> List[tuple]:
    """Return ``(i, j, ratio)`` for near-duplicate narration pairs (repetition check)."""
    from difflib import SequenceMatcher

    normed = [_normalize_for_similarity(t) for t in texts]
    dupes = []
    for i in range(len(normed)):
        for j in range(i + 1, len(normed)):
            if not normed[i] or not normed[j]:
                continue
            ratio = SequenceMatcher(None, normed[i], normed[j]).ratio()
            if ratio >= threshold:
                dupes.append((i, j, round(ratio, 3)))
    return dupes


StoryboardScene.model_rebuild()


def gemini_storyboard_schema() -> Any:
    """Return a Gemini-compatible response schema for the storyboard pass.

    Gemini's structured-output validator rejects the nested :class:`Storyboard`
    wrapper (an object containing both a ``GlobalStyle`` object and a scene
    array) with HTTP 400 ``INVALID_ARGUMENT``. The flat ``list[StoryboardScene]``
    form is accepted and carries every per-scene visual-direction field, so no
    direction detail is lost. :func:`parse_storyboard` accepts a bare list and
    applies the default :class:`GlobalStyle`.
    """
    return list[StoryboardScene]


def parse_storyboard(data: Any) -> Storyboard:
    """Validate already-decoded storyboard data.

    Accepts ``{"global_style": {...}, "scenes": [...]}`` or a bare list of
    storyboard scenes (a default global style is then applied).
    """
    if isinstance(data, dict) and "scenes" in data:
        payload = data
    elif isinstance(data, list):
        payload = {"scenes": data}
    else:
        raise ScriptValidationError(
            "expected a storyboard object with a 'scenes' array or a list of scenes"
        )
    try:
        return Storyboard.model_validate(payload)
    except ValidationError as exc:
        raise ScriptValidationError(_readable_errors(exc)) from exc


def parse_storyboard_from_text(raw: str) -> Storyboard:
    """Decode raw provider text then validate it as a :class:`Storyboard`."""
    try:
        decoded = _extract_json(raw)
    except json.JSONDecodeError as exc:
        raise ScriptValidationError(f"invalid JSON: {exc}") from exc
    return parse_storyboard(decoded)
