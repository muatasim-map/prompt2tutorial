# Visual-Quality Upgrade — Implementation Plan

Status: implemented. 89 unit tests pass (52 prior + 37 new); a real Manim render
that imports the primitives + FFmpeg frame extraction + contact sheet verified
locally. No git commits made.


Goal: raise bespoke LLM-generated Manim quality via a video-level **visual
direction** system (storyboard + visual ledger + diversity enforcement +
visual QA), without replacing bespoke generation with fixed scene templates.
Preserve all existing reliability work (Pydantic validation, Gemini routing/retry,
per-job dirs, FFmpeg assembly, FFprobe validation, caches, tests).

## Integration points (verified by inspection)
- `LLMService.generate(role, system, prompt, provider, client, response_schema)` —
  add a new `storyboard` role routed to the strongest configured model.
- `config.ModelRoles` (frozen dataclass) — add `storyboard` field (defaulted, so
  existing constructors/tests keep working) + `GEMINI_STORYBOARD_MODEL`.
- Flow: `start → generate_script_workflow → awaiting_review → /continue →
  generate_rendering_workflow`. The **storyboard is generated at the top of the
  rendering workflow** (after re-validating the possibly-edited scenes) so it
  matches the final script.
- `manim_generator.generate_manim_code` — enrich the prompt with the scene's
  storyboard entry, the global style contract, a compact previous-scene summary,
  and a compact visual ledger. Stop passing full prior source code.
- `_render_scene` in `video_generator` — add per-scene visual QA + one optional
  visual repair; record ledger; feed contact sheet.
- Artifacts surface via `job['metadata']` (frontend already receives the full job
  dict via `/api/progress`; extra keys are ignored → no breakage). A minimal,
  defensive log line is added to `app.js` for the QA links.
- Imaging: Pillow + numpy (already installed). No OpenCV, no new heavy deps.

## New modules (`src/`)
- `visual_primitives.py` — low-level, composition-agnostic Manim helpers (palette,
  styled text/title, node/edge, highlight, token chip, probability bar, safe
  layout/framing, transitions). Copied into each job's `code/` dir and added to
  the Manim subprocess `PYTHONPATH` so generated scenes MAY `from visual_primitives
  import *`. NOT a fixed-scene template system.
- `visual_ledger.py` — `VisualLedger`: accumulates used metaphors/layouts/objects/
  colors/motions/text-styles/transitions; emits a compact summary for prompts;
  detects repeats.
- `storyboard.py` — builds the storyboard prompt (diversity rules baked in), calls
  the LLM (storyboard role), validates, runs `check_diversity`, does one repair if
  violated, saves per job.
- `visual_qa.py` — ffmpeg frame-extraction command builders; Pillow/numpy frame
  analysis; heuristic flags (blank/white/low-variance/edge-clip/no-change/near-
  identical/text-only/repeated-layout); PIL contact-sheet montage. Flags are
  recorded, never auto-reject.

## Schema additions (`schemas.py`)
- `GlobalStyle` (palette, typography, pacing, spacing, mood).
- `StoryboardScene` (index, learning_goal, key_concept, visual_metaphor,
  composition, primary_objects[], primary_motion, color_role, camera_plan?,
  transition_from_prev, on_screen_text?, anti_repetition_notes, visual_complexity).
- `Storyboard` (global_style, scenes[]) + parse/repair helpers. Base `Scene`
  already has `extra="allow"` so existing API payloads are unaffected.

## Config additions (`config.py`)
- `GEMINI_STORYBOARD_MODEL`, `STORYBOARD_ENABLED`, `VISUAL_REPAIR_ATTEMPTS`,
  `CONTACT_SHEET_ENABLED`, `VISUAL_QA_ENABLED`, `GLOBAL_VISUAL_STYLE`,
  and QA thresholds (`QA_BLANK_MAX_BRIGHTNESS`, `QA_WHITE_MIN_BRIGHTNESS`,
  `QA_MIN_STDDEV`, `QA_EDGE_CONTENT_RATIO`, `QA_NEAR_IDENTICAL_MAE`,
  `MANIM_QUALITY`). Exposed via `get_visual_config()`.

## Diversity enforcement
- `required_distinct_approaches(target, scene_count)`: 60s→5, 120s→8 (linear
  between; clamped to scene count).
- `check_diversity(storyboard, target)` returns violation strings for: too few
  distinct metaphors; adjacent repeated metaphor/composition; a composition used
  >2×. Drives one storyboard repair; residual violations are logged (not fatal).

## Scene-level retry
- After a scene renders, extract frames + QA. If blank/near-blank (or compile
  failed), regenerate that scene once with storyboard constraints + blank feedback.
  `VISUAL_REPAIR_ATTEMPTS` (default 1). Other scenes untouched; caches reused.
- `CACHE_VERSION` bumped `v2 → v3` (prompt/schema/cache-key change).

## Tests (offline, fake providers)
storyboard validation; ledger diversity rules; adjacency; required counts for
60/120; prompt construction receives storyboard+ledger; scene-level retry only;
frame-extraction/contact-sheet command construction; QA flag parsing.

## Order
schemas → config → media_paths → visual_primitives → visual_ledger → storyboard →
visual_qa → manim_generator → concat_video → video_generator → docs → tests → run.
