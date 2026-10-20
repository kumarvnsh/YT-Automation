# Histold Production Improvements — Proposed Implementation Plan

**Goal:** Improve factual reliability, storytelling, narration synchronization, map recognition, and brand recall centrally in Histold's automated video pipeline.

**Status:** All seven implementation areas are present on `codex/histold-production-improvements`. Validation includes the existing and expanded unit suites, three synthetic local Chromium/FFmpeg fixtures, and a real research failure-path check. A complete AI-generated production Reel has not passed the entire new pipeline yet; live research correctly stopped on a disputed tangent. No deployment or publication performed. See README.md for operation and limits.

**Architecture:** Extend the current checkpointed Python pipeline. Research supplies evidence to the script generator; approved claims are checked before voice generation; actual audio timestamps drive a persisted storyboard consumed by Chromium. FFmpeg composes narration, captions, music, and branding; local quality checks gate publishing.

**Stack:** Existing Python, Claude, Edge TTS, Playwright/Chromium, FFmpeg, ASS captions, YAML configuration, and unittest. Research retrieval and optional forced alignment require a provider decision and a separate dependency review during implementation.

## Scope and approach

Three approaches considered:

1. Prompt-only changes: quickest, but cannot establish factual verification or reliable synchronization.
2. Incremental pipeline upgrades (recommended): reuse rendering, publishing, and resumable state; add evidence and timing artifacts with focused tests.
3. Replace the renderer or add manual editing: substantially more work without resolving the main upstream issues.

Implement in independently testable phases. Histold opts into each new capability through its root config.yaml; shared module changes retain existing defaults for channels/astrotold/config.yaml. New stages snapshot the pipeline version and relevant production settings. Existing stages retain their original step order and behavior.

This plan concerns production infrastructure and a small experiment, rather than redesigning the dashboard, changing publishing frequency, or replacing the renderer.

## Current evidence

- src/pipeline.py already synthesizes narration before rendering and persists word timings.
- src/claude_motion.py:segment_windows maps segments proportionally by word count. Its minimum 0.5-second window can extend beyond the next boundary for very short segments.
- Claude receives scene durations, narration, and visual keywords, but no timestamped internal reveal cues.
- src/tts.py receives native Edge word boundaries; sparse boundaries and other providers use evenly distributed estimates.
- Scene linting checks a synthetic final animation state, rather than multiple points on the rendered timeline.
- src/video_builder.py can continue without burned captions when libass is unavailable, while quality checks only require the caption source file.
- Historical accuracy is requested in a prompt; there is no persisted evidence-backed verification gate.
- Existing analytics exporters provide a foundation but do not establish availability of all requested retention and follower metrics.

## Phase 1 — Evidence-backed research and script verification (P0)

**Files:** Create src/research.py and tests/test_research.py. Modify src/script_generator.py, src/pipeline.py, src/quality_gate.py, config.yaml, tests/test_script_structure.py, tests/test_pipeline_artifacts.py, and tests/test_quality_gate.py.

- [ ] Define versioned research.json: topic, retrieved_at, sources, claims, locations, dates, and verification status. Each source stores URL, title, publisher, retrieval time, and the supporting excerpt; each claim references its source IDs.
- [ ] Implement a retrieval adapter using a production-accessible search/fetch provider. Check available credentials and deployment support before choosing it; assistant-only browsing tools cannot serve as the scheduled runtime integration.
- [ ] Prefer authoritative archives, government records, museums, and original documents. Require at least one directly supporting authoritative source per material claim; require independent corroboration for disputed or extraordinary claims.
- [ ] Feed the research packet into script generation and persist claim references per segment. Verify the resulting narration against retrieved evidence, including dates, numbers, place names, and geographic relationships.
- [ ] Treat model assessment as evidence evaluation, not proof. Persist supported, disputed, and unsupported findings and source references. Missing evidence, retrieval failure, or unresolved material contradictions stop the job before voiceover; never silently mark it verified.
- [ ] Insert research before script and verification after script for newly opted-in stages. Bind reports to the narration hash so edits invalidate verification. Final local quality checks validate the recorded report and matching hash without network access.
- [ ] Add mocked tests for supported facts, contradictory dates, missing sources, unsupported geographic claims, retrieval failure, modified narration, and resuming old/new stages.

