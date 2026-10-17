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
_HEAD_RE = re.compile(r"<head[^>]*>", re.IGNORECASE)

# Shared look (fonts, palette, grain, vignette, drift, motion utilities) is
# injected by us, so Claude's tokens go into composition instead of boilerplate.
_KIT_CSS = Path(__file__).with_name("motion_kit.css").read_text(encoding="utf-8")
_KIT = ('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Oswald:wght@500;600;700'
        '&family=Cormorant+Garamond:ital,wght@1,500&display=block">\n<style>' + _KIT_CSS + "</style>\n")
_EXAMPLE = Path(__file__).with_name("motion_example.html").read_text(encoding="utf-8")


def inject_kit(html: str) -> str:
    """Put the kit first in <head> so the scene's own CSS overrides it."""
    m = _HEAD_RE.search(html)
    if m:
        return html[:m.end()] + _KIT + html[m.end():]
    return "<!doctype html><html><head>" + _KIT + "</head>" + re.sub(r"(?i)<!doctype[^>]*>", "", html, count=1)

_SYSTEM = """You are a senior motion designer making the visuals for a narrated \
vertical YouTube Short on a history channel called Histold. The voiceover and \
captions are added later; you design one animated scene per narration segment.

HARD TECHNICAL RULES (scenes that break these are thrown away):
- Each scene is ONE complete, self-contained HTML document.
- Viewport is exactly {w}x{h} CSS px.
- Animate ONLY with CSS @keyframes animations, or element.animate() called \
synchronously in an inline <script> at load. No CSS transitions, no \
setTimeout/setInterval/requestAnimationFrame, no canvas, no video, no audio.
- Every animation starts at t=0 (use animation-delay to stage beats) and the \
scene must look finished and intentional at its stated duration. Use \
animation-fill-mode: both.
- No external images or scripts. Inline SVG is encouraged.
- The bottom 30% of the frame (y > {cap_y}px) is reserved for burned-in \
captions: keep it free of text and important detail (background texture only).

HOUSE KIT (already injected into every scene's <head> — do NOT redefine fonts, \
page size, background, grain or vignette; just use it and add scene-specific CSS):
- Vars: --ink --char --soot (darks), --parch --bone --dim (lights), --amber \
--verm, --accent (amber by default; set --accent:var(--verm) for danger/death), \
--display (Oswald), --serif (Cormorant italic).
- Layout: .k-stage = the safe area above captions (flex column, centered, gap). \
.k-bg = full-frame layer behind it; .k-grid = faint grid texture.
- Type: .k-kicker (small amber label with rules), .k-title (64px condensed caps), \
.k-huge (132px accent number), .k-serif (italic aside), .k-label, .k-accent.
- Objects: .k-card (parchment card), .k-stamp (rotated red stamp), .k-rule, \
.k-dot, .k-ring, .k-sil (silhouette fill for SVG).
- Counter: <span class="k-count" style="--to:515"></span> counts 0→515.
- Motion classes (stagger with style="--d:.4s", length with --t): k-rise, \
k-fade, k-pop, k-wipe, k-slam (stamp; tilt via --r), k-draw (SVG stroke with \
pathLength="1"), k-grow (bar scaleX), k-count, k-pan (slow camera move: \
--x0 --y0 --s0 → --x1 --y1 --s1), k-pulse, k-spin, k-ringout (loops). \
One element = one motion class; nest wrappers to combine.

EXAMPLE of the quality bar — narration "In four days in 1666, the Great Fire \
of London destroyed 13,200 houses." (3.5s):
<example>
{example}
</example>

CREATIVE DIRECTION:
- Editorial documentary feel, like a premium history explainer. Every scene \
needs depth: a background layer (silhouette, map, diagram, grid) with slow \
motion, plus a foreground payoff (number, date, name, stamp).
- Open each scene on its payoff. No intros. Movement starts in the first 0.3s, \
and something keeps moving until the end.
- Show, don't transcribe: maps, timelines, counters, silhouettes, diagrams, \
dates, names and numbers. On-screen text is short (max ~6 words per element). \
The narration is already captioned, so do not repeat it.
- Vary the composition from scene to scene; don't reuse one layout.
- Only state facts that appear in the narration. Never invent dates, numbers \
or quotes. Draw people as simple silhouettes or icons, never realistic faces.
- Be economical: no comments, compact CSS, no unused rules. Keep each scene under ~6,000 characters.
- LAYOUT (each scene is measured automatically; overlaps and clipping get flagged):
  * Put ALL text inside .k-stage, in normal flex flow, so it stacks without \
overlapping. Absolute positioning is for decorative, text-free layers in .k-bg only.
  * No text element wider than {w_text}px. A .k-huge number of 5+ characters \
needs a smaller font-size; .k-title lines max ~14 characters at 64px.
  * Keep tall or busy .k-bg art out from behind text: put it below the text \
block (y 440-{cap_y}px) or along the edges, never across a title or number.
  * Every element sits fully inside the frame with a 24px margin; nothing \
clipped at the edges, nothing in the caption band.

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
        system=_SYSTEM.format(w=w, h=h, cap_y=int(h * 0.7), w_text=w - 72, example=_EXAMPLE.strip()),
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


# Layout check run on each scene's final frame (ambient kit drift at rest).
# Text boxes are inset vertically to the glyph core so tight-but-clean
# stacks (big number over a label) don't count as overlap.
_LINT_JS = """() => {
  const W = innerWidth, H = innerHeight, CAP = H * 0.7, M = 8;
  const seen = e => { for (let n = e; n && n.nodeType === 1; n = n.parentElement) {
    const cs = getComputedStyle(n);
    if (cs.display === 'none' || cs.visibility === 'hidden' || +cs.opacity < 0.05) return false; } return true; };
  const boxes = [];
  for (const e of document.body.querySelectorAll('*')) {
    const tag = e.tagName.toLowerCase();
    if (e.closest('svg') && tag !== 'text' && tag !== 'tspan') continue;
    const counter = e.classList.contains('k-count');
    if (!counter && ![...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())) continue;
    if (!seen(e)) continue;
    let r = e.getBoundingClientRect();
    if (!counter) { const rg = document.createRange(); rg.selectNodeContents(e); r = rg.getBoundingClientRect(); }
    if (r.width < 2 || r.height < 2) continue;
    const pad = r.height * 0.2;
    boxes.push({ e, l: r.left, r: r.right, t: r.top + pad, b: r.bottom - pad,
                 txt: counter ? 'the counter' : '"' + e.textContent.trim().slice(0, 30) + '"' });
  }
  const out = [];
  for (const x of boxes) {
    if (x.l < M || x.r > W - M || x.t < M) out.push(x.txt + ' is cut off at the frame edge');
    if (x.b > CAP) out.push(x.txt + ' sits in the caption band (y > ' + Math.round(CAP) + 'px)');
  }
  for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
    const a = boxes[i], b = boxes[j];
    if (a.e.contains(b.e) || b.e.contains(a.e)) continue;
    const w = Math.min(a.r, b.r) - Math.max(a.l, b.l), h = Math.min(a.b, b.b) - Math.max(a.t, b.t);
    if (w > 0 && h > 0 && w * h > 0.1 * Math.min((a.r - a.l) * (a.b - a.t), (b.r - b.l) * (b.b - b.t)))
      out.push(a.txt + ' overlaps ' + b.txt);
  }
  return [...new Set(out)];
}"""

_SEEK_END_JS = """() => document.getAnimations().forEach(a => {
  a.pause();
  const end = a.effect.getComputedTiming().endTime;
  a.currentTime = (a.animationName || '').startsWith('kit-fx-') ? 0 : (isFinite(end) ? end : 6000);
})"""

_REPAIR_SYSTEM = """You fix layout bugs in animated HTML scenes for a vertical video \
({w}x{h} CSS px). Classes starting with k- and CSS variables like --accent come \
from a shared stylesheet that is injected later; keep using them.

