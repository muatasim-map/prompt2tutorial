"""Unit tests for DSA visual primitives (make_code_terminal & make_array_grid)."""

import pytest
from visual_primitives import make_array_grid, make_code_terminal


def test_make_code_terminal():
    """Verify make_code_terminal constructs a valid IDE window with line objects."""
    code = [
        "def two_sum(nums, target):",
        "    seen = set()",
        "    for num in nums:",
        "        return True",
    ]
    term = make_code_terminal(code, title="solution.py")
    assert hasattr(term, "line_objects")
    assert len(term.line_objects) == 4
    assert hasattr(term, "container")


def test_make_array_grid():
    """Verify make_array_grid constructs valid 2.5D memory cell VGroup."""
    values = [0, -1, 2, -3, 1]
    grid = make_array_grid(values, indices=True)
    assert hasattr(grid, "cells")
    assert hasattr(grid, "val_texts")
    assert hasattr(grid, "idx_labels")
    assert len(grid.cells) == 5
    assert len(grid.idx_labels) == 5
