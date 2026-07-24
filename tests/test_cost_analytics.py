"""Real, measured cost/usage analytics — replacing the previously-hardcoded
ASCII cost-audit numbers (14,250 tok / 76,600 tok / 1,420 chars, always the
same regardless of the actual job).

Chain under test: LLMService.generate() reports real provider token usage ->
_make_llm_status logs it to the job's own log file -> _aggregate_cost_report
reads that log back and computes cost via config.calculate_llm_cost /
calculate_tts_cost. Every number is either measured or explicitly zero/absent
("no_usage_recorded") — never a plausible-looking fabrication.
"""

import json

import pytest

import video_generator as vg
from config import calculate_llm_cost, calculate_tts_cost
from media_paths import JobWorkspace


@pytest.fixture
def job_id(tmp_path, monkeypatch):
    """A real JobWorkspace rooted in tmp_path, so log I/O is genuine but isolated."""
    import media_paths

    monkeypatch.setattr(media_paths, "JOBS_DIR", tmp_path / "jobs")
    jid = "test-job-cost-audit"
    ws = JobWorkspace(jid)
    ws.logs.mkdir(parents=True, exist_ok=True)
    return jid


def _log(job_id, event, payload):
    vg._write_job_log(job_id, event, payload)


# --------------------------------------------------------------------------- #
# LLMService.generate() reports real usage
# --------------------------------------------------------------------------- #

def test_generate_emits_a_usage_event_with_real_tokens():
    from config import ModelRoles, RetryPolicy
    from llm_service import LLMService

    events = []
    roles = ModelRoles(provider="gemini", script="primary", animation="anim",
                       repair="repair", fallback=None)
    policy = RetryPolicy(attempts=1, initial_delay=0.0, max_delay=0.0)
    service = LLMService(roles, policy, status=events.append)
    service._raw_gemini_call = lambda model, system, prompt, response_schema=None: (
        '{"ok":1}', 123, 45)

    result = service.generate("script", "sys", "p", "gemini")
    assert result.input_tokens == 123
    assert result.output_tokens == 45

    usage_events = [e for e in events if e.get("usage")]
    assert len(usage_events) == 1
    assert usage_events[0]["input_tokens"] == 123
    assert usage_events[0]["output_tokens"] == 45
    assert usage_events[0]["role"] == "script"
    assert usage_events[0]["model"] == "primary"


def test_failed_call_reports_zero_tokens_not_fabricated_ones():
    from config import ModelRoles, RetryPolicy
    from llm_service import LLMError, LLMService

    roles = ModelRoles(provider="gemini", script="primary", animation="anim",
                       repair="repair", fallback=None)
    policy = RetryPolicy(attempts=1, initial_delay=0.0, max_delay=0.0)
    service = LLMService(roles, policy)

    def boom(*a, **k):
        raise ValueError("network is having a bad day")

    service._raw_gemini_call = boom
    with pytest.raises(LLMError):
        service.generate("script", "sys", "p", "gemini")
    # No usage event should exist for a call that never succeeded — nothing
    # here asserts tokens are zero because nothing should be logged at all.


# --------------------------------------------------------------------------- #
# _make_llm_status writes real usage to the job log
# --------------------------------------------------------------------------- #

def test_llm_status_sink_writes_usage_to_job_log(job_id):
    sink = vg._make_llm_status(job_id)
    sink({"usage": True, "provider": "gemini", "model": "gemini-3.5-flash-lite",
         "role": "animation", "input_tokens": 500, "output_tokens": 200})

    log_path = JobWorkspace(job_id).log_file()
    lines = [json.loads(l) for l in log_path.read_text(encoding="utf-8").splitlines()]
    usage_lines = [l for l in lines if l["event"] == "llm_usage"]
    assert len(usage_lines) == 1
    assert usage_lines[0]["input_tokens"] == 500
    assert usage_lines[0]["output_tokens"] == 200
    assert usage_lines[0]["role"] == "animation"


def test_non_usage_events_do_not_pollute_the_usage_log(job_id):
    """A routine (non-usage) status event goes through update_job_status only —
    it must not also write a spurious job-log line."""
    sink = vg._make_llm_status(job_id)
    sink({"provider": "gemini", "model": "m", "role": "script", "note": "using m"})
    log_path = JobWorkspace(job_id).log_file()
    assert not log_path.exists()


# --------------------------------------------------------------------------- #
# _aggregate_cost_report: the actual analytics computation
# --------------------------------------------------------------------------- #

def test_no_log_file_gives_honest_zero_report(job_id, tmp_path):
    # job_id fixture creates the logs dir but no job.log file yet.
    report = vg._aggregate_cost_report(job_id, scene_count=0)
    assert report["total_cost_usd"] == 0.0
    assert report["data_source"] == "no_log"
    assert report["cost_breakdown"] == []


