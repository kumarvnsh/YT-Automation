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
    return prompt


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


def prepare_html(html: str, index: int, *, landscape=False, theme_offset=None) -> str:
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
    style = '<style id="cinematic-2d">' + _base_css() + theme + geometry + '</style>'
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
