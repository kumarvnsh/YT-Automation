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
    kit = scene_style()
    if kit:
        prompt += STYLE_NOTES[kit].replace('{example}', _HERE.joinpath(f'cinematic_{kit}_example.html').read_text(encoding='utf-8'))
    elif textured():
        prompt += TEXTURE_NOTE.replace('{example}', _HERE.joinpath('cinematic_texture_example.html').read_text(encoding='utf-8'))
    from .painted_scenes import enabled, prompt_note
    if enabled():
        prompt += prompt_note()
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


PAPERCUT_NOTE = """
CUT-PAPER STYLE (this run). The whole scene is built from flat pieces of cut paper, like a paper-craft diorama
photographed from the front: matte colours, visible fibre grain, slightly rough cut edges, soft drop shadows that
lift each layer off the one behind it, light from the top-left. The renderer adds grain and a vignette over every
frame and defines these (do not define them yourself):
  SVG: pc-cut      rough cut edge + fibre grain + drop shadow, for each paper layer or object (<g> or shape)
       pc-cut-lo   same, flatter shadow, for back layers and small pieces
  HTML: pc-slab    a cut card (set background-color with a palette variable); pc-banner taped headline banner
       pc-tape     masking-tape strip (absolute; set --r rotation) to pin banners and cards
       pc-tag      torn coloured label for a date or fact (set --r, -4deg to 4deg)
       pc-note     ruled note card; pc-stamp  rubber-stamp slam for a date or verdict (set --d)
  Motion (set --d delay): pc-pop springy pop with overshoot (props, 0.3s); pc-slide overshooting slide-in
       (--fx/--fy start offset); pc-drop drop-in from above; pc-bob idle bob; pc-spin (--t period) for gears and
       wheels; pc-boil 15fps paper jitter for a banner; pc-push / pc-far slow camera push and depth drift (ambient).
Build every illustration as stacked paper layers back to front: a backdrop card, far layer, mid layer, the
subject, a foreground lip, then labels. Shapes are flat fills (palette variables or color-mix() of them), no
outlines and no gradients on objects; add depth with layers, darker strips on shaded faces and shadows.
Headline: one pc-banner, taped, stating the scene's hard fact in 3-8 words. Labels: pc-tag or pc-slab cards with a
short fact, never the narration. Cue one discrete paper event to each stressed word (a piece pops, slides, drops,
stamps or turns), roughly every 0.5-1s; things assemble piece by piece rather than fading in.
This is a complete scene in the target style; match its richness and paper construction, not its subject:
<example>
{example}
</example>
"""


FLATLIT_NOTE = """
FLAT-LIT STYLE (this run). Clean flat-vector illustration lit from one side, like a modern science explainer:
every object is a flat shape in a palette variable, with ONE light direction for the whole scene (upper left).
The far side of each round or boxy object gets a darker crescent/face (class fl-shade on an overlay path), the
edge facing the light a thin rim (fl-rim), and the hero object a soft coloured glow behind it (fl-halo on a
blurred circle). No outlines, no textures, no gradients as decoration. The renderer adds faint grain and a soft
central glow and defines these (do not define them yourself):
  SVG: fl-shade dark crescent overlay   fl-rim light edge   fl-halo soft glow   fl-lift drop shadow under a part
       fl-speck twinkling dust (set --d, --t)   fl-turn gear/wheel rotation (--t period; add ccw to reverse)
  HTML: fl-pill rounded label pill (add ghost for an outline pill)   fl-num numbered circle   fl-hero big payoff tag
  Motion (set --d delay): fl-drop overshoot drop-in; fl-pop pop from nothing; fl-rise rise-in for callouts;
       fl-line leader line draws (pathLength="1"); fl-float gentle float; fl-push slow camera push (ambient).
  BREAKDOWN: to explain a machine, building or object, show it assembled, then lift its parts apart: wrap each
       part in <g class="fl-layer" style="--dy:-60px;--d:1.6s"> (negative --dy lifts it up, positive drops it),
       then draw a leader line from each part to a numbered callout (fl-num + short name) at the side.
Elements arrive one at a time with overshoot, on the words that name them. Labels are pills or numbered callouts
with 1-4 words, never the narration. Keep a real change at least every 1.5 seconds.
This is a complete scene in the target style; match its construction and lighting, not its subject:
<example>
{example}
</example>
"""

STYLE_NOTES = {'papercut': PAPERCUT_NOTE, 'flatlit': FLATLIT_NOTE}


