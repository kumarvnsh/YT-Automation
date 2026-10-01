"""Claude-written motion graphics: one animated HTML scene per script segment.

Claude writes each scene as a self-contained HTML page animated only with CSS
@keyframes / element.animate(). Every scene is rendered deterministically
with headless Chromium: all animations are paused, then each frame is captured
by seeking document.getAnimations() to t. That way the output does not depend
on wall-clock speed, and a slow CI runner produces the same video as a fast laptop.

One API call per video returns every scene. Any scene that is missing or fails
to render falls back to the normal stock/AI-image chain for that segment, so
this mode can never break a run.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .config import Config, env

# Scenes are authored at half resolution and captured at 2x device scale, so
# Claude works in a 540x960 CSS viewport while the output is 1080x1920.
_SCALE = 2

_SCENE_RE = re.compile(r'<scene index="(\d+)">\s*(.*?)\s*</scene>', re.DOTALL)

_SYSTEM = """You are a senior motion designer making the visuals for a narrated \
vertical YouTube Short on a history channel called Histold. The voiceover and \
captions are added later; you design one animated scene per narration segment.

HARD TECHNICAL RULES (scenes that break these are thrown away):
- Each scene is ONE complete, self-contained HTML document.
- Viewport is exactly {w}x{h} CSS px. html/body: margin 0, overflow hidden, \
fixed {w}x{h} size, opaque background.
- Animate ONLY with CSS @keyframes animations, or element.animate() called \
synchronously in an inline <script> at load. No CSS transitions, no \
setTimeout/setInterval/requestAnimationFrame, no canvas, no video, no audio.
- Every animation starts at t=0 (use animation-delay to stage beats) and the \
scene must look finished and intentional at its stated duration. Use \
animation-fill-mode: both.
- No external images or scripts. Inline SVG is encouraged. Google Fonts via \
<link> is allowed.
- The bottom 30% of the frame (y > {cap_y}px) is reserved for burned-in \
captions: keep it free of text and important detail (background texture only).

CREATIVE DIRECTION:
- One visual system across all scenes: dark ink/charcoal background, \
parchment and bone tones, one warm accent (amber or vermilion), bold \
condensed display type. Editorial, documentary feel, like a premium history explainer.
- Open each scene on its payoff. No intros. Movement starts in the first 0.3s.
- Show, don't transcribe: maps, timelines, counters, silhouettes, diagrams, \
dates, names and numbers. On-screen text should be short (max ~6 words per \
element). The narration is already captioned, so do not repeat it.
- Only state facts that appear in the narration. Never invent dates, numbers \
or quotes. Draw people as simple silhouettes or icons, never realistic faces.
- Keep the energy up: something should move at all times.
- Be economical: no comments, compact CSS, no unused rules. Keep each scene under ~9,000 characters.
- Every element must sit fully inside the frame; nothing clipped at the edges. \
Fill the upper 70% with layered detail (texture, grid, secondary motion).

