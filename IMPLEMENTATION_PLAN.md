# Prompt2Learn.ai — Reliability & Gemini Improvement Plan

Status: implemented. 52 unit tests pass; Manim+FFmpeg smoke test verified locally.
Scope was incremental hardening of the existing pipeline. No redesign into a
template/DSL renderer. LLM-generated Manim approach preserved.

## 1. Problem findings (from inspection)

### LLM / Gemini
- `video_generator.setup_llm_client` hard-codes Gemini model IDs and scatters
  model-selection logic. Same model is used for script **and** animation code.
- No role-based routing (script vs animation vs repair vs fallback).
- Retry logic is duplicated in `animations.py` and `manim_generator.py` as manual
  `2 ** attempt` loops that also regex-parse error strings. `google-genai` already
  ships tenacity-based retry via `HttpRetryOptions`; the manual loop stacks a second
  retry system → retry storms on 429.
- No model cooldown after repeated 429 / `RESOURCE_EXHAUSTED`.
- No bounded concurrency for Gemini calls.
- No configured fallback model; "auto" silently downgrades quality.

### Validation
- Provider output is only `json.loads`-parsed. No schema validation, no repair,
  no bounds on scene count / string length.

### Media / job isolation (collision risks)
- `media/audio_fragments/fragment_{index}.mp3` — shared across jobs.
- `media/audio.mp3` — shared final audio.
- `media/video_list.txt` — shared concat list.
- Manim render output path is CWD-relative (`media/videos/...`).
- Binary MP3 concatenation (`concatenate_audio_fragments`) produces technically
  invalid MP3 (concatenated frames, broken headers/duration).
- Video concat + mux use fragile PyAV raw-packet `dts=None` manipulation.
- No FFprobe validation of the final output before marking `completed`.

### Config
- Flask local default port is `5001`; Dockerfile `EXPOSE 5000` + compose maps
  `5000:5000`, but the container runs `python src/main.py` (honours `PORT`, default
  5001) → container listens on 5001, published port never reachable.
- `.env.example` has **no** Gemini keys and none of the model-role vars.
- `pydantic` is a direct dependency in practice but only declared transitively.
- README says `localhost:5000` but local run defaults to 5001.

## 2. Files to change / add

New modules (`src/`):
- `config.py` — provider + role-based model configuration from env, one source of truth.
- `schemas.py` — Pydantic `Scene`, `VideoScript`, `ManimCode` + parse/repair helpers.
- `media_paths.py` — `JobWorkspace` per-job dirs + project-root cache dirs + `CACHE_VERSION`.
- `ffmpeg_utils.py` — FFmpeg audio concat / video concat / mux + FFprobe validation
  (pure command builders + thin runners for testability).
- `llm_service.py` — centralized Gemini retry (SDK `HttpRetryOptions`), cooldown,
  concurrency semaphore, fallback selection, error categorization, status events.

Refactors:
- `animations.py` — route through `llm_service`, use structured output + Pydantic + one repair.
- `manim_generator.py` — route through `llm_service` with the animation/repair roles.
- `tts_generator.py` — per-job fragment dirs, FFmpeg audio concat (drop binary concat).
- `concat_video.py` — delegate to `ffmpeg_utils`; return absolute render path.
- `video_generator.py` — wire `JobWorkspace`, `config`, validation, FFprobe gate, richer status.
- `main.py` — default port 5000.
- `Dockerfile`, `docker-compose.yml`, `.env.example`, `README.md`, `pyproject.toml`.
- `tests/` — unit tests (no live API/paid calls).

## 3. Implementation order
1. `config.py`, `schemas.py`, `media_paths.py`, `ffmpeg_utils.py`, `llm_service.py`.
2. Refactor `animations.py`, `manim_generator.py`, `tts_generator.py`, `concat_video.py`.
3. Rewire `video_generator.py` (job workspace + validation + status).
4. Config/docs fixes.
5. Tests + run.

## 4. Test / verification plan
- `pytest` unit tests: schema valid/invalid, scene-count/length bounds, model routing,
  fallback selection + no silent downgrade, cooldown after simulated 429, per-job path
  uniqueness, cache-key versioning, FFmpeg command construction, FFprobe parse.
- Import/compile check of every module.
- Optional one-scene Manim + FFmpeg smoke test (deps present locally).
- No live Gemini/Claude/OpenAI/Edge calls — all mocked.

## 5. Known risks
- Backward compatibility: `video_data` must stay a JSON list of scene dicts for the
  review UI and `/api/generate/continue`. Mitigation: validate then store `model_dump()`.
- Existing content-hash caches change key formula (cache-version bump) → cold cache once.
- Gemini model IDs in the repo are placeholders (e.g. `gemini-3.6-flash`); kept
  configurable via env so real IDs can be swapped without code changes.
