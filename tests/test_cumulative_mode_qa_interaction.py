"""Cumulative visual similarity must remain informational."""

import inspect

import video_generator as vg
import visual_qa


def test_near_identical_flag_is_not_a_regeneration_trigger():
    report = {
        "index": 2,
        "flags": [visual_qa.FLAG_NEAR_IDENTICAL],
        "blank": False,
        "static_end_padding": False,
    }

    assert vg._visual_repair_reason(report, 8.0) is None


def test_near_identical_flag_only_ever_appended_to_the_flags_list():
    source = inspect.getsource(vg)
    assert 'later.setdefault("flags", []).append(visual_qa.FLAG_NEAR_IDENTICAL)' in source
    assert f"report.get(visual_qa.FLAG_NEAR_IDENTICAL" not in source


def test_advisory_repair_opt_in_still_ignores_near_identical_flag():
    report = {
        "index": 2,
        "flags": [visual_qa.FLAG_NEAR_IDENTICAL],
        "blank": False,
        "static_end_padding": False,
    }

    assert vg._visual_repair_reason(report, 8.0, allow_advisory=True) is None
