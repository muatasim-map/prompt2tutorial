"""Manim compilation + FFmpeg-based video assembly.

* ``compile_video`` renders a single scene with Manim into a **job-scoped**
  media directory and returns the absolute output path (no reliance on CWD).
* ``concatenate_videos`` / ``merge_video_and_audio`` delegate to
  :mod:`ffmpeg_utils`, replacing the previous fragile PyAV raw-packet handling.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence, Tuple

from ffmpeg_utils import FFmpegError, concat_video, mux_video_audio

# Manim quality presets -> (CLI flag, output subfolder). ``low`` (480p15) is the
# fast default; ``high`` (1080p60) is available via the MANIM_QUALITY env var.
_QUALITY_PRESETS = {
    "low": ("-ql", "480p15"),
    "medium": ("-qm", "720p30"),
    "high": ("-qh", "1080p60"),
}
# Backwards-compatible module constants (default = low).
MANIM_QUALITY_FLAG, MANIM_QUALITY_DIR = _QUALITY_PRESETS["low"]


def _resolve_quality() -> Tuple[str, str]:
    """Return (flag, output_subdir) for the configured MANIM_QUALITY."""
    quality = os.getenv("MANIM_QUALITY", "low").strip().lower()
    return _QUALITY_PRESETS.get(quality, _QUALITY_PRESETS["low"])


# A normal low-quality scene renders in well under 20s; medium/high take longer.
# These bounds fail-fast on a genuine hang (which would otherwise stall a whole
# job) while leaving generous headroom for legitimate complex/3D/graph scenes.
_COMPILE_TIMEOUTS = {"low": 120, "medium": 240, "high": 300}

# True 3D scenes (mesh Surfaces, multiple solids, camera-space geometry) cost
# meaningfully more render time on this cairo/CPU renderer than a flat or
# layered-2.5D scene of the same duration. The 2D-calibrated timeout above was
# observed causing repeated false-timeout failures on 3D scenes (3 consecutive
# 120s timeouts before any repair could even help) — this multiplier gives 3D
# scenes a realistic budget instead of one tuned for 2D.
# Successful simplified 3D attempts in the supplied job completed around
# 180-185s. A 1.75x allowance leaves headroom at low quality (210s) without
# letting a doomed first attempt consume the old five-minute limit.
_THREED_TIMEOUT_MULTIPLIER = 1.75


def resolve_compile_timeout(is_3d: bool = False) -> int:
    """Timeout (s) for a single Manim render, scaled to quality (and dimension).

    Overridable via MANIM_COMPILE_TIMEOUT (applied BEFORE the 3D multiplier, so
    an explicit override still gets the 3D allowance on top). Never below 60s.
    """
    override = os.getenv("MANIM_COMPILE_TIMEOUT")
    if override:
        try:
            base = max(60, int(float(override)))
        except (TypeError, ValueError):
            base = None
    else:
        base = None
    if base is None:
        quality = os.getenv("MANIM_QUALITY", "low").strip().lower()
        base = _COMPILE_TIMEOUTS.get(quality, _COMPILE_TIMEOUTS["low"])
    if is_3d:
        base = int(round(base * _THREED_TIMEOUT_MULTIPLIER))
    return base


def _manim_env(scene_dir: Path) -> dict:
    """Env for the Manim subprocess so generated scenes can import helpers.

    Adds the scene's own directory (where ``visual_primitives.py`` is copied) and
    this ``src`` directory to PYTHONPATH.
    """
    env = os.environ.copy()
    src_dir = str(Path(__file__).resolve().parent)
    extra = os.pathsep.join([str(scene_dir), src_dir])
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (extra + os.pathsep + existing) if existing else extra
    return env


def sanitize_filename(filename: str) -> str:
    """Remove characters that are problematic in filenames."""
    sanitized = re.sub(r"['\"\?!:;,\(\)\[\]\{\}]", "", filename)
    sanitized = re.sub(r"_+", "_", sanitized)
    return sanitized.strip("_")


def _resolve_manim_executable() -> str:
    """Locate the manim executable in the active virtualenv, else fall back."""
    py_bin_dir = Path(sys.executable).parent
    for candidate in (py_bin_dir / "manim.exe", py_bin_dir / "manim"):
        if candidate.exists():
            return str(candidate)
    return "manim"


def _build_manim_command(
    file_path: str | os.PathLike,
    class_name: str,
    media_dir: str | os.PathLike,
    *,
    quality_flag: str,
) -> list[str]:
    """Build a quiet, deterministic one-scene Manim command."""
    return [
        _resolve_manim_executable(),
        quality_flag,
        "--progress_bar", "none",
        "-v", "WARNING",
        "--disable_caching",
        "--media_dir", str(media_dir),
        str(file_path),
        class_name,
    ]


def _terminate_process_tree(process: subprocess.Popen) -> None:
    """Terminate ``process`` and every descendant without waiting on pipes."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            process.kill()
        return
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (OSError, ProcessLookupError):
        process.kill()


