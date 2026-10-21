"""Claude-written motion graphics: one animated HTML scene per script segment.

Claude writes each scene as a self-contained HTML page animated only with CSS
@keyframes / element.animate(). Every scene is rendered deterministically
with headless Chromium: all animations are paused, then each frame is captured
by seeking document.getAnimations() to t. That way the output does not depend
on wall-clock speed, and a slow CI runner produces the same video as a fast laptop.

One API call per video returns every scene. Production recovers missing or invalid
scenes using local, checked house templates and vetted maps. Legacy jobs retain
the stock/AI-image recovery chain.
"""
from __future__ import annotations

import re
import json
import subprocess
from pathlib import Path

from .config import Config, env

# Scenes are authored at half resolution and captured at 2x device scale, so
# Claude works in a 540x960 CSS viewport while the output is 1080x1920.
_SCALE = 2

_SCENE_RE = re.compile(r'''<scene\s+index\s*=\s*['"](\d+)['"]\s*>\s*(.*?)\s*</scene>''', re.DOTALL)
_HEAD_RE = re.compile(r"<head[^>]*>", re.IGNORECASE)

# Shared look (fonts, palette, grain, vignette, drift, motion utilities) is
# injected by us, so Claude's tokens go into composition instead of boilerplate.
_KIT_CSS = Path(__file__).with_name("motion_kit.css").read_text(encoding="utf-8")
_KIT = ('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Oswald:wght@500;600;700'
        '&family=Cormorant+Garamond:ital,wght@1,500&display=block">\n<style>' + _KIT_CSS + "</style>\n")
_EXAMPLE = Path(__file__).with_name("motion_example.html").read_text(encoding="utf-8")

PALETTES = {
    'navy_teal': dict(ink='#09192e', char='#102d49', soot='#164982', parch='#f5f8ff', bone='#a9c9ec', dim='#c0d2e8', accent='#40e0d0'),
    'ocean_cyan': dict(ink='#07243c', char='#0a3855', soot='#0c628a', parch='#f5f8ff', bone='#7dd3fc', dim='#c0deed', accent='#67e8f9'),
    'deep_teal_mint': dict(ink='#063436', char='#094a4e', soot='#0e6670', parch='#f5f8ff', bone='#67e8f9', dim='#bce2e0', accent='#86efac'),
    'blue_gold': dict(ink='#0b254a', char='#133969', soot='#19559b', parch='#f5f8ff', bone='#93c5fd', dim='#c5dbf5', accent='#facc15'),
    'teal_coral': dict(ink='#082d3b', char='#0b4353', soot='#115e72', parch='#f5f8ff', bone='#fb7185', dim='#bce2e0', accent='#5eead4'),
    'ice_teal': dict(ink='#e6f5fc', char='#d1eff8', soot='#b8e7f5', parch='#102d45', bone='#0369a1', dim='#31566d', accent='#087788'),
}
# Blue and teal lead; gold, coral and the light treatment provide occasional variety.
PALETTE_SEQUENCE = ('navy_teal', 'ocean_cyan', 'deep_teal_mint', 'navy_teal',
                    'ocean_cyan', 'blue_gold', 'teal_coral', 'ice_teal')


def apply_palette(html: str, name: str) -> str:
    html = inject_kit(html)
    html = re.sub(r'<style id="histold-palette">.*?</style>', '', html, flags=re.S)
    variables = ';'.join(f'--{key}:{value}' for key, value in PALETTES[name].items())
    style = ('<style id="histold-palette">:root{' + variables + '}'
             'body{background:radial-gradient(120% 80% at 80% 15%,var(--soot),var(--char) 45%,var(--ink))!important;color:var(--parch)!important}'
             '.k-card{background:var(--parch);color:var(--ink);box-shadow:0 18px 40px #0003}'
             '.k-kicker,.k-title,.k-serif,.k-label{text-shadow:none}</style>')
    return re.sub(r'</head>', lambda _: style + '</head>', html, count=1, flags=re.I)


def inject_kit(html: str) -> str:
    """Put the kit first in <head> so the scene's own CSS overrides it."""
    if _KIT_CSS in html:
        return html
    m = _HEAD_RE.search(html)
    if m:
        return html[:m.end()] + _KIT + html[m.end():]
    return "<!doctype html><html><head>" + _KIT + "</head>" + re.sub(r"(?i)<!doctype[^>]*>", "", html, count=1)