def scene_style() -> str:
    """Opt-in scene kit chosen with HISTOLD_STYLE: 'papercut' (cut-paper diorama, adapted from paper-reel and
    papercut-explainer, MIT) or 'flatlit' (flat-lit vector + exploded breakdowns, adapted from
    claude-animation-skills, MIT). Empty when unset or unknown."""
    import os
    name = os.environ.get('HISTOLD_STYLE', '').strip().lower()
    return name if name in STYLE_NOTES else ''


def papercut() -> bool:
    return scene_style() == 'papercut'


# Machines, structures and how-it-worked stories suit the flat-lit exploded breakdown; events, places,
# people and trade suit cut-paper dioramas. Each style is the other's fallback (owner, 24 Nov).
_MECHANISM_WORDS = ('machine', 'gear', 'engine', 'pump', 'mechanism', 'device', 'clock', 'invent', 'engineer',
                    'bridge', 'canal', 'aqueduct', 'dam', 'lock', 'wheel', 'lever', 'calculat', 'computer',
                    'telescope', 'printing', 'press', 'valve', 'steam', 'tool', 'pulley', 'crank', 'shaft',
                    'circuit', 'telegraph', 'loom', 'mill', 'furnace', 'cannon', 'ship', 'boat', 'instrument')
STYLE_FALLBACK = {'papercut': 'flatlit', 'flatlit': 'papercut'}


def choose_style(script: dict) -> str:
    """Pick the scene kit for one video from its script."""
    text = ' '.join([str(script.get('title', ''))] +
                    [str(seg.get('narration', '')) for seg in script.get('segments', []) if isinstance(seg, dict)]).lower()
    hits = sum(text.count(word) for word in _MECHANISM_WORDS)
    return 'flatlit' if hits >= 3 else 'papercut'


def resolve_style(script: dict) -> str:
    """HISTOLD_STYLE=auto picks per video; an explicit kit name is used as given; otherwise ''."""
    import os
    name = os.environ.get('HISTOLD_STYLE', '').strip().lower()
    if name == 'auto':
        return choose_style(script)
    return name if name in STYLE_NOTES else ''


def style_chain(primary: str) -> list[str]:
    """Writers to try in order: the chosen kit, the other kit, then the current textured look."""
    return [primary, STYLE_FALLBACK[primary], 'textured'] if primary in STYLE_FALLBACK else []


class writing_style:
    """Temporarily point the scene writer (system_prompt) and prepare_html at one kit; 'textured' = the
    pre-existing textured look."""

    def __init__(self, name: str):
        self.name = name

    def __enter__(self):
        import os
        self.saved = {k: os.environ.get(k) for k in ('HISTOLD_STYLE', 'HISTOLD_TEXTURE')}
        if self.name == 'textured':
            os.environ['HISTOLD_STYLE'] = ''
            os.environ['HISTOLD_TEXTURE'] = '1'
        else:
            os.environ['HISTOLD_STYLE'] = self.name
        return self

    def __exit__(self, *exc):
        import os
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return False


_STYLE_TAG = re.compile(r'<meta name="histold-style" content="([a-z]*)">')


def tag_style(html: str, name: str) -> str:
    """Record which kit a scene was written for, so it is prepared with that kit."""
    html = _STYLE_TAG.sub('', html)
    tag = f'<meta name="histold-style" content="{name}">'
    for pattern in (r'<head[^>]*>', r'<html[^>]*>', r'<!doctype[^>]*>'):
        found = re.search(pattern, html, flags=re.I)
        if found:
            return html[:found.end()] + tag + html[found.end():]
    return tag + html


def tagged_style(html: str) -> str | None:
    found = _STYLE_TAG.search(html)
    return found.group(1) if found else None


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
    tagged = tagged_style(html)  # a scene written in a fallback kit keeps that kit
    special = '' if tagged == 'textured' else (tagged if tagged in STYLE_NOTES else scene_style())
    if tagged == 'textured':
        texture = True
    texture = (textured() if texture is None else texture) or bool(special)
    kit_name = f'cinematic_{special}' if special else 'cinematic_texture'
    kit = _HERE.joinpath(kit_name + '.css').read_text(encoding='utf-8') if texture else ''
    style = '<style id="cinematic-2d">' + _base_css() + theme + geometry + kit + '</style>'
    if texture:  # the hx-*/pc-* filters the kit's classes point at
        defs = _HERE.joinpath(kit_name + '_defs.svg').read_text(encoding='utf-8')
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