For each scene you get the HTML and a list of problems measured on its final \
frame. Fix ONLY those problems: move, resize, re-flow or shorten-to-fit the \
offending elements. Keep the content, wording, animations, timing and style. \
Text must stay inside the frame (8px margin) and above y={cap_y}px. Prefer \
putting text in the .k-stage flex flow over absolute positioning.

Return every scene you were given, complete, as
<scene index="N">
<!doctype html>...full document...
</scene>"""


def _lint(page, html: str) -> list[str]:
    page.set_content(html, wait_until="load")
    page.evaluate("async () => { await document.fonts.ready; }")
    page.evaluate(_SEEK_END_JS)
    return page.evaluate(_LINT_JS)


def _repair_scenes(cfg: Config, raw: dict[int, str], issues: dict[int, list[str]],
                   w: int, h: int) -> dict[int, str]:
    """One cheap call that fixes only the flagged scenes. Failure = keep originals."""
    import anthropic

    parts = []
    for i, probs in issues.items():
        parts.append(f'Scene {i} problems:\n- ' + "\n- ".join(probs)
                     + f'\n<scene index="{i}">\n{raw[i]}\n</scene>\n')
    client = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"), max_retries=1)
    msg = client.messages.create(
        model=cfg.get("video.claude_motion.repair_model", "claude-haiku-5-5"),
        max_tokens=16000,
        output_config={"effort": cfg.get("video.claude_motion.repair_effort", "low")},
        system=_REPAIR_SYSTEM.format(w=w, h=h, cap_y=int(h * 0.7)),
        messages=[{"role": "user", "content": "\n".join(parts)}],
    )
    text = "".join(b.text for b in msg.content if b.type == "text")
    fixed = {int(i): html for i, html in _SCENE_RE.findall(text) if int(i) in issues}
    print(f"  claude_motion repair: {len(fixed)}/{len(issues)} scenes returned, "
          f"in_tokens={msg.usage.input_tokens} out_tokens={msg.usage.output_tokens}")
    return fixed


def _check_and_repair(cfg: Config, page, raw: dict[int, str], w: int, h: int) -> dict[int, str]:
    """Lint every scene; repair flagged ones; keep whichever version lints cleaner."""
    issues = {}
    for i, html in raw.items():
        try:
            found = _lint(page, inject_kit(html))
        except Exception as exc:  # noqa: BLE001 - a lint failure must not cost the scene
            print(f"  ! scene {i} lint failed ({exc})")
            continue
        if found:
            issues[i] = found
            print(f"  ~ scene {i} layout: " + "; ".join(found))
    if not issues or not cfg.get("video.claude_motion.repair", True):
        return raw
    try:
        fixed = _repair_scenes(cfg, raw, issues, w, h)
    except Exception as exc:  # noqa: BLE001
        print(f"  ! claude_motion repair failed ({exc}) — keeping originals.")
        return raw
    out = dict(raw)
    for i, html in fixed.items():
        try:
            after = _lint(page, inject_kit(html))
        except Exception:  # noqa: BLE001
            continue
        if len(after) < len(issues[i]):
            out[i] = html
            print(f"  ✓ scene {i} repaired ({len(issues[i])} → {len(after)} issues)")
        else:
            print(f"  ~ scene {i} repair not better ({len(after)} issues) — kept original")
    return out


def _render_scene(page, html: str, duration: float, fps: int, dest: Path, preset: str) -> Path:
    page.set_content(html, wait_until="load")
    page.evaluate("async () => { await document.fonts.ready; document.getAnimations().forEach(a => a.pause()); }")
    if not page.evaluate("document.getAnimations().filter(a => !(a.animationName || '').startsWith('kit-fx-')).length"):
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

    clips: list[Path | None] = [None] * len(segs)
    if scenes:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": cw, "height": ch}, device_scale_factor=_SCALE)
            scenes = _check_and_repair(cfg, page, scenes, cw, ch)
            scenes = {i: inject_kit(html) for i, html in scenes.items()}
            for i, html in scenes.items():
                (scene_dir / f"scene_{i:02d}.html").write_text(html, encoding="utf-8")
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