def parse_scenes(text: str, count: int) -> dict[int, str]:
    scenes = {int(i): html for i, html in _SCENE_RE.findall(text)}
    # Some responses use storyboard-style 1-based IDs; normalize only an unambiguous complete set.
    if set(scenes) == set(range(1, count + 1)):
        return {i - 1: html for i, html in scenes.items()}
    return {i: html for i, html in scenes.items() if 0 <= i < count}


def house_scene(narration: str, maps: list[dict], duration: float = 6.0) -> str:
    """Local recovery scene: supplied narration and pinned geography, no generated facts or images."""
    from html import escape
    placeholders = ''.join(f'<histold-map id="{a["map_id"]}"></histold-map>' for a in maps)
    art = placeholders or '<div class="k-rule k-grow" style="--t:4s"></div>'
    html = ('<!doctype html><html><head><style>'
            '.k-stage{padding:128px 36px 24px;justify-content:flex-start;gap:18px}'
            '.k-title{font-size:30px;line-height:1.12;text-transform:none;max-width:468px}'
            '</style></head><body><div class="k-stage">'
            '<div class="k-title k-rise">' + escape(narration) + '</div>' + art + '</div></body></html>')
    html = bind_maps(html, maps)
    return html.replace('class="histold-map" style="width:100%"',
        f'class="histold-map k-pan" style="width:100%;--t:{duration}s;--s0:1;--s1:1.02"')

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
- Vars: --ink --char --soot (backgrounds), --parch --bone --dim (readable text), \
--teal --sky --mint --gold --coral, --amber (gold alias), --verm (coral alias), \
--accent (teal by default), \
--display (Oswald), --serif (Cormorant italic).
- Layout: .k-stage = the safe area above captions (flex column, centered, gap). \
.k-bg = full-frame layer behind it; .k-grid = faint grid texture.
- Type: .k-kicker (small accent label with rules), .k-title (64px condensed caps), \
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
- Use the approved blue/teal family, with cyan, mint, small gold or coral accents. \
No purple, sepia wash or brown/orange-dominated scenes. The renderer supplies \
each scene's palette; use CSS variables rather than hardcoded background/text colors. \
Use two or three coordinated colors for meaningful objects and diagrams, not a rainbow. \
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


def sample_times(duration: float, fps: int, cues: list[dict]) -> list[float]:
    final = max(0, (round(duration * fps) - 1) / fps)
    return sorted({0.0, min(final, duration / 2), final,
                   *[min(final, max(0, float(c['time']))) for c in cues],
                   *[min(final, float(t)) for t in range(2, int(duration), 2)]})


def summarize_motion(differences: list[float], times: list[float]) -> list[str]:
    idle = 0.0
    for difference, start, end in zip(differences, times, times[1:]):
        idle = idle + end - start if difference < .002 else 0
        if idle >= 4:
            return ['static_foreground']
    return []


