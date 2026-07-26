"""Visual QA artifacts and heuristic checks.

Extracts representative frames from each rendered scene (FFmpeg), analyzes them
with Pillow + numpy, builds a per-job contact sheet, and records heuristic
visual-quality flags. Flags are advisory only — they are recorded in the job's
QA report/status for human review and NEVER auto-reject a scene (except the
blank/black case, which the caller may treat as a technical failure eligible for
one scene-level visual repair).

Command builders are pure so they can be unit-tested without invoking FFmpeg.
"""

from __future__ import annotations

import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np

from config import VisualConfig
from ffmpeg_utils import FFMPEG_BIN, FFmpegError, probe_media

# Flag identifiers (stable strings for status/report parsing).
FLAG_BLANK = "blank_or_black"
FLAG_WHITE = "nearly_white_empty"
FLAG_LOW_VARIANCE = "low_variance_no_content"
FLAG_EDGE_CLIP = "content_near_edge_possible_clipping"
FLAG_NO_CHANGE = "no_meaningful_change"
FLAG_NEAR_IDENTICAL = "near_identical_to_previous_scene"
FLAG_TEXT_ONLY = "text_only_scene"
FLAG_REPEAT_LAYOUT = "likely_repeated_layout"
FLAG_REPEAT_METAPHOR = "likely_repeated_metaphor"
FLAG_STATIC_END_PADDING = "static_end_padding"
FLAG_LONG_STATIC_RUN = "long_static_run"
FLAG_AV_DRIFT = "av_drift"

_THUMB_SIZE = (48, 27)  # 16:9 downscale for cross-frame comparison


# --------------------------------------------------------------------------- #
# Pure command builders
# --------------------------------------------------------------------------- #


def build_extract_frame_cmd(
    video: str | os.PathLike, timestamp: float, out_path: str | os.PathLike
) -> List[str]:
    """FFmpeg command extracting a single frame at ``timestamp`` seconds."""
    return [
        FFMPEG_BIN, "-hide_banner", "-y",
        "-ss", f"{max(0.0, float(timestamp)):.3f}",
        "-i", str(video),
        "-frames:v", "1", "-q:v", "2",
        str(out_path),
    ]


def frame_timestamps(duration: float, n: int) -> List[float]:
    """Evenly spaced sample timestamps within a clip (avoiding the very edges)."""
    duration = max(0.0, float(duration))
    n = max(1, int(n))
    if n == 1:
        return [duration * 0.5]
    return [duration * (i + 1) / (n + 1) for i in range(n)]


# --------------------------------------------------------------------------- #
# Frame analysis (Pillow + numpy)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FrameStats:
    """Grayscale statistics for a single extracted frame."""

    mean: float
    stddev: float
    content_ratio: float
    edge_content_ratio: float
    width: int
    height: int


def _load_gray(path: str | os.PathLike) -> np.ndarray:
    from PIL import Image

    with Image.open(path) as img:
        return np.asarray(img.convert("L"), dtype=np.float64)


def analyze_array(arr: np.ndarray) -> FrameStats:
    """Compute :class:`FrameStats` from a 2-D grayscale array (pure, testable)."""
    if arr.ndim != 2 or arr.size == 0:
        return FrameStats(0.0, 0.0, 0.0, 0.0, 0, 0)
    h, w = arr.shape
    median = float(np.median(arr))
    diff = np.abs(arr - median)
    content = diff > 24.0
    content_ratio = float(content.mean())

    mh = max(1, int(h * 0.03))
    mw = max(1, int(w * 0.03))
    border = np.zeros_like(content, dtype=bool)
    border[:mh, :] = True
    border[-mh:, :] = True
    border[:, :mw] = True
    border[:, -mw:] = True
    edge_ratio = float(content[border].mean()) if border.any() else 0.0

    return FrameStats(
        mean=float(arr.mean()),
        stddev=float(arr.std()),
        content_ratio=content_ratio,
        edge_content_ratio=edge_ratio,
        width=w,
        height=h,
    )


def analyze_frame(path: str | os.PathLike) -> FrameStats:
    return analyze_array(_load_gray(path))


