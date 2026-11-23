"""Offline typography and cinematic 2D direction for the shared HTML renderer."""
from __future__ import annotations

import base64
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import re
import math
import struct
import wave

from .config import Config

_HERE = Path(__file__).parent
_FONTS = _HERE.parent / 'assets' / 'fonts'
_THEMES = (
    ('#f1eadb', '#152f30', '#d2c5ae', '#b66549', '#786f60'),
    ('#153c3a', '#f1eadb', '#49665d', '#d49670', '#d0c5ae'),
    ('#b66549', '#fff1dc', '#d79a76', '#173f3b', '#f0d1b5'),
)


def system_prompt(w: int, h: int, design: dict | None = None) -> str:
    prompt = _HERE.joinpath('cinematic_2d_prompt.txt').read_text().format(width=w, height=h)
    if design:
        prompt += '\nUse the specified episode art direction in place of the default warm-paper look. The scene palette will match that direction.'
    if w > h:
        prompt += ('\nLandscape layout: use the extra width for side-by-side art and labels. '
                   'Use a 76px top inset below the branding rectangle and an 88px caption band. '
                   'Size art to fit the available height and keep the main illustration visible at frame zero.')
        prompt = prompt.replace('bottom 180 CSS pixels', 'bottom 88 CSS pixels').replace('Start content below 124px.', 'Start content below 76px.')
        prompt = prompt.replace('about 420px wide and 350–440px tall', 'about 500–650px wide and 230–330px tall')
    if textured():
        prompt += TEXTURE_NOTE.replace('{example}', _HERE.joinpath('cinematic_texture_example.html').read_text(encoding='utf-8'))
    from .painted_scenes import PROMPT_NOTE, enabled
    if enabled():
        prompt += PROMPT_NOTE + 'The painted-backdrop layout rules above win over the illustration size in the texture style.\n'
    return prompt


TEXTURE_NOTE = """
TEXTURED ILLUSTRATION STYLE (this run). The renderer adds paper grain, fibres and a vignette over every frame,
and these classes for your SVG artwork (filters are already defined; do not define them yourself):
  hx-ink     hand-drawn wobble for outlines and buildings      hx-wash    gouache-like mottled fill (skies, smoke, water)
  hx-shadow  soft drop shadow that lifts a subject             hx-glow    soft blur for light, fire glow, haze
  hx-hatch   fill:url(#hx-hatch) crosshatch on shaded faces    hx-flicker flame/candle flicker (transform-box set)
  hx-rise    things drifting up and fading (set --d delay, --x drift, --r spin, --t duration)
  hx-far / hx-near   slow background / foreground drift for depth (ambient; does not count as motion)
Paint scenes, not icons: build each illustration in depth layers (sky or wall, far silhouettes, the subject,
foreground), give it one light source (shade the far side with darker colour or hx-hatch, warm rim light on edges
facing the light), and use linear/radial gradients whose stop colours are palette variables or color-mix() of them
with #000 for shadows. Draw real period detail (columns, cornices, arched windows, tools, clothing). Animate the
story: things assemble, ignite, travel, fill, rise. Keep labels on calm areas with a short leader line, never on
busy art, and keep every label clear of shapes drawn above it.
This is a complete scene in the target style; match its richness, not its subject:
<example>
{example}
</example>
"""


def textured() -> bool:
    """Textured-illustration kit (paper, ink, wash, depth): on with HISTOLD_TEXTURE=1 while it is being judged."""
    import os
    return os.environ.get('HISTOLD_TEXTURE') == '1'


def _light(colour: str) -> bool:
    r, g, b = (int(colour.lstrip('#')[i:i + 2], 16) for i in (0, 2, 4))
    return .299 * r + .587 * g + .114 * b > 140


@lru_cache(maxsize=1)
def _base_css() -> str:
    faces = []
    for family, filename, style in (
        ('DM Sans', 'dm-sans-regular.ttf', 'normal'),
        ('Libre Baskerville', 'libre-baskerville-regular.ttf', 'normal'),
        ('Libre Baskerville', 'libre-baskerville-italic.ttf', 'italic'),
    ):
        data = base64.b64encode((_FONTS / filename).read_bytes()).decode('ascii')
        faces.append(f"@font-face{{font-family:'{family}';font-style:{style};font-weight:400;"
                     f"font-display:block;src:url(data:font/ttf;base64,{data}) format('truetype')}}")
    return ''.join(faces) + _HERE.joinpath('cinematic_2d.css').read_text()


