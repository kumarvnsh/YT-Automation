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


def house_scene(narration: str, maps: list[dict], duration: float = 6.0, photos: list[dict] = ()) -> str:
    """Animated recovery: vetted geography and timed excerpts, without invented facts."""
    from html import escape
    duration = max(duration, .1)
    words = narration.split()
    # Even chunks of <=6 words, so no lone trailing word like "ends." gets its own beat.
    size = -(-len(words) // max(1, -(-len(words) // 6))) if words else 6
    chunks = [' '.join(words[i:i+size]) for i in range(0, len(words), size)] or ['']
    labels = ''.join(
        f'<div class="house-beat" style="--delay:{i * duration / len(chunks):.3f}s;'
        f'--hold:{duration / len(chunks):.3f}s">{escape(chunk)}</div>'
        for i, chunk in enumerate(chunks))
    placeholders = ''.join(f'<histold-map id="{a["map_id"]}"></histold-map>' for a in maps)
    facts = key_facts(narration) or key_words(narration)
    # Explain with the narration's own dates, numbers and names; maps only establish place.
    cards = ''.join(
        f'<div class="house-fact k-pop" style="--d:{.1 + i * duration / (len(facts) + 1):.3f}s">'
        f'<div class="k-huge" style="font-size:{72 if len(f) > 4 else 112}px">{escape(f)}</div></div>'
        if any(c.isdigit() for c in f) else
        f'<div class="house-fact k-pop" style="--d:{.1 + i * duration / (len(facts) + 1):.3f}s">'
        f'<div class="k-title" style="font-size:{40 if len(f) > 14 else 56}px">{escape(f)}</div></div>'
        for i, f in enumerate(facts))
    if cards:
        cards = ('<div class="house-facts">' + cards + '</div>'
                 '<svg viewBox="0 0 460 40" aria-hidden="true"><path class="k-draw" pathLength="1" '
                 'd="M20 20H440" fill="none" stroke="var(--accent)" stroke-width="4" style="--t:' f'{duration:.2f}s"/></svg>')
    if cards:
        from .icon_assets import DEFAULTS, candidate_icons
        icon = next((n for n in candidate_icons([narration], limit=1) if n not in DEFAULTS), None)
        from .portrait_assets import mentioned
        photo = next((p for p in photos if mentioned(p, narration)), None)
        if photo:  # the person being talked about beats any illustration
            cards = f'<histold-photo id="{photo["id"]}" class="k-pop" style="width:210px"></histold-photo>' + cards
        elif icon:  # a matched illustration above the facts
            cards = (f'<histold-icon name="{icon}" class="k-pop" style="width:150px;--d:.05s"></histold-icon>'
                     + cards)
    # Abstract chronological diagram when no geographic asset or fact is available.
    art = placeholders or cards or ('<svg viewBox="0 0 460 220" aria-label="Narration progression">'
        '<path d="M30 110H430" fill="none" stroke="var(--bone)" stroke-width="6"/>'
        + ''.join(f'<circle cx="{x}" cy="110" r="{r}" fill="var(--accent)" '
                  f'class="k-pulse" style="--t:1.8s;--d:{i*.3}s"/>'
                  for i, (x, r) in enumerate(((55, 24), (175, 34), (295, 24), (405, 34))))
        + '</svg>')
    html = ('<!doctype html><html><head><style>'
            '.k-stage{padding:128px 36px 24px;justify-content:flex-start;gap:20px}'
            '.house-copy{position:relative;width:100%;height:100px;flex-shrink:0}'
            '.house-beat{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;'
            'font:700 38px/1.12 var(--display);text-align:center;opacity:0;'
            'animation:house-beat var(--hold) linear var(--delay) both}'
            '@keyframes house-beat{0%{opacity:0;transform:translateY(8px)}1%{opacity:1}'
            '85%{opacity:1;transform:translateY(-4px)}100%{opacity:0;transform:translateY(-8px)}}'
            '.house-art{width:100%;transform-origin:center;animation:house-camera var(--scene) linear both}'
            '@keyframes house-camera{from{transform:scale(.88) translateX(-12px)}'
            'to{transform:scale(.98) translateX(6px)}}'
            '.house-art svg path{stroke:var(--parch);stroke-width:1.5;stroke-dasharray:18 10;'
            'animation:house-outline 1.5s linear infinite}'
            '@keyframes house-outline{to{stroke-dashoffset:-56}}'
            '.house-facts{display:flex;flex-direction:column;align-items:center;gap:14px;text-align:center}'
            '.house-fact{max-width:460px;overflow-wrap:anywhere}.house-art .k-icon{margin:0 auto 10px}'
            '.house-rail{height:8px;width:100%;background:var(--bone);overflow:hidden;flex-shrink:0}'
            '.house-rail::after{content:"";display:block;width:42%;height:100%;background:var(--accent);'
            'animation:house-progress 2s linear infinite alternate}'
            '@keyframes house-progress{from{transform:translateX(0)}to{transform:translateX(138%)}}'
            '</style></head><body><div class="k-stage" '
            f'style="--scene:{duration}s" aria-label="{escape(narration, quote=True)}">'
            '<div class="house-copy">' + labels + '</div><div class="house-art">' + art +
            '</div><div class="house-rail"></div></div></body></html>')
    html = bind_maps(html, maps)
    return html

_STOP = set('about after again against because before being below between could during every '
             'first found other their there these thing those through under until where which while '
             'would never still always people really actually today years'.split())


def key_words(narration: str, limit: int = 3) -> list[str]:
    """Longest content words, in spoken order, when the line has no dates, numbers or names."""
    words = [w.strip('.,;:!?"\'()').lower() for w in narration.split()]
    words = list(dict.fromkeys(w for w in words if len(w) >= 5 and w.isalpha() and w not in _STOP))
    keep = set(sorted(words, key=len, reverse=True)[:limit])
    return [w for w in words if w in keep]


_ARROW_ONLY = re.compile(r'<(div|span)\b[^>]*>\s*(?:&[rl]arr;|&[rl]Arr;|[→⇒➜➔➝⟶⟹↓←])\s*</\1>', re.I)


def strip_layout_arrows(html: str) -> str:
    """k-steps/k-cause draw their own arrows; drop arrow-only elements the model adds to them."""
    if 'k-steps' not in html and 'k-cause' not in html:
        return html
    return _ARROW_ONLY.sub('', html)


def key_facts(narration: str, limit: int = 3) -> list[str]:
    """Dates, numbers and proper names stated in the narration, in spoken order."""
    found = []
    pattern = (r'\b\d[\d,.]*(?:\s?%|\s(?:million|billion|thousand)\b|\s?(?:BC|BCE|AD|CE)\b)?'
               r'|(?<![.!?]\s)(?<!^)\b[A-Z][a-z]+(?:\s+(?:of\s+|the\s+)?[A-Z][a-z]+)*')
    for m in re.finditer(pattern, narration.strip()):
        fact = m.group(0).strip().rstrip('.,')
        if fact and fact not in found:
            found.append(fact)
    return found[:limit]


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
- Illustrations: <histold-icon name="NAME" class="k-pop" style="--d:.4s;width:150px;color:var(--gold)"></histold-icon> \
inserts a vetted, detailed illustration (it fills currentColor). Use ONLY names from the per-video \
icon list. Prefer them to hand-drawn SVG for people, objects, buildings, tools, animals and vehicles; \
combine 2-4 into a small diagram. Never draw your own figurative SVG when an icon fits.
- Explainer layouts (children reveal one by one every --gap, default .8s, starting at --d): \
.k-steps (a process, 2-4 steps, arrows added), .k-cause (cause => effect, 2 children), \
.k-compare (then/now or A/B, exactly 2 children), .k-timeline (2-4 children, each <b>date</b> + label). \
Each child = one icon + one short .k-label. Arrows are drawn automatically: never add arrow \
characters or arrow elements inside these layouts. Use one whenever the narration explains how or why \
something happened, a process, a change, a comparison or a sequence of dates.
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
- Never open on a standalone static map or narration slide. Integrate geography \
with animated outlines, meaningful diagram builds, timed emphasis and short labels. \
A tiny camera zoom or ambient background drift alone is insufficient. Develop \
the foreground throughout every scene, including the first spoken line.
- Show, don't transcribe: maps, timelines, counters, silhouettes, diagrams, \
dates, names and numbers. On-screen text is short (max ~6 words per element). \
The narration is already captioned, so do not repeat it.
- Vary the composition from scene to scene; don't reuse one layout.
- Only state facts that appear in the narration. Never invent dates, numbers \
or quotes. Draw people as simple silhouettes or icons, never realistic faces; \
supplied <histold-photo> portraits are the only real faces allowed.
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
                   *[min(final, t) for t in (.15, .3)],
                   *[min(final, n * .75) for n in range(1, int(duration / .75) + 1)],
                   *[min(final, max(0, float(c['time']))) for c in cues],
                   *[min(final, float(t)) for t in range(2, int(duration), 2)]})


