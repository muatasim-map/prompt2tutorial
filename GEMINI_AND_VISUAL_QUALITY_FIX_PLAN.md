# Gemini + Visual Quality Fix Plan

All findings below are from artifacts/commands, not inference.

## PART 1 — script-generation regression: ROOT CAUSE FOUND

The failing job `f964b8c8…` (11:06) was produced by a **stale Flask process running the
committed git-HEAD code**, not the current working tree. Proof:

1. `git show HEAD:src/video_generator.py` line **240** = `raise Exception("Could not generate script")`.
   That exact string exists **nowhere** in the current `src/` (only in a docstring describing
   its removal). The current code physically cannot emit it.
2. The sibling job `d8d5516c…` (same session, 11:08) wrote the **pre-upgrade layout**:
   `media/output_<id>.mp4`, `media/output_silent_<id>.mp4`,
   `content/<topic-slug>-<id>-1.py`, CWD-relative `media/videos/<slug>…`.
   The current code writes only `media/jobs/<id>/{code,scenes,video,final,logs,qa}/`.
3. `media/jobs/f964b8c8…` and `media/jobs/d8d5516c…` **do not exist** — current code creates
   the workspace as its first action.
4. `src/main.py` runs with `use_reloader=False` ("disabled to prevent losing job state"), so a
   server started before the edits keeps the old modules resident indefinitely.

The underlying defect (already fixed in the working tree, verified by 103 passing tests and a
live 30 s run) was: `generate_script_json()` returned `None` on any failure and the caller
raised one generic `Exception`.

**Fix:** the diagnostics are already correct; add a **build stamp** so a stale server is
impossible to miss — printed at startup, exposed on `/api/health`, and recorded in job
metadata/logs as `app_build`.

## PART 2 — scenes failing before compile

`d8d5516c` shows **video stream 8.67 s vs audio 26.8 s** (`nb_frames=130 @15fps`, audio
1117 frames): only **1 of ~3 scenes** reached the final file, and the old PyAV mux produced a
container whose audio ran ~18 s past the last video frame. That is the old silent-`continue`
path. The current tree already has per-scene stage messages, per-scene `failure_info`,
`scene_N_error.json`, and a structured all-scene summary; this plan **verifies** them and adds
an explicit **A/V drift** guard.

## PART 3 — static visuals: ROOT CAUSE FOUND

Measured on the reference video (4 fps sampling, 160 px grayscale):

```
consecutive-frame MAE: max=0.99  mean=0.27
frames with ANY motion (MAE>=1.0): 0
final 2.0 s: MAE exactly 0.00  (dead frozen tail)
```

Generated source confirms the mechanism:
```
5 x self.play(...)          # ends ~5 s
self.wait(2.0)              # model's own dead pause
self.wait(8.4960 - _curr_time)   # append_duration_sync frozen padding
```
So `append_duration_sync()` "solves" audio alignment by appending a **frozen tail**, and the
prompt permits long dead `wait()`s. Roughly 40 % of the scene is a still frame.

**Fixes**
1. `schemas.py` — optional, backward-compatible progression fields on `StoryboardScene`:
   `opening_state`, `visual_beats` (new `VisualBeat` model), `transformations`,
   `ending_state`, `continuity_notes`. All default to `None`/`[]` so reviewed scenes and old
   cached payloads still validate.
2. `manim_generator.py` — motion contract: opening state → ≥2 meaningful changes for a
   6–12 s scene → clean ending state; visible progression every 2–4 s; brief emphasis pauses
   allowed; **no frozen tails / static text screens / decorative motion / unrelated resets**;
   on-screen objects must match the narration at that moment.
3. `video_generator.py` — cap dead tail padding (`MAX_TAIL_PAD_SECONDS`, default 0.75 s) while
   **keeping exact A/V sync**, and let visual QA's new `static_end_padding` flag trigger the
   existing bounded scene-level visual repair with explicit feedback.
4. `visual_qa.py` — new flags: `static_end_padding`, `long_static_run`, `av_drift`.

## PART 4 — narration & on-screen text
- `animations.py` script prompt: hook → intuition → mechanism → example → recap; one idea per
  scene; no filler/restatement; word count matched to real TTS pace (~2.6 wps × duration);
  preserve language, terminology and math symbols; never invent claims.
- `schemas.py` validators: **mojibake detection** (`âœ“`, `â†'`, `Ã©`…), near-duplicate
  narration detection across scenes, and a narration-length/pace sanity helper.
- Manim prompt text rules: sparing labels only, never the whole narration, ≤2–3 lines,
  no generic "Introduction/Summary" headings, readable sizes, safe margins, no clipping.

## PART 5 — tests
Offline/fake-provider tests for: strict 3.6-Flash propagation (script/storyboard/animation/
repair), no silent fallback, structured script diagnostics, valid script → review, valid Manim
→ `compile_video()`, invalid Manim → scene diagnostics, reviewed-scene compatibility, optional
visual fields, visual-beat contract, narration pace/duplication, Unicode/mojibake,
static-end-padding detection, QA/contact-sheet command construction.
Then **ask before** the single authorised live 30 s Gemini 3.6 Flash run.

## Preserved
Flask UI/REST API, TTS, Gemini routing + strict mode, Pydantic strictness, per-job isolation,
FFmpeg pipeline, bespoke LLM-generated Manim (no rigid templates), existing tests.
