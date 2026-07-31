import config


def test_render_workers_defaults_to_two_for_cpu_bound_manim(monkeypatch):
    monkeypatch.delenv("RENDER_WORKERS", raising=False)

    assert config.get_visual_config().render_workers == 2


def test_render_workers_can_be_reduced_for_low_memory_hosts(monkeypatch):
    monkeypatch.setenv("RENDER_WORKERS", "1")

    assert config.get_visual_config().render_workers == 1


def test_render_workers_are_bounded(monkeypatch):
    monkeypatch.setenv("RENDER_WORKERS", "999")

    assert config.get_visual_config().render_workers == 8


def test_advisory_visual_repairs_are_opt_in(monkeypatch):
    monkeypatch.delenv("AUTO_REPAIR_ADVISORY_QA", raising=False)
    assert config.get_visual_config().auto_repair_advisory_qa is False

    monkeypatch.setenv("AUTO_REPAIR_ADVISORY_QA", "true")
    assert config.get_visual_config().auto_repair_advisory_qa is True
