"""Per-job media/workspace path management.

Every generation job gets an isolated workspace under ``media/jobs/<job_id>/``
so concurrent jobs never collide on temporary audio/video/code files. All paths
are derived from the project root (never the process CWD) using ``pathlib``.

Reusable content-hash caches live under ``media/`` *outside* the per-job trees so
they are shared and never clobbered by job cleanup. Cache keys must include the
``CACHE_VERSION`` below so bumping it transparently invalidates stale entries.
"""

from __future__ import annotations

from pathlib import Path

# Bump when the cache key formula or stored artifact format changes.
# v3: scene cache key incorporates storyboard visual direction.
# v4: rendered scenes are isolated by effective Manim quality preset.
# v5: scripts must satisfy the stricter requested-duration contract.
CACHE_VERSION = "v5"

# Project root = parent of the ``src`` directory that holds this module.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
MEDIA_DIR = PROJECT_ROOT / "media"
CONTENT_DIR = PROJECT_ROOT / "content"
JOBS_DIR = MEDIA_DIR / "jobs"

# Shared, reusable caches (content-addressed; never inside a job workspace).
SCRIPT_CACHE_DIR = MEDIA_DIR / "script_cache"
SCENE_CACHE_DIR = MEDIA_DIR / "scene_cache"
TTS_CACHE_DIR = MEDIA_DIR / "tts_cache"


def ensure_base_dirs() -> None:
    """Create the top-level media/content directories if missing."""
    for d in (MEDIA_DIR, CONTENT_DIR, JOBS_DIR):
        d.mkdir(parents=True, exist_ok=True)


class JobWorkspace:
    """Isolated on-disk workspace for a single generation job.

    Layout::

        media/jobs/<job_id>/
            audio/    TTS fragments + final narration
            scenes/   rendered per-scene mp4s
            code/     generated Manim .py source
            video/    concat lists + silent concat output
            logs/     per-job log file + manifests
            final/    final muxed mp4 (user-facing output)
    """

    def __init__(self, job_id: str, base_dir: Path | None = None) -> None:
        self.job_id = job_id
        base = base_dir if base_dir is not None else JOBS_DIR
        self.root = base / job_id
        self.audio = self.root / "audio"
        self.scenes = self.root / "scenes"
        self.code = self.root / "code"
        self.video = self.root / "video"
        self.logs = self.root / "logs"
        self.final = self.root / "final"
        # Visual-QA artifacts (frames, contact sheet, QA report).
        self.qa = self.root / "qa"

    def create(self) -> "JobWorkspace":
        """Create all subdirectories. Idempotent."""
        for d in (self.root, self.audio, self.scenes, self.code, self.video,
                  self.logs, self.final, self.qa):
            d.mkdir(parents=True, exist_ok=True)
        return self

    # --- audio ---------------------------------------------------------- #
    def audio_fragment(self, index: int) -> Path:
        """Unique TTS fragment path for a scene (per job + scene)."""
        return self.audio / f"fragment_{index:03d}.mp3"

    def final_audio(self) -> Path:
        return self.audio / "narration.m4a"

    # --- code / scenes -------------------------------------------------- #
    def scene_code(self, index: int) -> Path:
        return self.code / f"scene_{index:03d}.py"

    def scene_timing(self, index: int) -> Path:
        """Where the rendered scene records its pre-pad animation length."""
        return self.logs / f"scene_{index:03d}_timing.json"

    def scene_video(self, index: int) -> Path:
        return self.scenes / f"scene_{index:03d}.mp4"

    def scene_media_dir(self, index: int) -> Path:
        """Private Manim ``--media_dir`` for one scene.

        Manim caches rendered text/images under ``<media_dir>/texts`` and
        ``<media_dir>/images`` using **content-hashed** filenames. Two scenes
        that render the same string therefore target the same file, and if they
        render concurrently one process can read a half-written SVG — measured
        failure rate was 3 of 4 concurrent renders with
        ``ParseError: no element found``.

        Giving each scene its own media_dir removes that shared state entirely
        (verified: 0 failures over 3 trials of 4 concurrent renders).
        """
        return self.video / f"scene_{index:03d}"

    # --- video / concat ------------------------------------------------- #
    def concat_list(self) -> Path:
        return self.video / "concat_list.txt"

    def silent_video(self) -> Path:
        return self.video / "silent.mp4"

    # --- final ---------------------------------------------------------- #
    def final_video(self) -> Path:
        return self.final / f"output_{self.job_id}.mp4"

    # --- logs ----------------------------------------------------------- #
    def log_file(self) -> Path:
        return self.logs / "job.log"

    def manifest_file(self) -> Path:
        return self.logs / "manifest.json"

    # --- visual direction ----------------------------------------------- #
    def storyboard_file(self) -> Path:
        return self.logs / "storyboard.json"

    def visual_ledger_file(self) -> Path:
        return self.logs / "visual_ledger.json"

    # --- visual QA ------------------------------------------------------ #
    def visual_primitives_copy(self) -> Path:
        """Where the primitives helper is copied so scenes can import it."""
        return self.code / "visual_primitives.py"

    def scene_frame(self, index: int, k: int) -> Path:
        return self.qa / f"frame_{index:03d}_{k}.png"

    def scene_thumb(self, index: int) -> Path:
        """Representative thumbnail for a scene (used in the contact sheet)."""
        return self.qa / f"thumb_{index:03d}.png"

    def contact_sheet(self) -> Path:
        return self.qa / "contact_sheet.png"

    def visual_qa_file(self) -> Path:
        return self.qa / "visual_qa.json"
