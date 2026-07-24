# Prompt2Learn.ai — Session Handoff

_Written at the end of a long Manim/Gemini animation-quality session, for whoever (human or Claude) picks this up next._

## ⚠️ FIRST THING TO DO WHEN RESUMING

The working directory is currently on **`branch-1-subjects`**, which does **not** contain any of this session's work. Everything from this session is safely stashed:

```
stash@{0}: On branch-2: epitaxy: pre-switch from branch-2
(405 files changed, 57917 insertions(+), 2050 deletions(-))
```

To resume this work:

```bash
git checkout branch-2
git stash pop
```

Then verify with `pytest` (expect **473 passed, 3 skipped** as of the end of this session).

Do **not** run `git stash drop`/`clear` or force-checkout over this until the pop is confirmed clean — this stash is the only copy of `schemas.py`, `llm_service.py`, `domain_guidance.py`, `storyboard.py`, `ffmpeg_utils.py`, `visual_qa.py`, `visual_ledger.py`, `visual_primitives.py`, `media_paths.py`, `mcp_client.py`, and the entire `tests/` directory (39 test files). None of this is committed anywhere yet.

---

## What Prompt2Learn.ai is

A Flask app that turns a topic into an educational video: Gemini generates a script → a storyboard (visual direction) pass → per-scene Manim animation code → TTS narration → FFmpeg assembly. Target audience: A-level students first, general learners second. Visual style: 3Blue1Brown-inspired.

## Session arc (chronological)

1. **Math/physics syllabus coverage** — audited and filled gaps against the full A-level maths + physics syllabus (16 physics topics, full maths list) in the Manim-generation prompt.
2. **Domain-tag routing** (`domain_guidance.py`, `schemas.py`) — split a monolithic ~5,200-token "every domain at once" prompt block into a 15-tag closed taxonomy, injecting only 1–4 relevant modules per scene (~600–2,000 tokens). Fixed instruction dilution.
3. **115-minute job-stall incident** — root-caused to a missing HTTP timeout on the Gemini client (not rate-limiting, as an earlier forensic report claimed). Fixed: HTTP timeout, dimension-aware compile timeout, timeout-aware repair prompts, `Surface` resolution cap, closed color vocabulary, wall-clock backstop.
4. **Narrative arc / continuity / semantic color / controlled motion** — added `narrative_role`, video-wide `continuity_mode` (cumulative vs varied), semantic color contracts, and a narrow verified `ValueTracker`/`always_redraw` allowlist (previously banned entirely).
5. **Regression fix** — user reported visuals felt "plainer"; root cause was the `general` domain-tag fallback (used for intro/framing scenes) being too thin after the routing split. Enriched it.
6. **Cost analytics rebuild** — the `--cost-audit` UI modal was **entirely fabricated** (hardcoded token counts). Wired real Gemini/Claude/OpenAI token usage through `llm_service.py` → job log → aggregation → frontend, with an honest empty-state instead of fake numbers.
7. **Craft-level animation direction** — line-weight hierarchy, motion hierarchy (lead/support), reframing over overlaying, match-cut between scenes (fixed a real bug: `ending_state` was falling through to `primary_motion`), intentional holds vs frozen tails, value-before-hue color hierarchy.
8. **Trigonometry deep-dive** — three rounds of "is P2L ready for prompt X" (rotating-point sine generation, SOH-CAH-TOA, sine/cosine rules), each followed by implementation. Culminated in:
   - **`scene_kind` taxonomy** (`explanation` | `worked_problem`) — a new orthogonal-to-`narrative_role` dimension so the storyboard can *plan* a worked-problem scene (GIVEN→ASKED→CHOOSE→SUBSTITUTE→SOLVE→ANSWER, numbers `TransformFromCopy`'d off the diagram into the equation) rather than generation improvising one.
   - Cosine rule as generalised Pythagoras, visible rule-selection, bearings convention.
9. **Domain-module staleness audit** — found and fixed real bugs: calculus module said "updaters are out of scope" (false, contradicted the shared vocabulary); SHM and waves described discrete workarounds for now-verified continuous patterns.
10. **Live validation** — ran 2 real Gemini calls (flash-lite) to check whether new guidance actually lands in output. It does (`ValueTracker`, `TransformFromCopy` both appeared correctly). First-attempt code still had minor API errors; the production compile-repair loop fixed both on attempt 2. This is the first live confirmation in the whole session that any of this actually works end-to-end.

## Current state

- **473 tests passing, 3 skipped**, all via `pytest` (run with `PYTHONIOENCODING=utf-8` prefix on Windows).
- Every new Manim construct/pattern was render-verified in an isolated scratch directory before being added to any prompt — this project was burned twice early on by hallucinated APIs (`axes.point_to_pycoords`, `BarChart` needing LaTeX).
- `.env` has a working `GEMINI_API_KEY` (loads via `load_dotenv()`; not present in raw shell env).
- LaTeX/MathTeX is **not installed** — a hard constraint throughout; all math notation uses plain `Text`/`Paragraph` with Unicode.

## Standing rules established this session (still apply)

- **Never commit without being explicitly asked.**
- **Never spend Gemini quota without asking first** — except live *rendering* of hand-written Manim code, which doesn't touch quota and was always fine.
- Any new Manim API/construct must be render-verified in a scratch dir before being added to a prompt.
- Chemistry work is explicitly out of scope (was raised once early on, deferred, never resumed).
- The dev server has repeatedly been found running stale code (`use_reloader=False`) — check `/api/health`'s `stale` field, never restart it without asking.

## Known open items (not yet done)

1. **The stash needs to be popped and this work needs to actually be committed somewhere.** It has never been committed — only stashed. This is the single highest-priority item.
2. **Full live A/B validation** was approved once, early in the session, and never executed (only the small 2-scene spot-check in item 10 above happened). Whether it's still wanted is unclear — ask.
3. **Ambiguous case (SSA)** for the sine rule — flagged, not built.
4. **Worked-problem staging rollout** to non-geometry domains — the mechanism is domain-neutral by design but has only been exercised in trig scenes so far.
5. Frontend "improve/remake the UI" from the cost-analytics round was scoped narrowly (the modal itself); the rest of the UI (landing page, chips, etc.) was untouched — chip topics still say "Neural Networks / Quantum Physics" rather than A-level subjects, which was suggested but not actioned.

## Where things live (for quick orientation after `stash pop`)

| Concern | File |
|---|---|
| Manim generation + repair prompts | `src/manim_generator.py` |
| Domain-tag taxonomy + per-tag guidance modules | `src/domain_guidance.py` |
| Storyboard (visual-direction) pass | `src/storyboard.py` |
| Pydantic schemas incl. `scene_kind`/`narrative_role`/`continuity_mode` | `src/schemas.py` |
| Gemini/Claude/OpenAI call layer, retry/cooldown/usage tracking | `src/llm_service.py` |
| Job orchestration, cost aggregation, routing audit logging | `src/video_generator.py` |
| Compile timeouts, dimension-aware scaling | `src/concat_video.py` |
| Visual QA (blank/near-identical/static-tail detection) | `src/visual_qa.py` |
| Cost-audit UI modal | `src/frontend/app.js` (search `generateAsciiReport`) |

## If you're an AI resuming this session

Read this file first, then run `git checkout branch-2 && git stash pop`, then `pytest` to confirm 473/3. Don't re-verify things this doc says were already verified (render-checks, live validation) unless something looks inconsistent — re-derive only what's actually needed for the next task.
