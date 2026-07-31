"""Static frontend contracts for explanation-mode and curriculum controls."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "src" / "frontend" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "src" / "frontend" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "src" / "frontend" / "style.css").read_text(encoding="utf-8")


MODES = (
    "general",
    "conceptual_intuition",
    "worked_example",
    "derivation_visual_proof",
    "graphical_exploration",
    "exam_technique",
    "misconception_repair",
    "revision_recap",
)


def test_frontend_exposes_all_backend_explanation_modes():
    assert 'id="explanation-mode-select"' in HTML
    for mode in MODES:
        assert f'value="{mode}"' in HTML


def test_general_mode_and_curriculum_are_safe_defaults():
    assert '<option value="general" selected>General' in HTML
    assert 'id="curriculum-profile-select"' in HTML
    assert '<option value="aqa_a_level_mathematics">' in HTML


def test_learning_controls_are_accessible_and_explained():
    assert 'aria-describedby="explanation-mode-help"' in HTML
    assert 'aria-describedby="curriculum-profile-help"' in HTML
    assert 'id="lesson-direction-summary"' in HTML
    assert 'aria-live="polite"' in HTML


def test_generation_payload_includes_selected_learning_profile():
    assert "explanation_mode:" in JS
    assert "curriculum_profile:" in JS
    assert "explanationModeSelect.value" in JS
    assert "curriculumProfileSelect.value" in JS


def test_mode_summary_updates_when_either_control_changes():
    assert "EXPLANATION_MODE_DESCRIPTIONS" in JS
    assert "updateLessonDirectionSummary" in JS
    assert "explanationModeSelect.addEventListener('change'" in JS
    assert "curriculumProfileSelect.addEventListener('change'" in JS


def test_lesson_direction_layout_has_mobile_fallback():
    assert ".lesson-direction-grid" in CSS
    assert ".lesson-direction-summary" in CSS
    assert "grid-template-columns: 1fr;" in CSS


def test_homepage_does_not_claim_unmeasured_performance_metrics():
    for unsupported_claim in (
        "1.2s",
        "60 FPS",
        "99.4%",
        "$0.0018",
        "guarantee uninterrupted",
        "down to the millisecond",
        "instant fast",
    ):
        assert unsupported_claim not in HTML


def test_cache_bypass_controls_reset_to_safe_defaults():
    assert "bypassCacheCheckbox.checked = false" in JS
    assert "bypassSceneCacheCheckbox.checked = false" in JS


def test_failed_job_replaces_running_progress_presentation():
    assert 'id="progress-spinner"' in HTML
    assert 'id="progress-title"' in HTML
    assert 'id="progress-subtitle"' in HTML
    assert "setProgressPresentation('failed')" in JS
    assert "Generation stopped" in JS
    assert ".progress-section.is-failed .pulsing-spinner" in CSS