def prepare_html(html: str, index: int, *, landscape=False, theme_offset=None, painted=False, texture=None) -> str:
    """Prefix helpers so authored layouts and keyframes retain precedence."""
    html = re.sub(r'<style id="cinematic-2d">.*?</style>', '', html, flags=re.S)
    # Asset binding replaces custom tags with SVG/figure elements. Preserve
    # authored sizing rules that targeted those original placeholder tags.
    def bound_selectors(match):
        css = re.sub(r'\bhistold-icon\b', '.k-icon', match.group(2))
        css = re.sub(r'\bhistold-photo\b', '.k-photo', css)
        return match.group(1) + css + match.group(3)
    html = re.sub(r'(<style\b[^>]*>)(.*?)(</style>)', bound_selectors, html, flags=re.S | re.I)
    palettes = (
        _THEMES[0],
        ('#f7eedf', '#1a1618', '#dbcbc3', '#a62936', '#524148'),
        ('#173565', '#fff2d1', '#304f7d', '#f3b857', '#d9e3fb'),
        ('#252627', '#f7f2e8', '#414244', '#e6ad53', '#dfd9cc'),
        ('#253437', '#e0fcf0', '#395052', '#b3eac8', '#c3ddd5'),
        ('#142844', '#e0f6ff', '#2d4465', '#73dffc', '#c2daee'),
        ('#f4eee2', '#332044', '#d3c3da', '#a24b36', '#594669'),
        ('#f4e5b1', '#25272a', '#d9c991', '#904634', '#4f4739'),
    )
    palette = _THEMES[index % len(_THEMES)] if theme_offset is None else palettes[theme_offset % len(palettes)]
    paper, ink, bone, accent, dim = palette
    if painted and _light(paper):
        # Over a painting the text needs light ink on dark panels; same colours, swapped roles.
        paper, ink, bone, dim = ink, paper, dim, bone
    theme = (f':root{{--parch:{paper};--ink:{ink};--bone:{bone};'
             f'--accent:{accent};--dim:{dim}}}')
    geometry = '''
:root{--caption-reserve:88px}
.k-stage{inset:0 0 88px!important;padding-top:76px!important}
.k-stage:has(.house-art){display:grid;grid-template-columns:1fr 1.45fr;grid-template-rows:1fr 8px;
  align-items:center;gap:24px;padding:76px 48px 24px!important}
.k-stage .house-copy{height:158px}
.k-stage .house-beat{font-size:32px}
.k-stage .house-art{display:flex;align-items:center;justify-content:center;gap:12px;
  min-width:0;max-height:280px;animation:none}
.k-stage .house-art>svg{width:160px;max-height:148px}
.k-stage .house-art>.k-photo{width:126px!important;flex:0 0 126px;margin:0}
.k-stage .house-art>.k-icon{width:92px!important;height:92px}
.k-stage .house-art>.house-facts{min-width:0;gap:8px}
.k-stage .house-art .house-fact div{font-size:25px!important;line-height:1.12}
.k-stage .house-art>.house-facts+svg{display:none}
.k-stage .histold-map svg{width:100%;max-height:162px}
.k-stage .house-rail{grid-column:1/-1}
''' if landscape else ''
    texture = textured() if texture is None else texture
    kit = _HERE.joinpath('cinematic_texture.css').read_text(encoding='utf-8') if texture else ''
    style = '<style id="cinematic-2d">' + _base_css() + theme + geometry + kit + '</style>'
    if texture:  # the hx-* filters the kit's classes point at
        defs = _HERE.joinpath('cinematic_texture_defs.svg').read_text(encoding='utf-8')
        body = re.search(r'<body[^>]*>', html, flags=re.I)
        html = html[:body.end()] + defs + html[body.end():] if body else html
    head = re.search(r'<head\b[^>]*>', html, flags=re.I)
    if head:
        return html[:head.end()] + style + html[head.end():]
    root = re.search(r'<html\b[^>]*>', html, flags=re.I)
    if root:
        return html[:root.end()] + '<head>' + style + '</head>' + html[root.end():]
    return '<!doctype html><html><head>' + style + '</head>' + re.sub(r'<!doctype[^>]*>', '', html, flags=re.I) + '</html>'


def _write_effects(path: Path, duration: float, boundaries: list[float]) -> None:
    """Quiet, deterministic paper-like transition ticks; never alter narration."""
    rate = 24000
    samples = [0.0] * max(1, math.ceil(duration * rate))
    for boundary in boundaries:
        start = max(0, round(boundary * rate))
        for i in range(min(round(.16 * rate), len(samples) - start)):
            t = i / rate
            envelope = math.sin(math.pi * t / .16) ** 2 * math.exp(-25 * t)
            samples[start + i] += .025 * envelope * (math.sin(2 * math.pi * 730 * t) + .4 * math.sin(2 * math.pi * 1237 * t))
    with wave.open(str(path), 'wb') as output:
        output.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        output.writeframes(b''.join(struct.pack('<h', round(max(-1, min(1, value)) * 32767)) for value in samples))


def render_motion_video(cfg: Config, st: dict, stage: Path, words, duration: float) -> Path:
    from .claude_motion import render_motion_video as render
    scoped = deepcopy(cfg.raw)
    motion = scoped.setdefault('video', {}).setdefault('claude_motion', {})
    motion['style'] = 'cinematic_2d'
    motion.setdefault('max_tokens', 30000)
    result = render(Config(scoped), st, stage, words, duration)
    boundaries = [float(scene['start']) for scene in st.get('storyboard', {}).get('scenes', [])[1:]]
    effects = stage / 'cinematic_fx.wav'
    _write_effects(effects, duration + .5, boundaries or list(range(2, math.ceil(duration), 2)))
    st['cinematic_effects'] = effects.name
    return result
