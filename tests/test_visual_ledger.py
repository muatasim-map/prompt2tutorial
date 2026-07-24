"""Tests for the per-job visual ledger and repeat detection."""

from visual_ledger import LedgerEntry, VisualLedger, normalize


def _sb(index, metaphor, composition):
    return {
        "index": index,
        "visual_metaphor": metaphor,
        "composition": composition,
        "primary_objects": ["a", "b"],
        "color_role": "blue primary",
        "primary_motion": "morph",
        "transition_from_prev": "grows",
        "on_screen_text": None,
    }


def test_normalize():
    assert normalize("  A  River   Flow ") == "a river flow"
    assert normalize(None) == ""


def test_records_and_summarizes():
    ledger = VisualLedger()
    ledger.record_from_storyboard(_sb(1, "a river", "flow left to right"))
    ledger.record_from_storyboard(_sb(2, "a spring", "vertical stack"))
    summary = ledger.compact_summary()
    assert "Scene 1" in summary and "a river" in summary
    assert "Scene 2" in summary and "a spring" in summary


def test_metaphor_used_detection():
    ledger = VisualLedger()
    ledger.record_from_storyboard(_sb(1, "A River", "flow"))
    assert ledger.metaphor_used("a river")   # case/space-insensitive
    assert not ledger.metaphor_used("a spring")


def test_composition_overused():
    ledger = VisualLedger()
    for i in range(1, 4):
        ledger.record_from_storyboard(_sb(i, f"m{i}", "grid"))
    assert ledger.composition_count("grid") == 3
    assert ledger.composition_overused("grid")          # >2
    assert not ledger.composition_overused("circle")


def test_distinct_metaphor_count():
    ledger = VisualLedger()
    ledger.record_from_storyboard(_sb(1, "m1", "l1"))
    ledger.record_from_storyboard(_sb(2, "m2", "l2"))
    ledger.record_from_storyboard(_sb(3, "m1", "l3"))  # duplicate metaphor
    assert ledger.distinct_metaphor_count() == 2


def test_text_style_recorded_from_on_screen_text():
    ledger = VisualLedger()
    entry = _sb(1, "m", "l")
    entry["on_screen_text"] = "Definition"
    e = ledger.record_from_storyboard(entry)
    assert e.text_style == "text-on-screen"


def test_empty_ledger_summary():
    assert "none yet" in VisualLedger().compact_summary()


def test_save_roundtrip(tmp_path):
    ledger = VisualLedger()
    ledger.record(LedgerEntry(index=1, metaphor="m", composition="c"))
    path = tmp_path / "ledger.json"
    ledger.save(path)
    assert path.exists()
    import json
    data = json.loads(path.read_text())
    assert data["entries"][0]["metaphor"] == "m"