def inspect_scene(page, html: str, duration: float, fps: int, cues: list[dict], branding: bool) -> dict:
    """Inspect actual timeline samples; ambient film effects cannot satisfy motion checks."""
    import io
    import numpy as np
    from PIL import Image
    page.set_content(html, wait_until='load')
    page.evaluate('async () => { await document.fonts.ready; document.getAnimations().forEach(a => a.pause()); }')
    page.add_style_tag(content='html::before,html::after{visibility:hidden!important}')
    errors, differences, previous = [], [], None
    visible_samples = []
    times = sample_times(duration, fps, cues)
    for time in times:
        page.evaluate("t => document.getAnimations().forEach(a => {a.currentTime = (a.animationName || '').startsWith('kit-fx-') ? 0 : t})", time * 1000)
        errors.extend(f'{time:.2f}s: {e}' for e in page.evaluate(_LINT_JS))
        if branding:
            collisions = page.evaluate("""() => [...document.querySelectorAll('.k-stage *')].filter(e => {
              const r=e.getBoundingClientRect(),s=getComputedStyle(e);
              return s.visibility!=='hidden' && +s.opacity>0.05 && e.textContent.trim() && r.left<innerWidth*.17 && r.right>innerWidth*.045 && r.top<innerHeight*.12 && r.bottom>innerHeight*.045;
            }).length""")
            if collisions:
                errors.append(f'{time:.2f}s: text overlaps HISTOLD safe area')
        frame = np.asarray(Image.open(io.BytesIO(page.screenshot(type='png'))).convert('L').resize((108, 192)), dtype=float) / 255
        if previous is not None:
            differences.append(float(np.mean(np.abs(frame - previous))))
        previous = frame
        visible_samples.append(page.evaluate("""() => [...document.querySelectorAll('.k-stage *, svg path')].some(e => {
          const r=e.getBoundingClientRect(); let visible=true;
          for(let n=e;n&&n.nodeType===1;n=n.parentElement){const s=getComputedStyle(n);if(s.display==='none'||s.visibility==='hidden'||+s.opacity<.05)visible=false;}
          return visible && r.width>2 && r.height>2 && r.right>0 && r.left<innerWidth && r.bottom>0 && r.top<innerHeight*.7 &&
            (e.tagName.toLowerCase()==='path'||[...e.childNodes].some(n=>n.nodeType===3&&n.textContent.trim()));
        })"""))
    # An opening reveal may begin invisible, but later blank samples are defects.
    for time, visible in zip(times, visible_samples):
        if time >= .3 and not visible:
            errors.append(f'{time:.2f}s: Scene has no visible foreground content')
    if not any(visible_samples):
        errors.append('Scene has no visible foreground content')
    if 'data-map-id=' in html:
        # Independently sample the establishing labels; a hidden placeholder is not a map.
        held = 0.0
        for tick in range(int(min(duration, 5) * 4) + 1):
            time = min(tick / 4, max(0, duration - 1 / fps))
            page.evaluate("t => document.getAnimations().forEach(a => {a.currentTime = (a.animationName || '').startsWith('kit-fx-') ? 0 : t})", time * 1000)
            visible = page.evaluate("""() => [...document.querySelectorAll('[data-map-id]')].every(e => {
              for(let n=e;n&&n.nodeType===1;n=n.parentElement){const s=getComputedStyle(n);if(s.display==='none'||s.visibility==='hidden'||+s.opacity<.9)return false;}
              const r=e.getBoundingClientRect();return r.width>5&&r.height>5&&r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight*.7;
            })""")
            held = held + .25 if visible else 0
            if held >= 2.25:
                break
        if held < 2.25:
            errors.append('Required map outlines and labels are not visible for two seconds')
    return {'errors': sorted(set(errors)), 'warnings': summarize_motion(differences, times), 'sample_times': times}


def bind_maps(html: str, assets: list[dict]) -> str:
    """Replace map placeholders with pinned geometry; the model never authors these paths."""
    from html import escape
    tokens = [f'<histold-map id="{a["map_id"]}"></histold-map>' for a in assets]
    if any(token in html for token in tokens) and any(
            a['map_id'] not in {'canada', 'united_states', 'washington', 'minnesota'} for a in assets):
        # Different extents need separate viewports; overlaying would invent relative positions.
        cards = []
        for asset in assets:
            svg = asset['svg'].replace('<svg ', '<svg style="display:block;width:100%;height:180px;color:var(--accent)" ', 1)
            cards.append(f'<div data-map-id="{escape(asset["map_id"])}" style="flex:1 1 150px;min-width:0;text-align:center">'
                         f'{svg}<div style="font-size:16px;color:var(--parch)">{escape(asset["label"])}</div></div>')
        bundle = '<div class="histold-map" style="width:100%"><div style="display:flex;flex-wrap:wrap;gap:12px">'+''.join(cards)+'</div><div class="k-label" style="font-size:10px">Present-day establishing geography · separate extents</div></div>'
        placed = False
        for token in tokens:
            if token in html:
                html = html.replace(token, bundle if not placed else '')
                placed = True
    elif any(token in html for token in tokens):
        labels = {'canada': (190, 135), 'united_states': (200, 294),
                  'washington': (62, 214), 'minnesota': (245, 235)}
        groups = []
        for asset in sorted(assets, key=lambda a: a['map_id'] in ('minnesota', 'washington')):
            x, y = labels[asset['map_id']]
            path = re.search(r'<path\b.*?/>', asset['svg']).group(0)
            color = 'var(--gold)' if asset['map_id'] in ('minnesota', 'washington') else 'var(--bone)' if asset['map_id'] == 'canada' else 'var(--accent)'
            groups.append(f'<g data-map-id="{asset["map_id"]}" color="{color}">{path}'
                          f'<text x="{x}" y="{y}" text-anchor="middle" fill="var(--parch)" stroke="var(--ink)" '
                          f'stroke-width="3" paint-order="stroke" font-size="14">{escape(asset["label"])}</text></g>')
        bundle = '<div class="histold-map" style="width:100%"><svg viewBox="0 0 480 366" role="img" aria-label="Present-day establishing geography">'+''.join(groups)+'</svg><div class="k-label" style="font-size:10px">Present-day continental context</div></div>'
        placed = False
        for token in tokens:
            if token in html:
                html = html.replace(token, bundle if not placed else '')
                placed = True
    if '<histold-map' in html:
        raise ValueError('Scene requested unverified geographic asset')
    return html