def _run_process_tree(
    cmd: Sequence[str],
    *,
    timeout: float,
    env: Optional[dict] = None,
) -> subprocess.CompletedProcess:
    """Run a command with a hard timeout that kills descendant processes too."""
    popen_kwargs = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "env": env,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    process = subprocess.Popen(list(cmd), **popen_kwargs)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _terminate_process_tree(process)
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
        raise
    return subprocess.CompletedProcess(
        args=list(cmd),
        returncode=process.returncode,
        stdout=stdout,
        stderr=stderr,
    )


def compile_video(
    file_path: str | os.PathLike,
    class_name: str,
    media_dir: str | os.PathLike,
    timeout: Optional[int] = None,
    is_3d: bool = False,
) -> Tuple[Optional[str], Optional[str]]:
    """Render one Manim scene into ``media_dir`` and return its absolute path.

    Args:
        file_path: Path to the generated ``.py`` scene file.
        class_name: The ``Scene`` subclass to render.
        media_dir: Job-scoped Manim media output root (keeps renders isolated).
        timeout: Per-scene render timeout in seconds. When ``None`` it is scaled
            to the render quality (see :func:`resolve_compile_timeout`), so a
            hung render fails fast instead of stalling the whole job for 5 min.
        is_3d: Whether this scene is a ThreeDScene. Only used when ``timeout``
            is ``None`` — gives 3D scenes a realistic budget instead of the
            2D-calibrated default (a 3D scene can legitimately take longer to
            render than the same duration in 2D).

    Returns:
        ``(video_path, error_message)`` — ``video_path`` is ``None`` on failure;
        ``error_message`` is ``None`` on success.
    """
    file_path = Path(file_path)
    media_dir = Path(media_dir)
    media_dir.mkdir(parents=True, exist_ok=True)
    quality_flag, quality_dir = _resolve_quality()
    if timeout is None:
        timeout = resolve_compile_timeout(is_3d=is_3d)

    cmd = _build_manim_command(
        file_path,
        class_name,
        media_dir,
        quality_flag=quality_flag,
    )
    print(f"\nCompiling: {' '.join(cmd)}")

    try:
        result = _run_process_tree(
            cmd,
            timeout=timeout,
            env=_manim_env(file_path.parent),
        )
    except subprocess.TimeoutExpired:
        return None, f"Timeout: compilation exceeded {timeout} seconds"
    except FileNotFoundError as exc:
        return None, f"manim executable not found: {exc}"
    except Exception as exc:  # pragma: no cover - defensive
        return None, str(exc)

    if result.returncode != 0:
        print("[ERROR] Manim compilation failed")
        return None, result.stderr or result.stdout or "unknown manim error"

    filename_no_ext = file_path.stem
    video_path = media_dir / "videos" / filename_no_ext / quality_dir / f"{class_name}.mp4"
    if not video_path.exists():
        return None, f"manim reported success but output missing: {video_path}"
    print(f"[OK] Video compiled: {video_path}")
    return str(video_path), None


def concatenate_videos(
    video_paths: Sequence[str],
    output_path: str | os.PathLike,
    list_file: str | os.PathLike,
) -> bool:
    """Concatenate scene videos into one silent clip (FFmpeg, order preserved)."""
    try:
        return concat_video(video_paths, output_path, list_file)
    except FFmpegError as exc:
        print(f"[ERROR] Video concatenation failed: {exc}")
        return False


def merge_video_and_audio(
    video_path: str | os.PathLike,
    audio_path: str | os.PathLike,
    output_path: str | os.PathLike,
) -> bool:
    """Mux video + narration into a browser-compatible faststart MP4 (FFmpeg)."""
    try:
        return mux_video_audio(video_path, audio_path, output_path)
    except FFmpegError as exc:
        print(f"[ERROR] Merging video and audio failed: {exc}")
        return False