def summarize_motion(differences: list[float], times: list[float], limit: float = 1.5) -> list[str]:
    idle = 0.0
    for difference, start, end in zip(differences, times, times[1:]):
        idle = idle + end - start if difference < .002 else 0
        if idle >= limit:
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
            # Share of pixels that visibly changed: a moving label or diagram counts even when
            # the frame-wide mean barely moves (that mean rejected our own reference scene).
            differences.append(float(np.mean(np.abs(frame - previous) > .04)))
        previous = frame
        visible_samples.append(page.evaluate("""() => [...document.querySelectorAll('.k-stage *, svg path, svg circle, svg rect, svg polygon')].some(e => {
          const r=e.getBoundingClientRect(); let visible=true;
          for(let n=e;n&&n.nodeType===1;n=n.parentElement){const s=getComputedStyle(n);if(s.display==='none'||s.visibility==='hidden'||+s.opacity<.05)visible=false;}
          return visible && r.width>2 && r.height>2 && r.right>0 && r.left<innerWidth && r.bottom>0 && r.top<(innerHeight - (parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--caption-reserve')) || innerHeight * .3)) &&
            (['path','circle','rect','polygon'].includes(e.tagName.toLowerCase())||[...e.childNodes].some(n=>n.nodeType===3&&n.textContent.trim()));
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
              const r=e.getBoundingClientRect();return r.width>5&&r.height>5&&r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=(innerHeight - (parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--caption-reserve')) || innerHeight * .3));
            })""")
            held = held + .25 if visible else 0
            if held >= 2.25:
                break
        if held < 2.25:
            errors.append('Required map outlines and labels are not visible for two seconds')
    if duration >= .3 and sum(d for d, t in zip(differences, times[1:]) if t <= .3) < .002:
        errors.append('static_opening')
    # A built-up scene may hold its payoff briefly; only a long freeze is a defect.
    warnings = []
    if summarize_motion(differences, times, limit=3.0):
        errors.append('static_foreground')
    elif summarize_motion(differences, times):
        warnings.append('brief_static_hold')
    return {'errors': sorted(set(errors)), 'warnings': warnings, 'sample_times': times}


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
    # Drop requests for maps we have not vetted rather than discarding the whole scene.
    return re.sub(r'<histold-map\b[^>]*>\s*</histold-map>', '', html)


def ensure_opening_maps(html: str, maps: list[dict]) -> str:
    """Insert required establishing-map placeholders the model forgot, at the top of .k-stage."""
    missing = ''.join(f'<histold-map id="{a["map_id"]}"></histold-map>' for a in maps
                      if f'<histold-map id="{a["map_id"]}"></histold-map>' not in html)
    if not missing:
        return html
    return re.sub(r'(<div[^>]*class="[^"]*\bk-stage\b[^"]*"[^>]*>)', lambda m: m.group(1) + missing, html, count=1)


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
                     'The binding supplies the geometry and layout. Keep labels readable for >=2 seconds '
                     'while animating outlines, emphasis and story graphics from the first frame; do not wait '
                     'for a static establishing shot before starting the action. '
                     'Do not draw substitute map paths, invented routes or unsupported borders.')
    portraits = cfg.get('production.portraits', [])
    if portraits:
        lines.append('Archival portraits (real, licence-checked): ' + '; '.join(
            f'id={p["id"]} = {p["name"]} ({p["years"]})' for p in portraits) +
            '. When the narration names that person, show <histold-photo id="ID" class="k-rise" '
            'style="width:260px"></histold-photo> inside .k-stage, readable for at least 2 seconds, with a short '
            'label of who they are. The binding adds the frame, name and dates. Never draw their face yourself. '
            'Show each portrait in at most two scenes.')
    from .icon_assets import candidate_icons
    icons = candidate_icons([title] + [f"{seg.narration} {' '.join(seg.keywords)}" for seg in segs])
    lines.append('Icon list for <histold-icon name="...">: ' + ', '.join(icons) +
                 '. Aim for an icon or explainer layout in most scenes.')
    for i, (seg, (s, e)) in enumerate(zip(segs, windows)):
        lines.append(
            f"Segment {i} — beat: {seg.beat or 'n/a'} — duration: {e - s:.1f}s\n"
            f"Narration: {seg.narration.strip()}\n"
            f"Visual keywords: {', '.join(seg.keywords[:4])}\n"
        )
    # Writing every scene can take well over the SDK's 10-min default, with long
    # quiet stretches while the model thinks; summarized thinking keeps bytes flowing.
    client = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"), timeout=1800, max_retries=1)
    system = _SYSTEM.format(w=w, h=h, cap_y=int(h * 0.7), w_text=w - 72, example=_EXAMPLE.strip())
    if cfg.get('video.claude_motion.style') == 'cinematic_2d':
        from .cinematic_motion import system_prompt
        system = system_prompt(w, h)
    with client.beta.messages.stream(
        model=cfg.get("video.claude_motion.model", "claude-sonnet-5-5"),
        max_tokens=int(cfg.get("video.claude_motion.max_tokens", 30000)),
        thinking={"type": "adaptive", "display": "summarized"},
        output_config={"effort": cfg.get("video.claude_motion.effort", "medium")},
        betas=["server-side-fallback-2026-07-01"],
        extra_body={"fallbacks": "default"},
        system=system,
        messages=[{"role": "user", "content": "\n".join(lines)}],
    ) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        raise RuntimeError(f"refused: {msg.stop_details}")
    text = "".join(b.text for b in msg.content if b.type == "text")
    scenes = parse_scenes(text, len(segs))
    print(f"  claude_motion: {len(scenes)}/{len(segs)} scenes, stop={msg.stop_reason}, "
          f"in_tokens={msg.usage.input_tokens} out_tokens={msg.usage.output_tokens}")
    missing = sorted(set(range(len(segs))) - scenes.keys())
    if missing and cfg.get('video.claude_motion.style') == 'cinematic_2d':
        # A truncated response often contains all but the last complete scene.
        # Keep usable work and spend one bounded request on missing scenes only.
        request = "\n".join(lines) + f"\nReturn ONLY these missing zero-based scene IDs: {missing}. Return complete HTML documents, not a continuation of partial HTML."
        with client.beta.messages.stream(
            model=cfg.get("video.claude_motion.model", "claude-sonnet-5-5"),
            max_tokens=16000,
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": cfg.get("video.claude_motion.effort", "medium")},
            betas=["server-side-fallback-2026-07-01"], extra_body={"fallbacks": "default"},
            system=system, messages=[{"role": "user", "content": request}],
        ) as stream:
            recovery = stream.get_final_message()
        recovered = parse_scenes("".join(b.text for b in recovery.content if b.type == "text"), len(segs))
        scenes.update({i: html for i, html in recovered.items() if i in missing})
        print(f"  cinematic completion: {len(scenes)}/{len(segs)} scenes, "
              f"in_tokens={recovery.usage.input_tokens} out_tokens={recovery.usage.output_tokens}")
    return scenes


