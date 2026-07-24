# Gemini 3.6 Flash Regression — Investigation & Fix Plan

## Evidence gathered (reproduced live, not guessed)

Environment facts verified against the live API with the configured key:

- `gemini-3.6-flash` **is a real, available model** on this key (37 Gemini models listed).
  So the regression is *not* an invalid model ID.
- `sys.stdout.encoding == cp1252` on this Windows box.
- A 30 s end-to-end run **completed** (`status: completed`, FFprobe validation passed,
  duration 30.53 s) using `gemini-3.6-flash` for script + animation.

### Root cause #1 — storyboard/visual-direction silently dropped (CONFIRMED)

`storyboard.generate_storyboard()` passes `response_schema=Storyboard` — a **nested**
Pydantic model (`GlobalStyle` object + `List[StoryboardScene]`, `extra="allow"`,
`Optional[...]` → `anyOf`/null). Gemini rejects it:

```
Storyboard(nested)     : FAIL code=400  INVALID_ARGUMENT
list[StoryboardScene]  : OK   len=1007
```

Flat `list[Scene]` and `ManimCode` are accepted; only the **nested wrapper** is rejected.

Consequences, all observed in the live run:
1. Every job logs `[model] gemini-3.6-flash failed: unavailable_model`.
2. `except (LLMError, ScriptValidationError)` swallows it → `"storyboard": "skipped"` and
   the job continues **without any visual direction** — the exact silent quality
   degradation the requirement forbids.
3. It is **miscategorised**: `_categorize()` maps HTTP 400 → `CAT_UNAVAILABLE_MODEL`,
   so a schema bug is reported as "model unavailable", which is actively misleading.

### Root cause #2 — failures collapse into generic messages (CONFIRMED by code path)

- `manim_generator.generate_manim_code()` catches `LLMError` / `ScriptValidationError`
  and returns `None`, discarding category, model, attempt, and validation detail.
- `_render_scene()` turns that into `(None, ctx, {...})`; the scene loop just does not
  append. When every scene fails the job reports only *"No scenes could be rendered"* —
  matching the reported *"No videos were generated"* with no per-scene cause.
- Script failures surface as a bare message with no model/stage/attempt/validation data
  (matching *"Could not generate script"*).

### Root cause #3 — explicit selection is still overridable by env

`_gemini_roles()` now propagates an explicit UI pick to all roles, but each role is still
wrapped in `_env("GEMINI_ANIMATION_MODEL", default)`. A stray `GEMINI_ANIMATION_MODEL` in
`.env` therefore **silently overrides an explicit UI selection** of 3.6 Flash.

### Root cause #4 — no strict mode; fallback is implicit

`fallback` is only `None` by accident of the current defaults; `GEMINI_FALLBACK_MODEL`
re-enables a silent cross-model downgrade. There is no per-job `strict_model` flag, and
retry attempts/backoff are invisible (SDK-internal only).

## Fixes

1. **`schemas.py`** — add `gemini_storyboard_schema()` returning the Gemini-compatible
   flat `list[StoryboardScene]`. `parse_storyboard()` already accepts a bare list and
   applies the default `GlobalStyle`, so **no storyboard field is lost**.
2. **`config.py`** — explicit UI selection wins over env for every role; add
   `ModelSelection` (canonical per-job record) with `strict` defaulting to **True** for an
   explicit UI pick; `LLM_ALLOW_FALLBACK` opt-in only.
3. **`llm_service.py`** — one visible bounded retry loop (exp backoff + jitter) on the
   **same** model, emitting attempt/wait events; SDK retry set to 1 attempt so the two
   systems never stack; strict mode refuses any model/provider switch; fix categories
   (400 → `bad_request`, 404 → `unavailable_model`).
4. **`storyboard.py`** — use the flat schema; never silently skip — raise precise errors.
5. **`video_generator.py`** — persist the canonical selection at job creation and reuse it
   after `/continue`; emit the required 6-stage per-scene sequence; record per-scene
   failure `{scene, stage, model, category, detail}`; structured all-scene failure summary;
   guard `entry_for()` returning `None`; write a per-job model-routing audit log.
6. **`animations.py` / `manim_generator.py`** — raise/propagate rich diagnostics instead of
   returning bare `None`.

## Non-goals (explicitly preserved)
Scene counts per duration, narration/objective/explanation/animation detail, storyboard +
continuity context, Manim render quality, Pydantic strictness on narration `text`,
scene-level (not job-level) repair, and the existing cache/job isolation.

## Verification
Offline regression tests (mocked, no API), full suite, import/compile checks, then one
authorised live 30 s run in strict mode on `gemini-3.6-flash` with a routing audit proving
every LLM stage used that exact model.