OUTPUT FORMAT: for every segment, emit exactly
<scene index="N">
<!doctype html>...full document...
</scene>
in order, with nothing else between scenes."""


def segment_windows(segments: list[str], words: list, total: float) -> list[tuple[float, float]]:
    """(start, end) seconds per segment, aligned to the voiceover's word timings.

    Segments are mapped onto the TTS word list by cumulative word count,
    rescaled because TTS tokenisation differs slightly from str.split(). The
    last segment runs to `total`.
    """
    counts = [max(len(s.split()), 1) for s in segments]
    n_script = sum(counts)
    starts, acc = [], 0
    for c in counts:
        k = min(round(acc * len(words) / n_script), len(words) - 1) if words else 0
        starts.append(words[k].start if words and acc else 0.0)
        acc += c
    ends = starts[1:] + [total]
    return [(s, max(e, s + 0.5)) for s, e in zip(starts, ends)]


def _request_scenes(cfg: Config, title: str, segs: list, windows, w: int, h: int) -> dict[int, str]:
    import anthropic

    lines = [f"Video title: {title}", ""]
    for i, (seg, (s, e)) in enumerate(zip(segs, windows)):
        lines.append(
            f"Segment {i} — beat: {seg.beat or 'n/a'} — duration: {e - s:.1f}s\n"
            f"Narration: {seg.narration.strip()}\n"
            f"Visual keywords: {', '.join(seg.keywords[:4])}\n"
        )
    # Writing every scene can take well over the SDK's 10-min default, with long
    # quiet stretches while the model thinks; summarized thinking keeps bytes flowing.
    client = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"), timeout=1800, max_retries=1)
    with client.beta.messages.stream(
        model=cfg.get("video.claude_motion.model", "claude-sonnet-5-5"),
        max_tokens=int(cfg.get("video.claude_motion.max_tokens", 30000)),
        thinking={"type": "adaptive", "display": "summarized"},
        output_config={"effort": cfg.get("video.claude_motion.effort", "medium")},
        betas=["server-side-fallback-2026-07-01"],
        extra_body={"fallbacks": "default"},
        system=_SYSTEM.format(w=w, h=h, cap_y=int(h * 0.7)),
        messages=[{"role": "user", "content": "\n".join(lines)}],
    ) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        raise RuntimeError(f"refused: {msg.stop_details}")
    text = "".join(b.text for b in msg.content if b.type == "text")
    scenes = {int(i): html for i, html in _SCENE_RE.findall(text)}
    print(f"  claude_motion: {len(scenes)}/{len(segs)} scenes, stop={msg.stop_reason}, "
          f"in_tokens={msg.usage.input_tokens} out_tokens={msg.usage.output_tokens}")
    return scenes


def _render_scene(page, html: str, duration: float, fps: int, dest: Path, preset: str) -> Path:
    page.set_content(html, wait_until="load")
    page.evaluate("async () => { await document.fonts.ready; document.getAnimations().forEach(a => a.pause()); }")
    if not page.evaluate("document.getAnimations().length"):
        raise RuntimeError("scene has no animations")
    ff = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(fps),
         "-c:v", "mjpeg", "-i", "-", "-c:v", "libx264", "-preset", preset,
         "-pix_fmt", "yuv420p", "-r", str(fps), str(dest)],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        for f in range(max(int(round(duration * fps)), 1)):
            page.evaluate("t => document.getAnimations().forEach(a => a.currentTime = t)", f * 1000 / fps)
            ff.stdin.write(page.screenshot(type="jpeg", quality=90))
    finally:
        ff.stdin.close()
        err = ff.stderr.read().decode()
        ff.wait()
    if ff.returncode != 0:
        raise RuntimeError(f"ffmpeg: {err[-500:]}")
    return dest


def render_motion_video(cfg: Config, st: dict, stage: Path, words, duration: float) -> Path:
    """Build stage/silent.mp4 from Claude scenes; per-segment stock fallback."""
    from . import video_builder as vb
    from .assets import fetch_for_segments
    from .pipeline import _rebuild_segments

    segs = _rebuild_segments(st)
    w, h, fps = vb._dims(cfg, st["fmt"])
    preset, _ = vb._encode_settings(cfg)
    cw, ch = w // _SCALE, h // _SCALE
    windows = segment_windows([s.narration for s in segs], words, duration + 0.5)

    try:
        scenes = _request_scenes(cfg, st["script"]["title"], segs, windows, cw, ch)
    except Exception as exc:  # noqa: BLE001 - fall back to stock for every segment
        print(f"  ! claude_motion: scene request failed ({exc}) — using stock.")
        scenes = {}
    scene_dir = stage / "scenes"
    scene_dir.mkdir(exist_ok=True)
    for i, html in scenes.items():
        (scene_dir / f"scene_{i:02d}.html").write_text(html, encoding="utf-8")

    clips: list[Path | None] = [None] * len(segs)
    if scenes:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": cw, "height": ch}, device_scale_factor=_SCALE)
            for i, (s, e) in enumerate(windows):
                if i not in scenes:
                    continue
                try:
                    clips[i] = _render_scene(page, scenes[i], e - s, fps,
                                             scene_dir / f"scene_{i:02d}.mp4", preset)
                    print(f"  ✓ scene {i} rendered ({e - s:.1f}s)")
                except Exception as exc:  # noqa: BLE001
                    print(f"  ! scene {i} failed ({exc}) — stock fallback.")
            browser.close()

    for i, (s, e) in enumerate(windows):
        if clips[i] is not None:
            continue
        assets = fetch_for_segments(cfg, [segs[i]], stage / f"fallback_{i:02d}", st["fmt"])[0]
        if not assets:
            raise RuntimeError(f"segment {i}: no scene and no fallback asset")
        clips[i] = vb._build_segment_clip(assets[0], e - s, w, h, fps,
                                          scene_dir / f"fallback_{i:02d}.mp4", preset)
    return vb._concat(clips, stage / "silent.mp4")