# Layout check run on each scene's final frame (ambient kit drift at rest).
# Text boxes are inset vertically to the glyph core so tight-but-clean
# stacks (big number over a label) don't count as overlap.
_LINT_JS = """() => {
  const W = innerWidth, H = innerHeight, CAP = H - (parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--caption-reserve')) || H * .3), M = 8;
  const seen = e => { for (let n = e; n && n.nodeType === 1; n = n.parentElement) {
    const cs = getComputedStyle(n);
    if (cs.display === 'none' || cs.visibility === 'hidden' || +cs.opacity < 0.05) return false; } return true; };
  const slamming = e => { for (let n = e; n && n.nodeType === 1; n = n.parentElement)
    if (n.getAnimations().some(a => a.animationName === 'k-slam' && a.effect.getComputedTiming().progress !== null
                                    && a.effect.getComputedTiming().progress < 1)) return true; return false; };
  const boxes = [];
  for (const e of document.body.querySelectorAll('*')) {
    const tag = e.tagName.toLowerCase();
    if (e.closest('svg') && tag !== 'text' && tag !== 'tspan') continue;
    const counter = e.classList.contains('k-count');
    if (!counter && ![...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())) continue;
    if (!seen(e)) continue;
    // The kit's k-slam stamp enters at 2.4x scale; judge it on its landed size, not mid-flight.
    if (slamming(e)) continue;
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

For each scene you get the HTML and problems measured across its timeline. \
For static_foreground or static_opening, add meaningful foreground development from 0–0.3s \
through the end: animated diagrams, map emphasis or staged reveals; ambient \
background drift alone is insufficient. For layout problems, move, resize, re-flow or shorten-to-fit the \
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
    repair_system = _REPAIR_SYSTEM.format(w=w, h=h, cap_y=int(h * 0.7))
    if cfg.get("video.claude_motion.style") == "cinematic_2d":
        from .cinematic_motion import system_prompt
        repair_system = system_prompt(w, h) + "\nRepair the supplied scenes using their measured errors. Preserve content, native timing and authored cinematic artwork. Return every supplied complete scene.\n"
    msg = client.messages.create(
        model=cfg.get("video.claude_motion.repair_model", "claude-haiku-5-5"),
        max_tokens=16000,
        output_config={"effort": cfg.get("video.claude_motion.repair_effort", "low")},
        system=repair_system,
        messages=[{"role": "user", "content": "\n".join(parts)}],
    )
    text = "".join(b.text for b in msg.content if b.type == "text")
    fixed = {int(i): html for i, html in _SCENE_RE.findall(text) if int(i) in issues}
    print(f"  claude_motion repair: {len(fixed)}/{len(issues)} scenes returned, "
          f"in_tokens={msg.usage.input_tokens} out_tokens={msg.usage.output_tokens}")
    return fixed


def _check_and_repair(cfg: Config, page, raw: dict[int, str], w: int, h: int) -> dict[int, str]:
    """Lint every scene; repair flagged ones; keep whichever version lints cleaner."""
    def prepared(html, index):
        if cfg.get("video.claude_motion.style") == "cinematic_2d":
            from .cinematic_motion import prepare_html
            return prepare_html(html, index)
        return inject_kit(html)
    issues = {}
    for i, html in raw.items():
        try:
            found = _lint(page, prepared(html, i))
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
            after = _lint(page, prepared(html, i))
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

    cinematic = cfg.get('video.claude_motion.style') == 'cinematic_2d'
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
        if cfg.get('video.claude_motion.portraits', True) and 'portraits' not in st:
            from .portrait_assets import fetch_portraits
            st['portraits'] = fetch_portraits(st.get('research', {}).get('people', []),
                                              ' '.join(s.narration for s in segs), stage / 'portraits')
        cfg.raw.setdefault('production', {})['storyboard'] = board
        cfg.raw['production']['map_assets'] = [{k: v for k, v in a.items() if k != 'svg'} for a in maps]
        cfg.raw['production']['portraits'] = st.get('portraits', [])

    try:
        scenes = _request_scenes(cfg, st["script"]["title"], segs, windows, cw, ch)
    except Exception as exc:  # noqa: BLE001 - fall back to stock for every segment
        if cinematic:
            raise RuntimeError('Cinematic 2D scene generation failed; refusing old-style fallback') from exc
        recovery = 'local house templates' if production else 'stock'
        print(f"  ! claude_motion: scene request failed ({exc}) — using {recovery}.")
        scenes = {}
    def prepare(html, index):
        from .icon_assets import bind_icons
        from .portrait_assets import bind_photos
        bound = bind_photos(bind_icons(strip_layout_arrows(html))[0], st.get('portraits', []))[0]
        if cinematic:
            from .cinematic_motion import prepare_html
            return prepare_html(bound, index)
        palette = PALETTE_SEQUENCE[((st.get('editorial_slot') or 0) + index) % len(PALETTE_SEQUENCE)]
        return apply_palette(bound, palette)

    scene_dir = stage / "scenes"
    scene_dir.mkdir(exist_ok=True)
    for i, html in scenes.items():  # keep the model's originals for diagnosing rejections
        (scene_dir / f"scene_{i:02d}.claude.html").write_text(html, encoding="utf-8")

    clips: list[Path | None] = [None] * len(segs)
    report = {'schema_version': 1, 'scenes': {}, 'warnings': [], 'errors': [], 'recoveries': [],
              'narration_hash': st.get('storyboard', {}).get('narration_hash'),
              'renderer': 'cinematic_2d' if cinematic else 'claude_motion'}
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
                    if i == 0 and maps:
                        html = ensure_opening_maps(html, maps)
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
                            html = bind_maps(ensure_opening_maps(html, maps) if i == 0 else html, maps)
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
                    if cinematic:
                        raise RuntimeError(f'Cinematic scene {i} failed visual checks: '+ '; '.join(original_errors))
                    scenes[i] = house_scene(segs[i].narration, maps if i == 0 else [], e-s, st.get('portraits', []))
                    inspection = inspect_scene(page, prepare(scenes[i], i), e-s, fps,
                        board['scenes'][i]['cues'], cfg.get('video.branding.enabled', False))
                    report['recoveries'].append({'scene': i, 'method': 'local_house_scene',
                        'original_errors': original_errors, 'validated': not inspection['errors']})
                    report['scenes'][str(i)] = inspection
                    print(f'  ✓ scene {i} recovered with local house template (was: {"; ".join(original_errors[:3])})')
            from .portrait_assets import cap_photos
            scenes = {i: prepare(html, i) for i, html in cap_photos(scenes).items()}
            if any('class="k-icon' in html for html in scenes.values()):
                from .icon_assets import CREDIT
                st['credits'] = sorted(set(st.get('credits', [])) | {CREDIT})
            shown = {p['id'] for p in st.get('portraits', []) if f'data-photo-id="{p["id"]}"' in ''.join(scenes.values())}
            st['credits'] = sorted(set(st.get('credits', [])) |
                                   {p['credit'] for p in st.get('portraits', []) if p['id'] in shown})
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
                    if cinematic:
                        raise
                    if production:
                        scenes[i] = prepare(house_scene(segs[i].narration, maps if i == 0 else [], e-s, st.get('portraits', [])), i)
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
        if cinematic:
            raise RuntimeError(f"Cinematic scene {i} missing; refusing legacy/stock fallback")
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
