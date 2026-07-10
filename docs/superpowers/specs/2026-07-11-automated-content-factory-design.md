# Automated Content Factory Design

Date: 2026-07-11
Status: Approved design; implementation not started

## Goal

Improve the existing YouTube automation system into a reliable, low-cost,
fully automatic content factory that publishes two distinct Shorts per day.
The external cron-job.org schedule and GitHub Actions trigger remain unchanged.

## Current System

The repository already has a checkpointed pipeline:

```text
topic -> script -> voiceover -> captions -> assets -> render -> compose -> upload
```

Each run stores artifacts and progress in an output stage directory. GitHub
Actions invokes the pipeline in resumable `--step` mode and already protects
against duplicate schedule fires.

## Daily Job Model

Each scheduled trigger creates one independent job. The two daily jobs are
identified by date and slot, such as `morning` and `evening`.

Each job will:

1. Reserve a fresh topic before script generation.
2. Generate one Short using its own stage directory and state file.
3. Run all pipeline steps with checkpointed resume behavior.
4. Run automated quality checks.
5. Upload only if all required checks pass.
6. Record the provider, topic, validation results, cost signals, and YouTube
   result.

Topic reservation must prevent the two slots from selecting the same topic,
including when jobs overlap or are retried. Topic fingerprints should catch
near-duplicates in addition to exact title matches. A failed morning job must
not block the evening job. Retrying a job must reuse completed artifacts and
must not upload twice.

## Script Provider Routing

Claude and OpenAI will be interchangeable providers for the existing script
generation responsibilities: narration, segments, visual keywords, title,
description, and tags. Both providers must produce the same validated JSON
shape.

Configuration should support:

```yaml
script:
  routing: "fallback"       # fallback | random | round_robin
  primary_provider: "anthropic"
  fallback_provider: "openai"
```

Routing behavior:

- `fallback`: call Claude first and call OpenAI only after a Claude failure.
- `random`: choose one provider for each new job.
- `round_robin`: alternate providers predictably between new jobs.

The default is `fallback` because it maximizes reliability without making two
paid calls for a successful job. Provider failures include timeouts, API
errors, rate limits, and invalid or unusable model output. A provider choice
and whether fallback was used must be saved in `state.json` and stage metadata.

If both providers fail, the job remains incomplete, its artifacts are kept,
and the failure is reported. Existing explicit provider settings should remain
backward compatible.

## Automated Quality Gate

The upload step must be conditional on a quality report. Required checks:

- Script structure, title, description, tags, and segments are valid.
- Topic and title are not recent duplicates.
- Narration length is within the configured target range.
- Every segment has narration and usable visual keywords.
- Voiceover exists, is nonempty, and has a valid duration.
- Captions exist and contain timed words when enabled.
- Every segment has usable assets.
- The rendered video exists, is playable, and has the configured dimensions.
- Duration is within the allowed range.
- Audio is present and not silent.
- Basic advertiser-safety checks pass.
- YouTube metadata limits are respected.

The report should contain a pass/fail result, score, and per-check status. A
failed job must never reach a public upload. The report and failed artifacts
remain available for diagnosis and resume.

## Visual Asset Strategy

Pexels remains the default low-cost visual provider. AI-generated still images
are an optional fallback for segments where stock results are missing or have
low relevance, especially for historical reconstructions. Existing ffmpeg
still-image and Ken Burns rendering remains the downstream contract.

AI generation must be bounded by a per-video image limit, daily budget,
timeouts, and retry limits. Generated images use deterministic cache keys based
on the model, prompt, segment content, keywords, format, and size. A retry or
resume reuses a cached image instead of making another paid request. Provider,
model, cache hit, and estimated cost signals are recorded per job.

## Analytics Feedback Loop

Published videos should be evaluated at fixed windows such as 24 and 72 hours.
The system records topic category, hook, title pattern, provider, visual
provider, publish slot, views, retention metrics where available, engagement,
cost, and QA results.

Future topic selection should use weighted history rather than reacting to one
outlier. The initial exploration policy is:

- 70% proven topic formats
- 20% variations of successful formats
- 10% experiments

Analytics should inform topic categories, hooks, titles, publishing slots, and
provider selection. Existing republish behavior remains separate from the
pre-upload quality gate.

## Reliability and Error Handling

- Claude failure: try OpenAI according to routing mode.
- OpenAI failure: retry with bounded backoff, then preserve the stage.
- Pexels failure: use cached assets, configured AI fallback, or safe fallback
  visuals.
- Render failure: resume from render without regenerating earlier artifacts.
- Upload failure: retry upload only; do not regenerate the video.
- Any terminal failure: keep artifacts, write the error to state, and notify.

No cron changes are required. The current GitHub Actions `--step` loop and
duplicate-run guard remain the scheduler boundary.

## Testing Strategy

Focused tests should cover:

- Claude success and Claude-to-OpenAI fallback.
- Random and round-robin provider selection.
- Invalid provider JSON and alternate-provider recovery.
- Topic reservation, near-duplicate prevention, and independent daily slots.
- Quality-gate pass and rejection cases.
- Resume without duplicate API calls or duplicate uploads.
- Asset cache reuse and missing-key behavior.
- Upload retry behavior.
- Clear error messages for missing provider credentials.

All provider and network calls must be mocked in unit tests. At least one
end-to-end smoke test should run the pipeline with mocked external services and
verify the final state, artifacts, quality report, and upload decision.

## Implementation Boundary

The first implementation phase should focus on job identity/topic reservation,
provider routing, the quality gate, and tests. Analytics enrichment and
selective AI visual fallback can follow behind stable interfaces. The existing
cron configuration, ffmpeg rendering, YouTube upload contract, and checkpoint
state format should be preserved unless a narrowly scoped extension is needed.