**Acceptance:** Every material narration claim is traceable to retrieved evidence. Unsupported jobs cannot proceed to narration or publishing.

## Phase 2 — Consequence-first storytelling (P0)

**Files:** Modify src/script_generator.py, config.yaml, tests/test_script_structure.py, and tests/test_script_length.py.

- [ ] Introduce a Histold story profile: strange consequence → context → historical cause → satisfying explanation. Keep existing beat names where possible; make profile-specific validation explicit instead of changing every channel's callback rules.
- [ ] Require the opening words to communicate the surprising consequence within roughly 2–3 spoken seconds. The whole hook segment may run longer; avoid requiring a 14–22-word sentence to fit entirely into three seconds.
- [ ] Require a concrete payoff that answers the opening question. Remove the mandatory question-ending callback for this profile; retain other profiles' current behavior.
- [ ] Record category, hook text, payoff, and story-profile version in script metadata. Reject invented suspense or hooks that overstate the verified facts.
- [ ] Test prompt construction and structural validation. Evaluate consequence clarity and payoff quality on a small reviewed script set; word counts alone cannot establish narrative quality.

**Acceptance:** Reviewed scripts open with an understandable consequence, explain its cause, and resolve the question; Astrotold structure tests continue to pass.

## Phase 3 — Measured alignment and timestamped storyboard (P0)

**Files:** Create src/alignment.py and tests/test_alignment.py. Modify src/tts.py, src/pipeline.py, src/claude_motion.py, tests/test_claude_motion.py, and tests/test_pipeline_artifacts.py.

- [ ] Record timing provenance as native, forced_aligned, or estimated. Preserve native Edge word boundaries and validate monotonic timestamps against the measured audio duration.
- [ ] Match normalized script tokens to spoken tokens with a monotonic sequence alignment. Cover punctuation, contractions, repeated words, numbers, and one-to-many token differences; persist matched coverage and unmatched spans.
- [ ] Where native boundaries are absent or coverage is insufficient, use an explicitly configured forced-alignment provider. For Histold's strict timing mode, fail with an actionable report if no measured alignment can be obtained. Keep estimates explicitly labeled for legacy jobs.
- [ ] Persist storyboard.json with schema_version, narration hash, audio duration, timing source, and scenes containing scene_id, segment_id, start, end, narration, purpose, and local reveal cues. Cue times come from matched words, not Claude's duration estimates.
- [ ] Make captions and scene generation consume the same validated timing artifact. Pass scene-local word/cue offsets to Claude, and render using persisted storyboard boundaries.
- [ ] Quantize absolute boundaries once to frame indices, then derive scene lengths from adjacent boundaries. Ensure positive contiguous windows and bound rounding error to one output frame; reject impossible windows rather than extending them into neighbors.
- [ ] Test number expansion, repeated phrases, missing words, empty timing data, short segments, frame rounding, duration limits, and resume behavior without regenerating audio.

**Acceptance:** Storyboard, captions, and visuals share measured timing; there are no overlapping windows or cumulative scene rounding drift.

## Phase 4 — Audio diagnostics (P1)

**Files:** Create src/audio_quality.py and tests/test_audio_quality.py. Modify src/pipeline.py, src/quality_gate.py, config.yaml, and tests/test_quality_gate.py.

