"""Build fingerprint for the running server.

Why this exists: ``main.py`` runs Flask with ``use_reloader=False`` (to avoid losing
in-memory job state), so a server started before a code change keeps the OLD modules
resident indefinitely. That is exactly how a fixed bug appeared to "still happen":
the process was executing pre-fix code while the working tree was already patched.

The fingerprint is a hash of the source files actually imported, so it changes the
moment the code on disk changes. It is printed at startup, exposed on ``/api/health``
and recorded in every job's metadata + log, making a stale process obvious.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent


def compute_build_id() -> str:
    """Short, stable hash of all Python sources in ``src/``."""
    digest = hashlib.sha256()
    for path in sorted(SRC_DIR.glob("*.py")):
        try:
            digest.update(path.name.encode("utf-8"))
            digest.update(path.read_bytes())
        except OSError:
            continue
    return digest.hexdigest()[:12]


# Computed once at import time: this is the build the process is actually running.
BUILD_ID = compute_build_id()
STARTED_AT = datetime.now().isoformat(timespec="seconds")


def build_info() -> dict:
    """Safe, secret-free build info for health checks and job metadata."""
    return {
        "build_id": BUILD_ID,
        "started_at": STARTED_AT,
        "source_dir": str(SRC_DIR),
        "stale": BUILD_ID != compute_build_id(),
    }
