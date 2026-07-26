"""Unit tests for the 8-pattern DSA Array guidance & tag routing."""

import pytest
from domain_guidance import build_domain_section, normalize_tags


def test_dsa_tag_aliases():
    """Verify all array and DSA tag aliases resolve to discrete_algorithms."""
    tags = [
        "dsa", "array", "arrays", "two_pointers", "two_pointer",
        "hashing", "hash_map", "hash_set", "prefix_sum",
        "matrix_traversal", "sliding_window", "kadane", "kadanes_algorithm",
        "intervals", "interval_merge", "greedy", "greedy_array",
    ]
    for tag in tags:
        normalized = normalize_tags([tag])
        assert normalized == ["discrete_algorithms"], f"Tag '{tag}' failed to normalize to discrete_algorithms"


def test_dsa_domain_section_content():
    """Verify built domain section contains split-screen layout and all 16 patterns."""
    section = build_domain_section(["dsa"])
    
    # Verify strict domain scoping rule
    assert "STRICT DOMAIN SCOPING RULE (CRITICAL)" in section
    assert "is ONLY to be used" in section
    assert "DO NOT render a Code Terminal in Geometry, Trigonometry, Calculus" in section
    
    # Verify glassmorphic IDE styling instructions
    assert "Glassmorphic IDE & Code Terminal Styling" in section
    assert "Left Panel (x = -3.5)" in section
    assert "Right Panel (x = +3.5)" in section
    assert "Active Line Tracker" in section
    assert "Syntax Color Palette" in section
    
    # Verify all 16 DSA patterns are documented
    for pattern_num in range(1, 17):
        assert f"Pattern {pattern_num}:" in section, f"Pattern {pattern_num} missing from domain section"

