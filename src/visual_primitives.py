"""Low-level, composition-agnostic Manim building blocks.

These are *optional* helpers the animation model MAY import to write cleaner,
safer scenes. They are deliberately primitive (styled text, nodes/edges,
highlights, chips, bars, safe layout + framing, simple transitions) so they
compose into many different scene designs. They are NOT full-scene templates —
there is intentionally no "flowchart scene" or "comparison scene" class here.

This module is copied into each job's ``code/`` directory at render time and is
imported inside the Manim subprocess, so it must depend only on ``manim`` (never
on the application modules).
"""

from __future__ import annotations

from manim import (
    BLACK,
    BLUE,
    DOWN,
    GREEN,
    GREY,
    LEFT,
    ORANGE,
    PURPLE,
    RED,
    RIGHT,
    UP,
    WHITE,
    YELLOW,
    Arrow,
    Circle,
    Create,
    Dot,
    FadeIn,
    FadeOut,
    Line,
    ReplacementTransform,
    RoundedRectangle,
    SurroundingRectangle,
    Text,
    VGroup,
    config,
    smooth,
)

# --------------------------------------------------------------------------- #
# Palette — a restrained, coherent set of roles (only base Manim colors so no
# missing-import surprises). Use consistently across a video.
# --------------------------------------------------------------------------- #
PALETTE = {
    "bg": BLACK,
    "primary": BLUE,
    "secondary": GREEN,
    "accent": ORANGE,
    "highlight": YELLOW,
    "danger": RED,
    "muted": GREY,
    "text": WHITE,
    "alt": PURPLE,
}

TYPE_SCALE = {
    "hero": 60,
    "title": 48,
    "section": 36,
    "body": 30,
    "label": 26,
    "caption": 22,
}
MIN_READABLE_FONT_SIZE = TYPE_SCALE["caption"]

# Safe drawing area = frame minus a margin so nothing clips off-screen at 1080p.
SAFE_MARGIN = 0.6


def safe_width() -> float:
    return config.frame_width - 2 * SAFE_MARGIN


def safe_height() -> float:
    return config.frame_height - 2 * SAFE_MARGIN


def fit_to_frame(mobject, max_width: float | None = None, max_height: float | None = None):
    """Scale ``mobject`` down (never up) so it stays inside the safe area."""
    max_w = max_width if max_width is not None else safe_width()
    max_h = max_height if max_height is not None else safe_height()
    if mobject.width > max_w:
        mobject.scale_to_fit_width(max_w)
    if mobject.height > max_h:
        mobject.scale_to_fit_height(max_h)
    return mobject


# --------------------------------------------------------------------------- #
# Typography
# --------------------------------------------------------------------------- #


def styled_title(text: str, color=WHITE, font_size: int = TYPE_SCALE["title"]):
    """A short, readable title placed toward the top with a safe margin."""
    title = Text(str(text), color=color, font_size=font_size, weight="BOLD")
    fit_to_frame(title, max_width=safe_width())
    title.to_edge(UP, buff=SAFE_MARGIN)
    return title


def body_text(text: str, color=WHITE, font_size: int = TYPE_SCALE["body"]):
    """A single-line body label, auto-fitted to the safe width."""
    label = Text(str(text), color=color, font_size=font_size)
    fit_to_frame(label, max_width=safe_width())
    return label


def caption(text: str, color=GREY, font_size: int = TYPE_SCALE["caption"]):
    """A small caption anchored near the bottom safe margin."""
    cap = Text(str(text), color=color, font_size=font_size)
    fit_to_frame(cap, max_width=safe_width())
    cap.to_edge(DOWN, buff=SAFE_MARGIN)
    return cap


# --------------------------------------------------------------------------- #
# Nodes / edges (graphs, flows, structures — used freely, not a fixed template)
# --------------------------------------------------------------------------- #


def make_node(label: str, color=BLUE, radius: float = 0.6, text_color=WHITE, font_size: int = 26):
    """A labelled circular node as a VGroup(circle, label)."""
    circle = Circle(radius=radius, color=color, fill_color=color, fill_opacity=0.15, stroke_width=3)
    text = Text(str(label), color=text_color, font_size=font_size)
    if text.width > 2 * radius * 0.85:
        text.scale_to_fit_width(2 * radius * 0.85)
    text.move_to(circle.get_center())
    return VGroup(circle, text)


