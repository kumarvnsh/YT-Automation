# Astrotold Whiteboard × Vox Visuals — Design Spec

**Date:** 2026-08-09
**Status:** Locked
**Channel:** Astrotold (`channels/astrotold/config.yaml`)

## Goal

Give Astrotold shorts a distinctive explainer look — hand-drawn whiteboard
marker line-art combined with a Vox flat-vector explainer sensibility — with a
"drawn-on" reveal feel, inside a hard budget of **$0.10–0.20 per short**.

## Decision

- **Style:** `whiteboard_vox` — clean black marker line-art on a white
  background (whiteboard) plus one or two bold flat accent colours,
  iconographic shapes, and generous white space (Vox). One cohesive prompt so
  the look holds across every beat.
- **Motion:** `pan` — slow zoom + gentle drift (subtle; whiteboard reads clean).
- **Transition:** `wiperight` — a wipe that reveals each new beat like it is
  being drawn on. This is the cheap stand-in for true whiteboard animation.
- **Coverage:** every beat generated (no stock), capped for budget.

Compliance: `whiteboard_vox` is obviously an illustration → no YouTube
synthetic-media disclosure obligation.

## The whiteboard "draw-on" — what we do and don't do

Real whiteboard animation is a hand progressively drawing each stroke: true
frame-by-frame animation, far outside the budget. Instead the `wiperight`
transition wipes each finished still on across the frame, which reads as the
drawing appearing. It is a look-and-feel approximation, not literal hand-drawing.
`transition_seconds` controls how slow the "drawing" feels; `fadewhite` is an
alternative that dissolves through white.

## How it works (per short)

Identical flow to the Histold spec — script → TTS → per-beat `imagegen.generate`
in `whiteboard_vox` → `build_broll_silent` applies `pan` + `wiperight`
(`_concat_xfade`, durations padded for the wipe overlap) → captions → `finalize`.
Stock b-roll remains the automatic fallback on any generation failure or for
beats beyond `max_images` (see `assets.py`).

## Config (`channels/astrotold/config.yaml`)

```yaml
assets:
  ai_images:
    enabled: true
    style: "whiteboard_vox"
    beats: []
    max_images: 8
    model: "gpt-image-1"
    size: "1024x1536"
    quality: "low"
    timeout_seconds: 90
video:
  mode: "broll"
  motion: "pan"
  transition: "wiperight"
  transition_seconds: 0.4
```

Astrotold previously had no `ai_images` block (stock-only), so this both turns
generation on and sets the style.

## Cost

~6–8 beats × `low` gpt-image-1 (~$0.01–0.02 each) ≈ **$0.07–0.16 per short**.
TTS and the wipe/motion ffmpeg work are free. `max_images` is the hard ceiling;
`quality` the main lever.

## Shared code

Uses the same engine shipped for Histold (`imagegen` style presets,
`video_builder` motion/transition, `_concat_xfade`). This spec adds only the
`whiteboard_vox` preset and the Astrotold config.

## Out of scope (budget)

- True hand-drawing whiteboard animation (frame-by-frame).
- Vox motion-graphics overlays (animated arrows, kinetic typography). A cheap
  ffmpeg drawtext/arrow approximation is possible later if wanted.

## Rollout

All config, so reverting is a config edit or `git revert`. Judge on
**like-rate**, not views (per channel analytics note): A/B a few whiteboard_vox
shorts against current stock b-roll before committing the channel to it.