def _request_scenes(cfg: Config, title: str, segs: list, windows, w: int, h: int) -> dict[int, str]:
    import anthropic

    lines = [f"Video title: {title}", ""]
    board = cfg.get('production.storyboard')
    if board:
        board = {**board, 'scenes': [{**scene, 'scene_id': scene['segment_id']} for scene in board['scenes']]}
        lines.append('Measured storyboard with ZERO-BASED scene IDs (scene-local word/cue seconds drive animation delays):\n' + json.dumps(board))
        lines.append('Use reveal cues for meaningful development every 2–4 seconds. Hold key labels at least two seconds. '
                     'Reserve upper-left x=4.5%-17%, y=4.5%-12% for the small translucent HISTOLD logo. '
                     'Use present-day maps only as establishing context; never invent historical borders.')
    maps = cfg.get('production.map_assets', [])
    editorial = cfg.get('production.editorial')
    if editorial:
        lines.append('Facebook world-history series: ' + editorial['series']['name'] +
                     '. Establish the specific opening subject immediately with a readable map, object, '
                     'diagram or meaningful comparison. Prefer original explanatory visuals to generic stock. '
                     'Use large high-contrast labels, hold important labels long enough to read and avoid flashing '
                     'or decorative rapid cuts. Keep the series name as a short readable opening label. '
                     'Distinguish illustrative reconstructions from archival evidence; never invent archival photographs.')
    if maps:
        lines.append('Verified map IDs: ' + ', '.join(a['map_id'] for a in maps) +
                     '. In scene 0 establish ALL these locations with exact placeholders '
                     '<histold-map id="MAP_ID"></histold-map> inside .k-stage, at readable size. '
                     'These are present-day establishing outlines; world maps use separate extents and North American country/state maps share a projection. '
                     'The binding supplies the geometry and layout. Keep the establishing labels for >=2 seconds '
                     'before any schematic/inset. Do not draw substitute map paths or unsupported borders.')
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
    scenes = parse_scenes(text, len(segs))
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
    """Build silent visuals; production recovers locally, legacy jobs use stock."""
    from . import video_builder as vb
    from .assets import fetch_for_segments
    from .pipeline import _rebuild_segments

    segs = _rebuild_segments(st)
    w, h, fps = vb._dims(cfg, st["fmt"])
    preset, _ = vb._encode_settings(cfg)
    cw, ch = w // _SCALE, h // _SCALE
    windows = segment_windows([s.narration for s in segs], words, duration + 0.5)
    production = cfg.get('production.enabled', False)
    maps = []
    if production:
        from .config import Config
        from .map_assets import load_map
        from copy import deepcopy
        board = st['storyboard']
        windows = [(s['start'], s['end']) for s in board['scenes']]
        cfg = Config(deepcopy(cfg.raw))
        maps = [load_map(asset['map_id']) for asset in st.get('maps', [])]
        cfg.raw.setdefault('production', {})['storyboard'] = board
        cfg.raw['production']['map_assets'] = [{k: v for k, v in a.items() if k != 'svg'} for a in maps]

    try:
        scenes = _request_scenes(cfg, st["script"]["title"], segs, windows, cw, ch)
    except Exception as exc:  # noqa: BLE001 - fall back to stock for every segment
        recovery = 'local house templates' if production else 'stock'
        print(f"  ! claude_motion: scene request failed ({exc}) — using {recovery}.")
        scenes = {}
    def prepare(html, index):
        palette = PALETTE_SEQUENCE[((st.get('editorial_slot') or 0) + index) % len(PALETTE_SEQUENCE)]
        return apply_palette(html, palette)

    scene_dir = stage / "scenes"
    scene_dir.mkdir(exist_ok=True)

    clips: list[Path | None] = [None] * len(segs)
    report = {'schema_version': 1, 'scenes': {}, 'warnings': [], 'errors': [], 'recoveries': [],
              'narration_hash': st.get('storyboard', {}).get('narration_hash')}
    if scenes or production:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": cw, "height": ch}, device_scale_factor=_SCALE)
            if not production:
                scenes = _check_and_repair(cfg, page, scenes, cw, ch)
            else:
                issues = {}
                for i, (s, e) in enumerate(windows):
                    html = scenes.get(i)
                    if not html:
                        report['scenes'][str(i)] = {'errors': ['Missing generated scene'], 'warnings': []}
                        continue
                    if i == 0 and maps and any(f'<histold-map id="{a["map_id"]}"></histold-map>' not in html for a in maps):
                        report['scenes'][str(i)] = {'errors': ['Opening scene is missing required establishing maps'], 'warnings': []}
                        continue
                    s, e = windows[i]
                    try:
                        scenes[i] = bind_maps(html, maps)
                        inspection = inspect_scene(page, prepare(scenes[i], i), e-s, fps, board['scenes'][i]['cues'], cfg.get('video.branding.enabled', False))
                    except Exception as exc:
                        inspection = {'errors': [str(exc)], 'warnings': []}
                    report['scenes'][str(i)] = inspection
                    if inspection['errors']:
                        issues[i] = inspection['errors']
                if issues and cfg.get('video.claude_motion.repair', True):
                    try:
                        fixed = _repair_scenes(cfg, scenes, issues, cw, ch)
                    except Exception as exc:
                        print(f'  ! layout repair unavailable ({exc}) — using local house scenes')
                        fixed = {}
                    for i, html in fixed.items():
                        try:
                            html = bind_maps(html, maps)
                            if i == 0 and maps and any(f'data-map-id="{asset["map_id"]}"' not in html for asset in maps):
                                continue
                            s, e = windows[i]
                            repaired = inspect_scene(page, prepare(html, i), e-s, fps, board['scenes'][i]['cues'], cfg.get('video.branding.enabled', False))
                        except Exception:
                            continue
                        if len(repaired['errors']) < len(report['scenes'][str(i)]['errors']):
                            scenes[i] = html
                            report['scenes'][str(i)] = repaired
                for i, (s, e) in enumerate(windows):
                    original_errors = report['scenes'][str(i)]['errors']
                    if not original_errors:
                        continue
                    scenes[i] = house_scene(segs[i].narration, maps, e-s)
                    inspection = inspect_scene(page, prepare(scenes[i], i), e-s, fps,
                        board['scenes'][i]['cues'], cfg.get('video.branding.enabled', False))
                    report['recoveries'].append({'scene': i, 'method': 'local_house_scene',
                        'original_errors': original_errors, 'validated': not inspection['errors']})
                    report['scenes'][str(i)] = inspection
                    print(f'  ✓ scene {i} recovered with local house template')
            scenes = {i: prepare(html, i) for i, html in scenes.items()}
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
                    if production:
                        scenes[i] = prepare(house_scene(segs[i].narration, maps, e-s), i)
                        inspection = inspect_scene(page, scenes[i], e-s, fps,
                            board['scenes'][i]['cues'], cfg.get('video.branding.enabled', False))
                        if inspection['errors']:
                            print(f'  ! local scene {i} has layout errors; quality gate will report them')
                        report['scenes'][str(i)] = inspection
                        report['recoveries'].append({'scene':i,'method':'local_house_scene',
                            'original_errors':[str(exc)],'validated':not inspection['errors']})
                        clips[i] = _render_scene(page, scenes[i], e-s, fps, scene_dir / f'scene_{i:02d}.mp4', preset)
                        (scene_dir / f'scene_{i:02d}.html').write_text(scenes[i], encoding='utf-8')
                        continue
                    print(f"  ! scene {i} failed ({exc}) — stock fallback.")
            browser.close()

    for i, (s, e) in enumerate(windows):
        if clips[i] is not None:
            continue
        if production:
            report['warnings'].append(f'Scene {i} used stock fallback; visual review required')
            if maps:
                report['errors'].append(f'Scene {i} geographic rendering failed')
        assets = fetch_for_segments(cfg, [segs[i]], stage / f"fallback_{i:02d}", st["fmt"])[0]
        if not assets:
            raise RuntimeError(f"segment {i}: no scene and no fallback asset")
        clips[i] = vb._build_segment_clip(assets[0], e - s, w, h, fps,
                                          scene_dir / f"fallback_{i:02d}.mp4", preset)
    if production:
        for i, inspection in report['scenes'].items():
            report['errors'].extend(f'Scene {i}: {v}' for v in inspection['errors'])
            report['warnings'].extend(f'Scene {i}: {v}' for v in inspection['warnings'])
        st['render_quality'] = report
        (stage / 'render_quality.json').write_text(json.dumps(report, indent=2))
    return vb._concat(clips, stage / "silent.mp4")
