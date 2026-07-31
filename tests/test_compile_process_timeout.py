"""Regression coverage for hard Manim subprocess timeouts."""

import subprocess
import sys
import time

import pytest

import concat_video


def test_process_tree_timeout_returns_near_the_deadline():
    """A descendant inheriting stdout must not keep the timed-out call alive."""
    child_code = "import time; time.sleep(3)"
    parent_code = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        "time.sleep(3)"
    )

    started = time.perf_counter()
    with pytest.raises(subprocess.TimeoutExpired):
        concat_video._run_process_tree(
            [sys.executable, "-c", parent_code],
            timeout=0.2,
        )
    elapsed = time.perf_counter() - started

    assert elapsed < 1.5, f"0.2s timeout took {elapsed:.2f}s"


def test_manim_command_disables_noisy_progress_output():
    cmd = concat_video._build_manim_command(
        "scene.py", "Demo", "media", quality_flag="-ql"
    )

    assert "--progress_bar" in cmd
    assert cmd[cmd.index("--progress_bar") + 1] == "none"
    assert "-v" in cmd
    assert cmd[cmd.index("-v") + 1] == "WARNING"
