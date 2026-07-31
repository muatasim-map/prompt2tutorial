"""Controlled teaching modes and curriculum profiles for prompt construction."""

from __future__ import annotations

from typing import Optional


EXPLANATION_MODES = (
    "general",
    "conceptual_intuition",
    "worked_example",
    "derivation_visual_proof",
    "graphical_exploration",
    "exam_technique",
    "misconception_repair",
    "revision_recap",
)

CURRICULUM_PROFILES = ("general", "aqa_a_level_mathematics")

_MODE_ALIASES = {
    "": "general",
    "auto": "general",
    "balanced": "general",
    "conceptual": "conceptual_intuition",
    "intuition": "conceptual_intuition",
    "worked": "worked_example",
    "example": "worked_example",
    "visual_proof": "derivation_visual_proof",
    "proof": "derivation_visual_proof",
    "graph_exploration": "graphical_exploration",
    "graphical": "graphical_exploration",
    "exam": "exam_technique",
    "misconception": "misconception_repair",
    "revision": "revision_recap",
    "recap": "revision_recap",
}

_CURRICULUM_ALIASES = {
    "": "general",
    "none": "general",
    "aqa": "aqa_a_level_mathematics",
    "aqa_a_level": "aqa_a_level_mathematics",
    "aqa_a_level_math": "aqa_a_level_mathematics",
    "aqa_a_level_maths": "aqa_a_level_mathematics",
    "aqa_7357": "aqa_a_level_mathematics",
}


def _key(value: Optional[str]) -> str:
    if value is None:
        return ""
    return "_".join(str(value).strip().lower().replace("-", " ").split())


def normalize_explanation_mode(value: Optional[str]) -> str:
    key = _key(value)
    key = _MODE_ALIASES.get(key, key)
    if key not in EXPLANATION_MODES:
        raise ValueError(
            f"Unknown explanation mode '{value}'. "
            f"Choose one of: {', '.join(EXPLANATION_MODES)}"
        )
    return key


def normalize_curriculum_profile(value: Optional[str]) -> str:
    key = _key(value)
    key = _CURRICULUM_ALIASES.get(key, key)
    if key not in CURRICULUM_PROFILES:
        raise ValueError(
            f"Unknown curriculum profile '{value}'. "
            f"Choose one of: {', '.join(CURRICULUM_PROFILES)}"
        )
    return key


_MODE_SCRIPT = {
    "conceptual_intuition": """CONCEPTUAL INTUITION
Begin with a concrete question or dynamic representation. Build meaning before
formal notation, then explicitly connect every visual object to its symbol.""",
    "worked_example": """STEP-BY-STEP WORKED EXAMPLE
Choose one coherent problem with sensible values. Stage givens, method choice,
substitution, calculation, checking, and interpretation; never skip algebra.""",
    "derivation_visual_proof": """DERIVATION / VISUAL PROOF
State assumptions, build the result from prior facts, and make each equality or
implication visually justified. End by stating the scope of the derived result.""",
    "graphical_exploration": """GRAPHICAL EXPLORATION
Organize the lesson around accurate axes, parameters, invariants, intercepts,
turning points, asymptotes, and graph-to-equation connections.""",
    "exam_technique": """EXAM TECHNIQUE
Use an AQA-style prompt, identify the command word and givens, choose an
efficient method, show mark-earning working, check the result, and state traps.""",
    "misconception_repair": """MISCONCEPTION REPAIR
Present one plausible wrong approach, locate the exact invalid step, demonstrate
its consequence, then rebuild the correct mental model and method.""",
    "revision_recap": """REVISION RECAP
Prioritize recognition cues, essential results, linked representations, one
compact example, and common traps. Be concise without becoming a formula dump.""",
}

_MODE_VISUAL = {
    "conceptual_intuition": "Make the abstract idea emerge from a persistent visual model before equations.",
    "worked_example": "Keep the problem, diagram, and evolving calculation visible as one coordinated workspace.",
    "derivation_visual_proof": "Use TransformFromCopy or TransformMatchingTex so every new statement visibly follows.",
    "graphical_exploration": "Use correctly scaled axes and purposeful parameter motion; compare states without hard cuts.",
    "exam_technique": "Visually separate givens, method choice, working, checking, and the final exam-ready conclusion.",
    "misconception_repair": "Show the wrong model, mark the precise failure, then transform it into the corrected model.",
    "revision_recap": "Use a compact visual map with a small number of high-value transformations and retrieval cues.",
}

_AQA_SCRIPT = """CURRICULUM PROFILE: AQA A-level Mathematics (7357)
Align terminology and mathematical depth with the current AQA 7357
specification. Identify the relevant strand and integrate the overarching themes
of mathematical argument and proof, mathematical problem solving, and
mathematical modelling. Do not claim official endorsement, reproduce protected
exam questions, invent mark allocations, or force exam language into a purely
conceptual lesson."""

_AQA_STORYBOARD = """AQA 7357 VISUAL ACCURACY
Treat notation, domains, units, graph scales, exact values, assumptions, and
model limitations as visible teaching content. Prefer representations that help
a teacher expose reasoning and common AQA assessment errors."""

_AQA_MANIM = """AQA 7357 SCENE STANDARD
Keep notation conventional and exam-ready. Every plotted point, excluded value,
asymptote, vector direction, probability region, and numerical approximation
must encode the stated mathematics exactly."""


def _combine(*sections: str) -> str:
    return "\n\n".join(section for section in sections if section)


def build_script_guidance(mode: Optional[str], curriculum: Optional[str]) -> str:
    mode_key = normalize_explanation_mode(mode)
    curriculum_key = normalize_curriculum_profile(curriculum)
    mode_section = _MODE_SCRIPT.get(mode_key, "")
    if mode_section:
        mode_section = f"EXPLANATION MODE — {mode_section}"
    curriculum_section = _AQA_SCRIPT if curriculum_key == "aqa_a_level_mathematics" else ""
    return _combine(mode_section, curriculum_section)


def build_storyboard_guidance(mode: Optional[str], curriculum: Optional[str]) -> str:
    mode_key = normalize_explanation_mode(mode)
    curriculum_key = normalize_curriculum_profile(curriculum)
    mode_section = _MODE_VISUAL.get(mode_key, "")
    if mode_section:
        mode_section = (
            f"VISUAL DIRECTION FOR MODE — {mode_key.replace('_', ' ').upper()}\n"
            f"{mode_section}"
        )
    curriculum_section = _AQA_STORYBOARD if curriculum_key == "aqa_a_level_mathematics" else ""
    return _combine(mode_section, curriculum_section)


def build_manim_guidance(mode: Optional[str], curriculum: Optional[str]) -> str:
    mode_key = normalize_explanation_mode(mode)
    curriculum_key = normalize_curriculum_profile(curriculum)
    mode_section = _MODE_VISUAL.get(mode_key, "")
    if mode_section:
        mode_section = (
            f"SCENE EXECUTION FOR MODE — {mode_key.replace('_', ' ').upper()}\n"
            f"{mode_section}"
        )
    curriculum_section = _AQA_MANIM if curriculum_key == "aqa_a_level_mathematics" else ""
    return _combine(mode_section, curriculum_section)