- [ ] Analyze the dry narration before music mixing using FFmpeg silencedetect. Start with configurable -40 dB / 0.8-second thresholds and calibrate against several existing recordings before enforcement.
- [ ] Persist audio_quality.json containing detected intervals, settings, edge padding, timing coverage, and deliberate pause allowances declared in storyboard metadata.
- [ ] Report unexpected internal silence as a warning initially. Intentional pauses require a bounded declared interval; do not infer intent solely from punctuation or automatically delete silence.
- [ ] Check actual final audio/video endpoints against the last aligned spoken word, with configurable 0.15-second tolerance for encoding offsets. Detect missing/empty audio and excessive leading/trailing silence separately.
- [ ] Test silence parsing, intentional pause matching, malformed FFmpeg output, missing audio, and truncated narration. Use synthetic audio for a real FFmpeg integration check.

**Acceptance:** Reports flag known inserted silence and truncation without flagging an allowed dramatic pause. Audio remains untrimmed unless a later change regenerates all dependent timings.

## Phase 5 — Recognizable maps and persistent branding (P1)

**Files:** Create assets/maps/manifest.json, vetted SVG assets, src/map_assets.py, and tests/test_map_assets.py. Modify src/claude_motion.py, src/motion_kit.css, src/video_builder.py, config.yaml, and tests/test_claude_motion.py. Create tests/test_video_branding.py.

- [ ] Curate simplified geographic outlines with source URL, license, version, projection, extent, and supported location IDs in a manifest. Include United States, Canada, Minnesota, and Washington for the initial geography fixtures.
- [ ] Distinguish present-day geography from historical borders. Require date-specific evidence for historical overlays; label schematic/inset views explicitly.
- [ ] Bind verified location IDs to supplied, sanitized inline SVG. Do not ask Claude to invent recognizable geographic outlines; reject missing required geography before scene rendering.
- [ ] Show an establishing map with country/state labels before inset detail. Keep labels visible for at least two seconds unless readability review calls for longer.
- [ ] Add a configurable HISTOLD mark during FFmpeg composition, independent of generated scene HTML. Reserve a matching upper safe rectangle in scene generation and layout linting; scale it with output dimensions.
- [ ] Test asset lookup, unsupported locations, manifest provenance, SVG sanitization, composition filter construction, and channel opt-in. Render one vertical fixture to check label/brand/caption collisions visually.

**Acceptance:** Viewers can identify the relevant countries and states before detail zooms; branding appears consistently on generated and fallback scenes without covering key content.

## Phase 6 — Render-aware quality gates (P1)

**Files:** Modify src/claude_motion.py, src/video_builder.py, src/quality_gate.py, tests/test_claude_motion.py, and tests/test_quality_gate.py. Create tests/test_render_quality.py.

- [ ] Sample layout at opening, reveal cue points, midpoint, and final rendered frame. Check clipping, text overlap, caption band, and reserved branding area at those actual times.
- [ ] Record meaningful reveal cues with a target interval of 2–4 seconds. Compare sampled foreground frames for sustained inactivity; exclude ambient grain/drift and treat static-scene detection as heuristic warnings until calibrated.
- [ ] Detect blank frames and persistent missing content. Give repair one bounded attempt; recheck repaired scenes before accepting them. Record stock fallback as a degraded visual result requiring review for the experimental batch.
- [ ] Persist composition.json with caption filter availability, actual caption application, branding application, and output timing. In Histold strict mode, missing libass or failed caption composition must block upload.
- [ ] Extend quality.json with pass/warn/fail results while retaining required existing checks. Hard-block missing verified evidence, invalid timing, missing required captions/audio, detected truncation, and required map failures.
- [ ] Test intermediate overlap, blank output, ambient-only movement, readable held labels, missing libass, fallback rendering, and resume compatibility. Validate rendered caption presence on sampled frames during review; metadata alone is not pixel-level proof.

**Acceptance:** Known defective fixtures fail or warn as specified; clean fixtures pass. A source caption file cannot mask an uncaptioned final render.

## Phase 7 — Six-Reel experiment and analytics (P2)

