# Histold Animated Visuals — Design Spec

**Date:** 2026-08-09
**Status:** Locked
**Channel:** Histold (root `config.yaml`)

## Goal

Replace generic stock b-roll with topic-specific generated illustration in a
consistent, on-brand "history" art style, with gentle motion and transitions —
while staying inside a hard budget of **$0.10–0.20 per whole short**.

## Decision

- **Style:** `histold_hybrid` — one cohesive look fusing painterly brushwork,
  vintage propaganda-poster composition/palette, and graphic-novel ink drama.
  Chosen over rotating styles because a single prompt holds consistency across
  a video's beats.
- **Motion:** `pan` — slow zoom plus a gentle directional drift, direction
  alternating per beat.
- **Transitions:** `fade` crossfades between beats (0.4s).
- **Coverage:** every beat gets a generated still (no stock), capped by a hard
  image count that keeps total spend in budget.

Compliance: every style used is obviously non-photographic, so none carries a
YouTube synthetic-media disclosure obligation. `photoreal` remains the only
style that would.

## How it works (per short, at render time)

1. **Script** → segments/beats (unchanged, `script_generator`).
2. **Voiceover** → `tts.synthesize` (edge TTS, free).
3. **Stills** → `imagegen.generate` per beat in the `histold_hybrid` style.
   `select_segments` caps the count (`max_images`); any beat past the cap or any
   failed generation falls back to stock footage — a run can never break.
4. **Silent visuals** → `build_broll_silent` applies `pan` motion per still and
   stitches with `fade` crossfades (`_concat_xfade`). Segment durations are
   padded by `(n-1)*fade` so the finished video still covers the voiceover.
5. **Captions** → `captions.build_ass` (word-timed).
6. **Compose** → `finalize` mixes voiceover (+ optional music) and burns captions.
7. **Quality gate / upload** → unchanged.

## Config (Histold `config.yaml`)

```yaml
assets:
  ai_images:
    style: "histold_hybrid"
    beats: []            # every beat, not just pivot/fact
    max_images: 8        # budget cap (see cost)
    quality: "low"       # cost lever; raise to "medium" for sharper (~3x)
video:
  motion: "pan"          # pan | kenburns | stopmotion
  transition: "fade"     # empty = hard cuts
  transition_seconds: 0.4
```

`video.mode` stays `broll` — the mode already routes AI images + the b-roll
render path; motion/transition are independent config, not a new mode.

## Cost

~6–8 beats × `low` gpt-image-1 (~$0.01–0.02 each) ≈ **$0.07–0.16 per short**.
TTS and the motion/transition ffmpeg work are free. `max_images` is the hard
ceiling; `quality` is the main lever. At 2 shorts/day that is ~$0.15–0.30/day.

## Code

- `imagegen.py` — `STYLE_PRESETS` gains `histold_hybrid`, `animated`, `vox`,
  `claymation`, and the history set (`graphic_novel`, `propaganda_poster`,
  `woodcut`, `watercolor`, `noir`).
- `video_builder.py` — `pan` motion branch; `stopmotion` wobble branch;
  `_concat_xfade` crossfades with pure `_xfade_offsets` helper; `build_broll_silent`
  reads `video.motion` / `video.transition` and pads durations for fades.
- `pipeline.py` — `stopmotion` added to the mode allowlist (optional wobble mode).
- `tests/test_stopmotion_visuals.py` — presets, prompt wiring, xfade math.

## Out of scope (budget-driven)

- True AI video / real frame-by-frame stop-motion (10–50× cost).
- Vox-style motion-graphics overlays (animated arrows, kinetic typography,
  data callouts). A cheap ffmpeg approximation (drawtext callouts, arrow PNGs)
  is possible later if the explainer look is wanted.

## Rollout

Style/motion/transition are all config, so reverting is a config edit (or
`git revert`). Judge the change on **like-rate**, not views (per channel
analytics note): A/B a handful of hybrid shorts against current stock b-roll
before committing the whole channel to it.