def flag_frame(stats: FrameStats, cfg: VisualConfig) -> List[str]:
    """Return heuristic flags for a single frame (pure, testable).

    Blankness is judged by how little *content* a frame carries (dark Manim
    backgrounds are normal), not by absolute brightness.
    """
    flags: List[str] = []
    if stats.content_ratio < cfg.blank_content_ratio:
        flags.append(FLAG_BLANK)
    if stats.mean >= cfg.white_min_brightness:
        flags.append(FLAG_WHITE)
    if stats.stddev < cfg.min_stddev and FLAG_BLANK not in flags:
        flags.append(FLAG_LOW_VARIANCE)
    if stats.edge_content_ratio > cfg.edge_content_ratio:
        flags.append(FLAG_EDGE_CLIP)
    return flags


def longest_static_run(maes: Sequence[float], threshold: float, seconds_per_step: float) -> float:
    """Longest consecutive stretch (seconds) whose frame-to-frame MAE stays below
    ``threshold`` — i.e. the longest visually frozen passage (pure/testable)."""
    best = run = 0
    for m in maes:
        if m < threshold:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best * float(seconds_per_step)


def trailing_static_run(maes: Sequence[float], threshold: float, seconds_per_step: float) -> float:
    """Length in seconds of the frozen stretch at the END of a clip (pure/testable)."""
    run = 0
    for m in reversed(list(maes)):
        if m < threshold:
            run += 1
        else:
            break
    return run * float(seconds_per_step)


def mae_between(a: np.ndarray, b: np.ndarray) -> float:
    """Mean absolute error between two equally-shaped arrays."""
    if a.shape != b.shape or a.size == 0:
        return 255.0
    return float(np.abs(a.astype(np.float64) - b.astype(np.float64)).mean())


# A pixel must shift by more than this to count as having changed. Set above
# codec/antialiasing noise on a downscaled thumbnail, below any real redraw.
MOTION_PIXEL_DELTA = 12.0
# Below this many content pixels there is nothing on screen that *could* move,
# so motion is reported as zero rather than letting the small denominator
# amplify compression noise into a phantom "the scene is moving" signal.
MOTION_MIN_CONTENT_PX = 25


def motion_between(a: np.ndarray, b: np.ndarray) -> float:
    """Fraction of the VISIBLE CONTENT that changed between two frames.

    Replaces raw :func:`mae_between` for motion detection. MAE averages over the
    whole canvas, and Manim frames are 95-98% background: measured on the 13-run
    benchmark, a scene that was demonstrably animating throughout (45 of 46
    frame pairs carried real pixel change) had a maximum MAE of 1.11 against a
    threshold of 2.0, so every pair was scored "static". That single defect is
    why ``long_static_run`` fired on ~72-84% of all scenes.

    Normalising by the content area asks the question that actually matters —
    "what fraction of the ink moved?" — so a dot sliding across an otherwise
    empty frame registers as motion while a genuinely held frame does not.

    Returns 0.0 for identical frames; ~1.0 when the visible content has fully
    relocated. Pure and cheap: two comparisons and a sum.
    """
    if a.shape != b.shape or a.size == 0:
        return 0.0
    a = a.astype(np.float64)
    b = b.astype(np.float64)
    content_a = np.abs(a - np.median(a)) > 24.0
    content_b = np.abs(b - np.median(b)) > 24.0
    content_px = int((content_a | content_b).sum())
    if content_px < MOTION_MIN_CONTENT_PX:
        return 0.0
    changed = int((np.abs(a - b) > MOTION_PIXEL_DELTA).sum())
    return changed / float(content_px)


def _thumb_array(path: str | os.PathLike) -> np.ndarray:
    from PIL import Image

    with Image.open(path) as img:
        small = img.convert("L").resize(_THUMB_SIZE)
        return np.asarray(small, dtype=np.float64)


def images_near_identical(path_a: str | os.PathLike, path_b: str | os.PathLike,
                          threshold: float) -> bool:
    return mae_between(_thumb_array(path_a), _thumb_array(path_b)) < threshold


