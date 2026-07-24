"""Persistent per-job visual ledger.

Tracks the visual choices already used in a video (metaphors, layouts, object
types, colors, motion patterns, text styles, transitions) so each new scene
prompt can be told what to avoid. Summaries are intentionally compact to limit
token usage and context pollution — full prior Manim source is never included.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

_WS = re.compile(r"\s+")


def normalize(value: Optional[str]) -> str:
    """Lowercase + collapse whitespace for stable comparison of visual choices."""
    if not value:
        return ""
    return _WS.sub(" ", str(value)).strip().lower()


@dataclass
class LedgerEntry:
    """One scene's recorded visual choices (compact)."""

    index: int
    metaphor: str = ""
    composition: str = ""
    objects: List[str] = field(default_factory=list)
    color_role: str = ""
    motion: str = ""
    transition: str = ""
    text_style: str = ""


class VisualLedger:
    """Accumulates and summarizes the visual choices used so far in a video."""

    def __init__(self) -> None:
        self.entries: List[LedgerEntry] = []

    # -- recording ------------------------------------------------------- #
    def record(self, entry: LedgerEntry) -> None:
        self.entries.append(entry)

    def record_from_storyboard(self, sb_scene: dict) -> LedgerEntry:
        """Build + record a ledger entry from a storyboard scene dict."""
        on_text = sb_scene.get("on_screen_text")
        entry = LedgerEntry(
            index=int(sb_scene.get("index", len(self.entries) + 1)),
            metaphor=str(sb_scene.get("visual_metaphor", "")),
            composition=str(sb_scene.get("composition", "")),
            objects=list(sb_scene.get("primary_objects", []) or []),
            color_role=str(sb_scene.get("color_role", "")),
            motion=str(sb_scene.get("primary_motion", "")),
            transition=str(sb_scene.get("transition_from_prev", "")),
            text_style=("text-on-screen" if on_text else "visual-only"),
        )
        self.record(entry)
        return entry

    # -- queries / diversity -------------------------------------------- #
    def metaphors(self) -> List[str]:
        return [normalize(e.metaphor) for e in self.entries if e.metaphor]

    def compositions(self) -> List[str]:
        return [normalize(e.composition) for e in self.entries if e.composition]

    def metaphor_used(self, metaphor: str) -> bool:
        return normalize(metaphor) in set(self.metaphors())

    def composition_count(self, composition: str) -> int:
        target = normalize(composition)
        return sum(1 for c in self.compositions() if c == target)

    def composition_overused(self, composition: str, limit: int = 2) -> bool:
        return self.composition_count(composition) > limit

    def distinct_metaphor_count(self) -> int:
        return len(set(self.metaphors()))

    # -- summaries ------------------------------------------------------- #
    def compact_summary(self, max_items: int = 12) -> str:
        """A short, prompt-friendly list of prior visual choices to avoid reusing."""
        if not self.entries:
            return "(none yet — this is the first scene)"
        recent = self.entries[-max_items:]
        lines = []
        for e in recent:
            objs = ", ".join(e.objects[:4])
            lines.append(
                f"- Scene {e.index}: metaphor='{e.metaphor}'; layout='{e.composition}'; "
                f"objects=[{objs}]; motion='{e.motion}'; color='{e.color_role}'"
            )
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {"entries": [asdict(e) for e in self.entries]}

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
                              encoding="utf-8")