def make_box(label: str, color=BLUE, width: float = 2.2, height: float = 1.0,
             text_color=WHITE, font_size: int = 26):
    """A labelled rounded box as a VGroup(box, label)."""
    box = RoundedRectangle(corner_radius=0.15, width=width, height=height,
                           color=color, fill_color=color, fill_opacity=0.12, stroke_width=3)
    text = Text(str(label), color=text_color, font_size=font_size)
    if text.width > width * 0.85:
        text.scale_to_fit_width(width * 0.85)
    text.move_to(box.get_center())
    return VGroup(box, text)


def connect(a, b, color=GREY, tip: bool = True, stroke_width: float = 3):
    """A line/arrow from the edge of ``a`` toward the edge of ``b``."""
    start = a.get_center()
    end = b.get_center()
    if tip:
        return Arrow(start=start, end=end, color=color, stroke_width=stroke_width,
                     buff=0.15, max_tip_length_to_length_ratio=0.15)
    return Line(start=start, end=end, color=color, stroke_width=stroke_width, buff=0.15)


# --------------------------------------------------------------------------- #
# Chips / bars (tokens, probabilities, quantities)
# --------------------------------------------------------------------------- #


def token_chip(text: str, color=ORANGE, text_color=WHITE, font_size: int = 24):
    """A small rounded 'token' chip sized to its text."""
    label = Text(str(text), color=text_color, font_size=font_size)
    pad_w, pad_h = 0.35, 0.25
    chip = RoundedRectangle(
        corner_radius=0.12,
        width=max(0.8, label.width + pad_w),
        height=max(0.5, label.height + pad_h),
        color=color, fill_color=color, fill_opacity=0.25, stroke_width=2,
    )
    label.move_to(chip.get_center())
    return VGroup(chip, label)


def prob_bar(value: float, max_value: float = 1.0, color=GREEN, width: float = 4.0,
             height: float = 0.4, track_color=GREY):
    """A horizontal proportion bar: VGroup(track, fill) for value/max_value."""
    frac = 0.0 if max_value <= 0 else max(0.0, min(1.0, value / max_value))
    track = RoundedRectangle(corner_radius=height / 2, width=width, height=height,
                             color=track_color, fill_color=track_color, fill_opacity=0.15,
                             stroke_width=2)
    fill_w = max(0.001, width * frac)
    fill = RoundedRectangle(corner_radius=height / 2, width=fill_w, height=height,
                            color=color, fill_color=color, fill_opacity=0.8, stroke_width=0)
    fill.align_to(track, LEFT)
    return VGroup(track, fill)


# --------------------------------------------------------------------------- #
# Emphasis / layout / transitions
# --------------------------------------------------------------------------- #


def highlight(mobject, color=YELLOW, buff: float = 0.12):
    """A rounded highlight rectangle around ``mobject`` (add + play Create)."""
    return SurroundingRectangle(mobject, color=color, buff=buff, corner_radius=0.1)


def focus_on(scene, target, context, scale_factor: float = 1.06,
             context_opacity: float = 0.28, run_time: float = 0.6):
    """Lead attention calmly by lifting one object and dimming its context."""
    scene.play(
        target.animate.scale(scale_factor).set_opacity(1.0),
        context.animate.set_opacity(context_opacity),
        run_time=run_time,
        rate_func=smooth,
    )
    return target


def restore_focus(scene, target, context, scale_factor: float = 1.06,
                  run_time: float = 0.5):
    """Restore a composition after :func:`focus_on` without removing objects."""
    scene.play(
        target.animate.scale(1.0 / scale_factor),
        context.animate.set_opacity(1.0),
        run_time=run_time,
        rate_func=smooth,
    )
    return target


def row(*mobjects, gap: float = 0.6):
    """Arrange mobjects in a horizontal row and fit to the safe width."""
    group = VGroup(*mobjects).arrange(RIGHT, buff=gap)
    return fit_to_frame(group, max_width=safe_width())


def column(*mobjects, gap: float = 0.5):
    """Arrange mobjects in a vertical column and fit to the safe height."""
    group = VGroup(*mobjects).arrange(DOWN, buff=gap)
    return fit_to_frame(group, max_height=safe_height())


def grid(mobjects, cols: int = 3, gap: float = 0.5):
    """Arrange mobjects in a grid and fit to the safe area."""
    group = VGroup(*mobjects).arrange_in_grid(cols=cols, buff=gap)
    return fit_to_frame(group)