def storyboard_metadata_flags(entry: Optional[dict]) -> List[str]:
    """Flags derived from storyboard metadata (text-only heuristic)."""
    if not entry:
        return []
    flags: List[str] = []
    objects = [str(o).lower() for o in (entry.get("primary_objects") or [])]
    composition = str(entry.get("composition", "")).lower()
    metaphor = str(entry.get("visual_metaphor", "")).lower()
    text_words = ("text", "word", "label", "title", "caption", "bullet", "sentence", "paragraph")
    only_text_objects = bool(objects) and all(any(t in o for t in text_words) for o in objects)
    text_composition = any(t in composition for t in ("text", "title card", "bullet"))
    text_metaphor = any(t in metaphor for t in ("text", "title card", "bullet list"))
    if only_text_objects or text_composition or text_metaphor:
        flags.append(FLAG_TEXT_ONLY)
    return flags


# --------------------------------------------------------------------------- #
# Frame extraction + per-scene QA (runners)
# --------------------------------------------------------------------------- #


def _run(cmd: Sequence[str], timeout: int = 120) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(list(cmd), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise FFmpegError(f"frame extraction timed out: {cmd[0]}") from exc
    except FileNotFoundError as exc:
        raise FFmpegError(f"'{cmd[0]}' not found on PATH") from exc


def extract_frames(video: str | os.PathLike, timestamps: Sequence[float],
                   out_paths: Sequence[Path]) -> List[Path]:
    """Extract one frame per timestamp; returns the paths that were created.

    Each timestamp is an independent ffmpeg seek writing to its own file, so the
    extractions run concurrently. The returned list is re-sorted into timestamp
    order, so downstream motion analysis sees the same sequence as before.
    """
    pairs = list(zip(timestamps, out_paths))
    for _ts, out in pairs:
        out.parent.mkdir(parents=True, exist_ok=True)

    def _extract(pair):
        ts, out = pair
        try:
            result = _run(build_extract_frame_cmd(video, ts, out))
        except FFmpegError:
            return None
        if result.returncode == 0 and out.exists() and out.stat().st_size > 0:
            return out
        return None

    if len(pairs) > 1:
        with ThreadPoolExecutor(max_workers=min(4, len(pairs))) as pool:
            results = list(pool.map(_extract, pairs))
    else:
        results = [_extract(p) for p in pairs]

    # Preserve chronological order (index i corresponds to timestamps[i]).
    return [p for p in results if p is not None]


def analyze_scene(
    video_path: str | os.PathLike,
    index: int,
    frame_paths_for_index,
    thumb_path: Path,
    cfg: VisualConfig,
    storyboard_entry: Optional[dict] = None,
) -> dict:
    """Extract frames for one scene, analyze them, and record flags.

    ``frame_paths_for_index`` is a callable ``k -> Path`` for the k-th frame.
    Returns a QA dict for the scene (never raises on analysis issues).
    """
    report = {"index": index, "flags": [], "blank": False, "frames": []}
    try:
        info = probe_media(video_path)
        duration = info.duration
    except Exception:
        duration = 0.0

    timestamps = frame_timestamps(duration, cfg.frames_per_scene)
    out_paths = [frame_paths_for_index(k) for k in range(len(timestamps))]

    try:
        created = extract_frames(video_path, timestamps, out_paths)
    except FFmpegError as exc:
        report["flags"].append("frame_extraction_failed")
        report["error"] = str(exc)
        return report

    stats_list: List[FrameStats] = []
    for p in created:
        try:
            st = analyze_frame(p)
            stats_list.append(st)
            report["frames"].append({"path": p.name, **asdict(st)})
        except Exception:
            continue

    flags: List[str] = []
    if stats_list:
        # A scene is "blank" only if ALL sampled frames look blank/black.
        per_frame_flags = [flag_frame(s, cfg) for s in stats_list]
        if all(FLAG_BLANK in f for f in per_frame_flags):
            flags.append(FLAG_BLANK)
            report["blank"] = True
        # Aggregate the other frame flags (any frame).
        for name in (FLAG_WHITE, FLAG_LOW_VARIANCE, FLAG_EDGE_CLIP):
            if any(name in f for f in per_frame_flags):
                flags.append(name)
        # No meaningful change: first vs last sampled frame nearly identical.
        if len(created) >= 2:
            # Also content-normalised. Whole-canvas MAE was wrong in BOTH
            # directions here: measured, real scenes that build a whole diagram
            # scored only 2.1-2.8 against a threshold of 2.0 (so ~30% tripped
            # the flag), while a scene that rendered nothing but an empty grey
            # rectangle scored 13.8 and passed as "changed". Content-normalised
            # motion puts those the right way round — the real scenes score
            # >0.5 and the empty one scores 0.0.
            if motion_between(_thumb_array(created[0]),
                              _thumb_array(created[-1])) < cfg.min_scene_motion:
                flags.append(FLAG_NO_CHANGE)

            # Frozen passages: consecutive-frame motion across the sampled frames.
            try:
                thumbs = [_thumb_array(p) for p in created]
                # Content-normalised, NOT raw MAE — see motion_between for why
                # the mean over a 97%-black canvas cannot detect real motion.
                maes = [motion_between(thumbs[i - 1], thumbs[i]) for i in range(1, len(thumbs))]
                step = duration / max(1, len(created)) if duration else 0.0
                if step:
                    longest = longest_static_run(maes, cfg.min_scene_motion, step)
                    report["longest_static_run_seconds"] = round(longest, 2)
                    report["trailing_static_run_seconds"] = round(
                        trailing_static_run(maes, cfg.min_scene_motion, step), 2)
                    report["motion_scores"] = [round(m, 4) for m in maes]
                    if longest > cfg.max_static_run_seconds:
                        flags.append(FLAG_LONG_STATIC_RUN)
            except Exception:
                pass

    flags.extend(storyboard_metadata_flags(storyboard_entry))

    # Save a representative thumbnail (middle frame) for the contact sheet.
    if created:
        mid = created[len(created) // 2]
        try:
            from PIL import Image

            with Image.open(mid) as img:
                img.convert("RGB").save(thumb_path)
            report["thumb"] = thumb_path.name
        except Exception:
            pass

    report["flags"] = sorted(set(flags))
    return report


def flag_near_identical_scenes(thumb_paths: Sequence[Path], cfg: VisualConfig) -> List[Tuple[int, int]]:
    """Return (i, i+1) index pairs whose representative thumbnails are near-identical."""
    pairs: List[Tuple[int, int]] = []
    existing = [p for p in thumb_paths if p and Path(p).exists()]
    for i in range(1, len(existing)):
        try:
            if images_near_identical(existing[i - 1], existing[i], cfg.near_identical_mae):
                pairs.append((i - 1, i))
        except Exception:
            continue
    return pairs


# --------------------------------------------------------------------------- #
# Contact sheet
# --------------------------------------------------------------------------- #


def build_contact_sheet(
    thumb_paths: Sequence[Path],
    out_path: str | os.PathLike,
    cols: int = 3,
    cell: Tuple[int, int] = (320, 180),
    labels: Optional[Sequence[str]] = None,
) -> bool:
    """Compose scene thumbnails into a labelled grid PNG (Pillow). Returns success."""
    from PIL import Image, ImageDraw

    thumbs = [Path(p) for p in thumb_paths if p and Path(p).exists()]
    if not thumbs:
        return False

    cols = max(1, cols)
    rows = (len(thumbs) + cols - 1) // cols
    cw, ch = cell
    pad = 8
    label_h = 20
    sheet_w = cols * cw + (cols + 1) * pad
    sheet_h = rows * (ch + label_h) + (rows + 1) * pad

    sheet = Image.new("RGB", (sheet_w, sheet_h), (18, 18, 22))
    draw = ImageDraw.Draw(sheet)

    for i, tp in enumerate(thumbs):
        r, c = divmod(i, cols)
        x = pad + c * (cw + pad)
        y = pad + r * (ch + label_h + pad)
        try:
            with Image.open(tp) as im:
                im = im.convert("RGB")
                im.thumbnail((cw, ch))
                ox = x + (cw - im.width) // 2
                oy = y + (ch - im.height) // 2
                sheet.paste(im, (ox, oy))
        except Exception:
            continue
        label = labels[i] if labels and i < len(labels) else f"Scene {i + 1}"
        draw.text((x + 2, y + ch + 4), str(label)[:40], fill=(220, 220, 230))

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path)
    return True


def save_qa_report(report: dict, path: str | os.PathLike) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
