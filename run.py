"""Project launcher for local development.

Run with ``python run.py``. When a project virtual environment exists, the
launcher re-runs itself with that interpreter so Flask dependencies are found.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def _use_project_venv() -> bool:
    """Re-exec with .venv on Windows when the caller used system Python."""
    project_root = Path(__file__).resolve().parent
    venv_python = project_root / ".venv" / "Scripts" / "python.exe"
    if not venv_python.exists() or Path(sys.executable).resolve() == venv_python.resolve():
        return False

    completed = subprocess.run([str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])
    raise SystemExit(completed.returncode)


if __name__ == "__main__":
    _use_project_venv()
    from src.main import main

    main()
