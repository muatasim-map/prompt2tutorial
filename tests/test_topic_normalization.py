"""Topic text is normalized before cache keys, prompts, and job metadata."""

import video_generator as vg


def test_topic_mojibake_is_repaired():
    assert vg.normalize_topic_text("30Â° angle of elevation") == "30° angle of elevation"


def test_clean_unicode_topic_is_preserved():
    topic = "Explain π, Σ, and a 30° angle"
    assert vg.normalize_topic_text(topic) == topic