def morph(scene, source, target, run_time: float = 1.0):
    """Transform ``source`` into ``target`` in place (carries an object forward)."""
    scene.play(ReplacementTransform(source, target), run_time=run_time)
    return target


def reveal(scene, *mobjects, run_time: float = 0.8):
    """Fade/create mobjects onto the scene cleanly."""
    scene.play(*[FadeIn(m) for m in mobjects], run_time=run_time)


def clear_scene(scene, run_time: float = 0.6):
    """Fade everything out to reach a clean ending state."""
    if scene.mobjects:
        scene.play(*[FadeOut(m) for m in scene.mobjects], run_time=run_time)


# --------------------------------------------------------------------------- #
# DSA & Code Primitives (IDE Terminal & Data Structure Grid)
# --------------------------------------------------------------------------- #


def make_code_terminal(
    code_lines: list[str],
    title: str = "solution.py",
    width: float = 6.2,
    height: float = 4.6,
    font_size: int = 18,
    bg_color="#0b0f19",
    border_color="#1e293b",
) -> VGroup:
    """Build a Glassmorphic IDE Code Terminal window as a styled VGroup."""
    container = RoundedRectangle(
        corner_radius=0.25, width=width, height=height,
        stroke_color=border_color, stroke_width=2.5,
        fill_color=bg_color, fill_opacity=0.92,
    )
    
    top_y = container.get_top()[1]
    left_x = container.get_left()[0]
    
    dot_red = Dot(color=RED, radius=0.08).move_to([left_x + 0.35, top_y - 0.3, 0])
    dot_yellow = Dot(color=YELLOW, radius=0.08).next_to(dot_red, RIGHT, buff=0.15)
    dot_green = Dot(color=GREEN, radius=0.08).next_to(dot_yellow, RIGHT, buff=0.15)
    
    header_title = Text(str(title), font_size=14, color=GREY)
    header_title.move_to([container.get_center()[0], top_y - 0.3, 0])
    
    header = VGroup(dot_red, dot_yellow, dot_green, header_title)
    
    line_mobjects = []
    start_y = top_y - 0.8
    for i, line_str in enumerate(code_lines):
        txt = Text(str(line_str), font_size=font_size, color=WHITE)
        txt.move_to([left_x + 0.4 + txt.width / 2.0, start_y - (i * 0.45), 0])
        line_mobjects.append(txt)
    
    lines_group = VGroup(*line_mobjects)
    
    term = VGroup(container, header, lines_group)
    term.line_objects = line_mobjects
    term.container = container
    return term


def make_array_grid(
    values: list,
    indices: bool = True,
    width: float = 1.1,
    height: float = 1.1,
    gap: float = 0.15,
    cell_color="#38bdf8",
    bg_color="#0f172a",
) -> VGroup:
    """Build a horizontal 2.5D memory cell grid with index labels."""
    cells = []
    val_texts = []
    idx_labels = []
    
    for i, val in enumerate(values):
        box = RoundedRectangle(
            corner_radius=0.12, width=width, height=height,
            stroke_color=cell_color, stroke_width=2.0,
            fill_color=bg_color, fill_opacity=0.85,
        )
        val_txt = Text(str(val), font_size=24, color=WHITE)
        val_txt.move_to(box.get_center())
        
        cells.append(box)
        val_texts.append(val_txt)
        
        if indices:
            idx_txt = Text(str(i), font_size=16, color=GREY)
            idx_txt.next_to(box, DOWN, buff=0.2)
            idx_labels.append(idx_txt)
            
    grid_group = VGroup()
    for i in range(len(values)):
        if indices:
            unit = VGroup(cells[i], val_texts[i], idx_labels[i])
        else:
            unit = VGroup(cells[i], val_texts[i])
        grid_group.add(unit)
        
    grid_group.arrange(RIGHT, buff=gap)
    grid_group.cells = cells
    grid_group.val_texts = val_texts
    grid_group.idx_labels = idx_labels
    return grid_group


__all__ = [
    "PALETTE", "TYPE_SCALE", "MIN_READABLE_FONT_SIZE", "SAFE_MARGIN",
    "safe_width", "safe_height", "fit_to_frame",
    "styled_title", "body_text", "caption", "make_node", "make_box", "connect",
    "token_chip", "prob_bar", "highlight", "row", "column", "grid",
    "focus_on", "restore_focus", "morph", "reveal", "clear_scene",
    "make_code_terminal", "make_array_grid",
]
