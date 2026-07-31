"""Safe FFmpeg / FFprobe helpers.

All subprocess calls use argument *lists* (never shell strings), capture stderr,
and enforce explicit timeouts. Command construction is split into pure
``build_*_cmd`` functions so it can be unit-tested without invoking FFmpeg.

Design choices:

* Audio fragments are concatenated **and re-encoded** to a single normalized
  AAC/m4a track (44.1 kHz stereo) — replacing the previous invalid binary MP3
  concatenation.
* Scene videos use a lossless-to-source stream-copy fast path because every
  clip in a job is rendered by the same Manim quality preset. If FFmpeg reports
  incompatible inputs, assembly automatically falls back to the prior uniform
  H.264 / yuv420p transcode instead of risking a broken output.
* The final mux copies the already-normalized H.264 video and AAC narration,
  avoiding a second lossy AAC encode. It falls back to audio transcoding only
  for an incompatible external track.
* The final output is validated with FFprobe before a job is marked complete.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

FFMPEG_BIN = os.getenv("FFMPEG_BINARY", "ffmpeg")
FFPROBE_BIN = os.getenv("FFPROBE_BINARY", "ffprobe")

DEFAULT_TIMEOUT = 600  # seconds


class FFmpegError(RuntimeError):
    """Raised when an FFmpeg/FFprobe invocation fails."""


# --------------------------------------------------------------------------- #
# Pure command builders (unit-testable)
# --------------------------------------------------------------------------- #


def build_concat_list_text(paths: Sequence[os.PathLike | str]) -> str:
    """Build the text body for FFmpeg's concat demuxer list file.

    Uses absolute POSIX paths and escapes single quotes so paths with spaces or
    apostrophes are handled safely on every platform.
    """
    lines = []
    for p in paths:
        posix = Path(p).resolve().as_posix()
        safe = posix.replace("'", "'\\''")
        lines.append(f"file '{safe}'")
    return "\n".join(lines) + "\n"


def build_concat_audio_cmd(list_file: str | os.PathLike, output: str | os.PathLike) -> List[str]:
    """Concatenate audio fragments into one normalized AAC (.m4a) track."""
    return [
        FFMPEG_BIN, "-hide_banner", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-ar", "44100", "-ac", "2",
        "-c:a", "aac", "-b:a", "192k",
        str(output),
    ]


def build_concat_video_cmd(list_file: str | os.PathLike, output: str | os.PathLike) -> List[str]:
    """Concatenate compatible Manim scenes without decoding or re-encoding."""
    return [
        FFMPEG_BIN, "-hide_banner", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-map", "0:v:0",
        "-c:v", "copy",
        "-an",
        "-movflags", "+faststart",
        str(output),
    ]


def build_concat_video_transcode_cmd(
    list_file: str | os.PathLike,
    output: str | os.PathLike,
) -> List[str]:
    """Compatibility fallback: normalize scenes to H.264 / yuv420p."""
    return [
        FFMPEG_BIN, "-hide_banner", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-an",
        "-movflags", "+faststart",
        str(output),
    ]


def build_mux_cmd(
    video: str | os.PathLike,
    audio: str | os.PathLike,
    output: str | os.PathLike,
) -> List[str]:
    """Mux normalized H.264 + AAC streams into a faststart MP4 without loss."""
    return [
        FFMPEG_BIN, "-hide_banner", "-y",
        "-i", str(video),
        "-i", str(audio),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output),
    ]


def build_mux_transcode_audio_cmd(
    video: str | os.PathLike,
    audio: str | os.PathLike,
    output: str | os.PathLike,
) -> List[str]:
    """Compatibility fallback that copies video and normalizes audio to AAC."""
    return [
        FFMPEG_BIN, "-hide_banner", "-y",
        "-i", str(video),
        "-i", str(audio),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output),
    ]


def build_probe_cmd(path: str | os.PathLike) -> List[str]:
    """FFprobe command returning format + stream info as JSON."""
    return [
        FFPROBE_BIN, "-v", "error",
        "-show_format", "-show_streams",
        "-print_format", "json",
        str(path),
    ]


# --------------------------------------------------------------------------- #
# Runners
# --------------------------------------------------------------------------- #


def _run(cmd: Sequence[str], timeout: int = DEFAULT_TIMEOUT) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            list(cmd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise FFmpegError(f"command timed out after {timeout}s: {cmd[0]}") from exc
    except FileNotFoundError as exc:
        raise FFmpegError(
            f"'{cmd[0]}' not found. Install FFmpeg and ensure it is on PATH."
        ) from exc


def _tail(text: Optional[str], limit: int = 1200) -> str:
    if not text:
        return ""
    text = text.strip()
    return text[-limit:]


def _write_concat_list(paths: Sequence[str], list_file: Path) -> List[str]:
    existing = [p for p in paths if os.path.exists(p)]
    list_file.parent.mkdir(parents=True, exist_ok=True)
    list_file.write_text(build_concat_list_text(existing), encoding="utf-8")
    return existing


def concat_audio(
    fragment_paths: Sequence[str],
    output_path: str | os.PathLike,
    list_file: str | os.PathLike,
    timeout: int = DEFAULT_TIMEOUT,
) -> bool:
    """Concatenate + re-encode audio fragments into one valid narration file."""
    if not fragment_paths:
        raise FFmpegError("no audio fragments to concatenate")
    list_path = Path(list_file)
    existing = _write_concat_list(list(fragment_paths), list_path)
    missing = [p for p in fragment_paths if not os.path.exists(p)]
    if missing:
        print(f"  [WARNING] {len(missing)} audio fragment(s) missing, skipping: {missing}")
    if not existing:
        raise FFmpegError("all audio fragments are missing on disk")

    result = _run(build_concat_audio_cmd(list_path, output_path), timeout=timeout)
    if result.returncode != 0 or not os.path.exists(output_path):
        raise FFmpegError(f"audio concat failed: {_tail(result.stderr)}")
    return True


def concat_video(
    video_paths: Sequence[str],
    output_path: str | os.PathLike,
    list_file: str | os.PathLike,
    timeout: int = DEFAULT_TIMEOUT,
) -> bool:
    """Concatenate scene videos in order, preferring quality-preserving copy.

    Manim scenes from one job share codec, resolution, frame rate, and pixel
    format. Copying their encoded packets avoids a redundant full-video
    transcode and cannot reduce image quality. The former normalization path
    remains as an automatic fallback for legacy or externally supplied clips
    whose streams are incompatible.
    """
    if not video_paths:
        raise FFmpegError("no videos to concatenate")
    list_path = Path(list_file)
    existing = _write_concat_list(list(video_paths), list_path)
    if not existing:
        raise FFmpegError("all scene videos are missing on disk")

    result = _run(build_concat_video_cmd(list_path, output_path), timeout=timeout)
    if result.returncode == 0 and os.path.exists(output_path):
        return True

    copy_error = _tail(result.stderr)
    try:
        Path(output_path).unlink(missing_ok=True)
    except OSError:
        pass

    fallback = _run(
        build_concat_video_transcode_cmd(list_path, output_path),
        timeout=timeout,
    )
    if fallback.returncode != 0 or not os.path.exists(output_path):
        details = _tail(fallback.stderr) or copy_error
        raise FFmpegError(f"video concat failed: {details}")
    return True


def mux_video_audio(
    video_path: str | os.PathLike,
    audio_path: str | os.PathLike,
    output_path: str | os.PathLike,
    timeout: int = DEFAULT_TIMEOUT,
) -> bool:
    """Mux video + normalized narration, with an audio-transcode fallback."""
    if not os.path.exists(video_path):
        raise FFmpegError(f"video not found: {video_path}")
    if not os.path.exists(audio_path):
        raise FFmpegError(f"audio not found: {audio_path}")
    result = _run(build_mux_cmd(video_path, audio_path, output_path), timeout=timeout)
    if result.returncode == 0 and os.path.exists(output_path):
        return True

    copy_error = _tail(result.stderr)
    try:
        Path(output_path).unlink(missing_ok=True)
    except OSError:
        pass

    fallback = _run(
        build_mux_transcode_audio_cmd(video_path, audio_path, output_path),
        timeout=timeout,
    )
    if fallback.returncode != 0 or not os.path.exists(output_path):
        details = _tail(fallback.stderr) or copy_error
        raise FFmpegError(f"mux failed: {details}")
    return True


# --------------------------------------------------------------------------- #
# Probing / validation
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MediaInfo:
    """Parsed FFprobe summary for a media file."""

    duration: float
    has_video: bool
    has_audio: bool
    video_frames: Optional[int]
    width: Optional[int]
    height: Optional[int]


def parse_probe(probe_json: dict) -> MediaInfo:
    """Parse FFprobe JSON output into a :class:`MediaInfo` (pure/testable)."""
    fmt = probe_json.get("format", {}) or {}
    streams = probe_json.get("streams", []) or []

    duration = 0.0
    try:
        duration = float(fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0

    has_video = False
    has_audio = False
    frames: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None

    for stream in streams:
        codec_type = stream.get("codec_type")
        if codec_type == "video":
            has_video = True
            width = stream.get("width")
            height = stream.get("height")
            for key in ("nb_frames", "nb_read_frames"):
                raw = stream.get(key)
                if raw not in (None, "N/A"):
                    try:
                        frames = int(raw)
                        break
                    except (TypeError, ValueError):
                        pass
            if duration <= 0:
                try:
                    duration = float(stream.get("duration") or 0.0)
                except (TypeError, ValueError):
                    pass
        elif codec_type == "audio":
            has_audio = True

    return MediaInfo(
        duration=duration,
        has_video=has_video,
        has_audio=has_audio,
        video_frames=frames,
        width=width,
        height=height,
    )


def probe_media(path: str | os.PathLike, timeout: int = 60) -> MediaInfo:
    """Run FFprobe against ``path`` and return parsed :class:`MediaInfo`."""
    result = _run(build_probe_cmd(path), timeout=timeout)
    if result.returncode != 0:
        raise FFmpegError(f"ffprobe failed: {_tail(result.stderr)}")
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise FFmpegError(f"ffprobe returned invalid JSON: {exc}") from exc
    return parse_probe(data)


def validate_output(
    path: str | os.PathLike,
    expect_audio: bool,
    expected_duration: Optional[float] = None,
    tolerance: float = 0.5,
    min_tolerance_seconds: float = 3.0,
) -> Tuple[bool, str, Optional[MediaInfo]]:
    """Validate a final render. Returns ``(ok, reason, info)``.

    Checks: file exists, non-zero size, duration > 0, a video stream exists,
    the video has frames, an audio stream exists when TTS was enabled, and the
    duration is within tolerance of ``expected_duration`` (when provided).
    """
    p = Path(path)
    if not p.exists():
        return False, "output file was not created", None
    if p.stat().st_size == 0:
        return False, "output file is empty (0 bytes)", None

    try:
        info = probe_media(p)
    except FFmpegError as exc:
        return False, f"could not probe output: {exc}", None

    if not info.has_video:
        return False, "output has no video stream", info
    if info.duration <= 0:
        return False, "output duration is zero", info
    if info.video_frames is not None and info.video_frames <= 0:
        return False, "output video has no frames", info
    if expect_audio and not info.has_audio:
        return False, "TTS was enabled but output has no audio stream", info

    if expected_duration and expected_duration > 0:
        allowed = max(min_tolerance_seconds, expected_duration * tolerance)
        if abs(info.duration - expected_duration) > allowed:
            return (
                False,
                (
                    f"output duration {info.duration:.1f}s differs from expected "
                    f"{expected_duration:.1f}s by more than {allowed:.1f}s"
                ),
                info,
            )

    return True, "ok", info