**Files:** Modify scripts/export_analytics.py, scripts/export_meta_analytics.py, src/pipeline.py, tests/test_meta_analytics.py, and tests/test_pipeline_artifacts.py. Create data/experiments/histold-six-reels.json and scripts/report_experiment.py with tests/test_experiment_reporting.py.

- [ ] Define six slots, interleaving two strange-geography, two historical-disaster, and two unbelievable-true-history videos. Topic candidates must pass research and deduplication before reservation; geography claims must avoid misleading road-access absolutes.
- [ ] Keep production profile and target duration stable. Save experiment ID, category, hook, duration, production version, publication time, platform, and platform video ID alongside each published item.
- [ ] Capture snapshots at comparable ages: 24 hours, 72 hours, and seven days. Persist actual observation age and flag late snapshots instead of treating them as exactly comparable.
- [ ] During implementation, verify official API documentation and actual account permissions for average watch time, three-second retention, completion, shares, and followers attributed to each Reel. Store unavailable values as null with a reason; never replace them with zero or infer per-Reel followers from account totals.
- [ ] Compute shares_per_1000_views = shares/views*1000 and followers_per_1000_views = attributed_followers/views*1000 only when numerator and denominator are available and compatible; handle zero views as unavailable.
- [ ] Report by platform and measurement age. Retain metric definitions and avoid pooling incompatible play/view denominators.
- [ ] Treat the supplied 14K views / 34 followers over 28 days as unverified account context. It is not a per-Reel control group. Report directional results and confounding factors, rather than statistical winners or causal improvement.

**Acceptance:** All six posts can be traced to their production settings and comparable-age observations; unsupported metrics remain explicit gaps.

## Delivery and validation order

1. Record baseline fixtures from existing local stages, plus current configuration and commit ID; run the existing suite before implementation.
2. Deliver Phases 1–3 as the first release: research, story profile, measured alignment, and persisted storyboard.
3. Deliver Phases 4–6 as the second release: diagnostics, map assets, composition branding, and render-aware quality.
4. Render three unpublished candidates covering the experiment categories. Review source support, opening clarity, alignment, maps, label readability, branding, audio pauses, captions, and payoff before enabling the production profile.
5. Deliver Phase 7 and publish the six-Reel batch through the existing publishing process after production settings are accepted. This plan itself does not authorize publication.

Run targeted unittest modules after each task, then the full existing suite after each release. Use the repository's existing environment; for example:

```bash
python -m unittest tests.test_claude_motion tests.test_script_structure tests.test_quality_gate tests.test_pipeline_artifacts -v
python -m unittest discover -s tests -v
python -m src.main --format short --no-upload
```

Unit tests must mock network calls. Browser/FFmpeg smoke checks use local fixtures. The final no-upload command makes configured provider calls and should run only with the required credentials and an accepted generation budget.

## Rollout and recovery

- Store production profile and artifact schema versions in new stages; do not retrofit in-flight stages silently.
- Hash narration and relevant inputs so retries reuse valid artifacts while edits invalidate downstream artifacts.
- Keep evidence and media diagnostics available as pipeline artifacts for failed jobs.
- Roll back by disabling Histold's new profile for newly created jobs. Jobs already running under that profile retain their required verification and quality rules.
- Keep existing upload credentials, schedules, and channel isolation intact. Test both Histold and Astrotold config loading and legacy resume paths before release.

## Completion criteria

- Verified source references precede voice generation and match final narration.
- Consequence-first openings and resolved payoffs pass reviewed samples.
- Measured timestamps drive captions, scenes, and reveals with contiguous frame boundaries.
- Silence, truncation, missing captions, and visible layout failures produce actionable reports.
- Maps use traceable geographic assets and the HISTOLD mark stays in its reserved area.
- Existing stage recovery and Astrotold behavior remain covered by passing checks.
- Six-Reel reporting compares equivalent-age data and preserves metric availability limits.
