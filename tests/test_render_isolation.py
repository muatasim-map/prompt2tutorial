"""Manim render output-path isolation.

Regression guard for a measured hazard: Manim caches text/image renders under
``<media_dir>/texts`` and ``<media_dir>/images`` with content-hashed filenames.
With a shared media_dir, concurrent renders of the same string corrupted each
other (3 of 4 concurrent renders failed with "ParseError: no element found").
"""

from pathlib import Path

from media_paths import JobWorkspace


def test_each_scene_gets_a_private_media_dir(tmp_path):
    ws = JobWorkspace("iso-job", base_dir=tmp_path)
    dirs = [ws.scene_media_dir(i) for i in range(1, 13)]
    assert len(set(dirs)) == 12, "scene media dirs must all differ"


def test_scene_media_dir_is_inside_the_job_workspace(tmp_path):
    ws = JobWorkspace("iso-job", base_dir=tmp_path)
    d = ws.scene_media_dir(3)
    assert ws.root in d.parents
    assert ws.video in d.parents or d.parent == ws.video


def test_media_dirs_do_not_collide_across_jobs(tmp_path):
    a = JobWorkspace("job-a", base_dir=tmp_path)
    b = JobWorkspace("job-b", base_dir=tmp_path)
    assert a.scene_media_dir(1) != b.scene_media_dir(1)


def test_scene_outputs_remain_unique_and_ordered(tmp_path):
    ws = JobWorkspace("iso-job", base_dir=tmp_path)
    vids = [ws.scene_video(i) for i in range(1, 13)]
    assert len(set(vids)) == 12
    # zero-padded so lexical order == numeric order (concat ordering safety)
    assert [p.name for p in vids] == sorted(p.name for p in vids)


def test_render_uses_the_per_scene_media_dir(monkeypatch, tmp_path):
    """The render call must receive the isolated dir, not the shared one."""
    import video_generator as vg

    ws = JobWorkspace("iso-job", base_dir=tmp_path).create()
    seen = {}

    def fake_compile(code_path, class_name, media_dir, timeout=300, is_3d=False):
        seen["media_dir"] = Path(media_dir)
        out = Path(media_dir) / "r.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00")
        return str(out), None

    monkeypatch.setattr(vg, "compile_video", fake_compile)
    monkeypatch.setattr(vg.manim_generator, "generate_manim_code", lambda **kw: {
        "content": "from manim import *\nclass S(Scene):\n    def construct(self): pass",
        "class_name": "S", "model": "fake", "raw_received": True})

    vg._generate_and_compile(
        ws, service=object(), provider="gemini", client=None, index=7, total=12,
        job_id="iso-job", scene={"text": "t", "animation": "a"}, audio_duration=None,
        previous_context=None, storyboard_entry=None, global_style="s", ledger_summary="",
    )

    assert seen["media_dir"] == ws.scene_media_dir(7)
    assert seen["media_dir"] != ws.video          # not the shared directory