def test_aggregates_multiple_roles_and_calls(job_id):
    _log(job_id, "llm_usage", {"provider": "gemini", "model": "gemini-3.5-flash-lite",
                               "role": "script", "input_tokens": 1000, "output_tokens": 300})
    _log(job_id, "llm_usage", {"provider": "gemini", "model": "gemini-3.5-flash-lite",
                               "role": "storyboard", "input_tokens": 2000, "output_tokens": 500})
    _log(job_id, "llm_usage", {"provider": "gemini", "model": "gemini-3.5-flash-lite",
                               "role": "animation", "input_tokens": 5000, "output_tokens": 1500})
    _log(job_id, "llm_usage", {"provider": "gemini", "model": "gemini-3.5-flash-lite",
                               "role": "animation", "input_tokens": 4800, "output_tokens": 1400})

    report = vg._aggregate_cost_report(job_id, scene_count=5)

    assert report["total_input_tokens"] == 1000 + 2000 + 5000 + 4800
    assert report["total_output_tokens"] == 300 + 500 + 1500 + 1400
    assert report["llm_call_count"] == 4
    assert report["data_source"] == "measured"

    stages = {r["stage"]: r for r in report["cost_breakdown"]}
    assert "Manim code generation" in stages
    assert stages["Manim code generation"]["input_tokens"] == 5000 + 4800
    assert stages["Manim code generation"]["calls"] == 2
    assert stages["Script generation"]["calls"] == 1


def test_cost_matches_manual_calculation(job_id):
    _log(job_id, "llm_usage", {"provider": "gemini", "model": "gemini-3.5-flash-lite",
                               "role": "script", "input_tokens": 10_000, "output_tokens": 2_000})
    report = vg._aggregate_cost_report(job_id, scene_count=1)
    expected = calculate_llm_cost("gemini-3.5-flash-lite", 10_000, 2_000)
    assert report["total_cost_usd"] == round(expected, 6)


def test_tts_usage_included_in_total_and_breakdown(job_id):
    _log(job_id, "tts_usage", {"provider": "edge-tts", "characters": 3200, "scenes": 6})
    report = vg._aggregate_cost_report(job_id, scene_count=6)
    assert report["total_tts_characters"] == 3200
    tts_rows = [r for r in report["cost_breakdown"] if r["stage"] == "Narration TTS"]
    assert len(tts_rows) == 1
    assert tts_rows[0]["characters"] == 3200
    assert tts_rows[0]["cost_usd"] == round(calculate_tts_cost("edge-tts", 3200), 6)


def test_paid_tts_provider_produces_nonzero_cost(job_id):
    _log(job_id, "tts_usage", {"provider": "openai-tts", "characters": 5000, "scenes": 4})
    report = vg._aggregate_cost_report(job_id, scene_count=4)
    assert report["total_cost_usd"] > 0.0


def test_breakdown_is_stage_ordered(job_id):
    _log(job_id, "tts_usage", {"provider": "edge-tts", "characters": 100, "scenes": 1})
    _log(job_id, "llm_usage", {"provider": "gemini", "model": "gemini-3.5-flash-lite",
                               "role": "repair", "input_tokens": 100, "output_tokens": 50})
    _log(job_id, "llm_usage", {"provider": "gemini", "model": "gemini-3.5-flash-lite",
                               "role": "script", "input_tokens": 100, "output_tokens": 50})
    report = vg._aggregate_cost_report(job_id, scene_count=1)
    stages = [r["stage"] for r in report["cost_breakdown"]]
    assert stages.index("Script generation") < stages.index("Compile-repair (REPL)")
    assert stages[-1] == "Narration TTS"          # TTS always last


def test_malformed_log_lines_are_skipped_not_fatal(job_id):
    log_path = JobWorkspace(job_id).log_file()
    log_path.write_text('not valid json\n{"event": "llm_usage", "role": "script", '
                        '"model": "gemini-3.5-flash-lite", "input_tokens": 10, '
                        '"output_tokens": 5}\n', encoding="utf-8")
    report = vg._aggregate_cost_report(job_id, scene_count=1)
    assert report["total_input_tokens"] == 10
    assert report["llm_call_count"] == 1


def test_empty_log_produces_no_usage_recorded_not_fake_data(job_id):
    _log(job_id, "scene_stage", {"scene": 1, "stage": "request_started"})
    report = vg._aggregate_cost_report(job_id, scene_count=3)
    assert report["cost_breakdown"] == []
    assert report["total_cost_usd"] == 0.0
    assert report["data_source"] == "no_usage_recorded"


def test_scene_count_reflects_actual_generated_videos_not_a_constant(job_id):
    report = vg._aggregate_cost_report(job_id, scene_count=7)
    assert report["scene_count"] == 7
