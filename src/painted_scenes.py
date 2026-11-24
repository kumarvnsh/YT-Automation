"""Painted pictures for Histold scenes made on the laptop.

While Claude writes each scene's animation, Antigravity (Gemini) paints one illustration per scene on the
owner's plan, with Codex as backup. Two layouts (HISTOLD_PAINTED_MODE, from local_production.painted_scenes.mode):
- ``inset`` (default, the owner's choice on 24 Nov): the painting is a framed picture inside the scene, in a
  ``<div class="histold-painting">`` slot the scene writer places, on the normal paper background.
- ``backdrop``: the painting fills the screen behind the scene with a slow camera push.
Only the laptop runner turns this on (HISTOLD_PAINTED=1, set from local_production.painted_scenes); cloud
runs never paint. A painting that fails leaves that scene exactly as it renders today.
"""
from __future__ import annotations

import base64
import os
import subprocess
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

PROMPT_NOTE = """
PAINTED BACKDROP (this run): every scene has a full-bleed painted illustration of the moment
behind it, added by the renderer. Do NOT paint your own background: leave html, body and .k-stage
backgrounds transparent, and ignore the rule that says to set them to var(--parch). The painting
is the main visual, so the 60% illustration rule does not apply. Keep the headline at the top and
put explanatory art (diagram, counter, timeline, comparison, histold-photo, histold-map) in one
compact band no taller than about 40% of the usable area, usually the lower half above the caption
band, on a translucent panel: background: color-mix(in srgb, var(--parch) 72%, transparent);
border-radius 14px. Use var(--ink) for text so it reads on that panel and on the painting's darkened
top. Keep a real visual change at least every two seconds in your own layers; the backdrop's
camera move does not count as motion.
"""

INSET_NOTE = """
PAINTED PICTURE (this run): every scene gets one painted illustration of the moment, shown as a framed
picture INSIDE your layout, never as the background. Keep your normal var(--parch) background. Place
exactly one empty <div class="histold-painting"></div> where the picture should go and leave it empty:
the renderer fills it with the painting, its frame and a slow camera push. Size it as the scene's main
illustration: in a vertical frame full content width and about 38-48% of the usable height; in a
landscape frame about half the width and most of the usable height, beside your text and diagrams.
Give it an explicit width and height (or aspect-ratio), keep it clear of the caption band, and bring it
in with an entrance like any other element. Labels, arrows, counters and portraits may sit next to it
or overlap its edge, but never cover its middle. It counts toward the illustration area; keep a real
visual change at least every two seconds in your own layers.
"""

STYLE = ('Rich painterly illustration in the manner of an editorial gouache or a museum mural: period-accurate '
         'architecture, clothing, tools and objects, cinematic lighting, atmospheric depth. Palette of deep teal, '
         'navy, warm cream, terracotta and muted brass.')


_OFF = []  # set for the rest of this process once Codex is found unavailable at the start of a video


def enabled() -> bool:
    return os.environ.get('HISTOLD_PAINTED') == '1' and not _OFF


def mode() -> str:
    """'inset' (framed picture in the scene) unless 'backdrop' is asked for."""
    return 'backdrop' if os.environ.get('HISTOLD_PAINTED_MODE', '').strip().lower() == 'backdrop' else 'inset'


def prompt_note() -> str:
    if mode() == 'backdrop':
        return PROMPT_NOTE + 'The painted-backdrop layout rules above win over the illustration size in the texture style.\n'
    return INSET_NOTE


def disable() -> None:
    _OFF.append(True)


def _prompt(title: str, narration: str, story: str, landscape: bool, inset: bool = False) -> str:
    framing = ('It is shown as a framed picture inside the scene, so compose it as a complete picture with the '
               'main subject clearly in the centre. ' if inset else
               'Main subject in the middle third; keep the top sixth and the bottom quarter calmer and darker, '
               'because a headline and captions are laid over them. ')
    return (f'Paint one {"landscape 3:2" if landscape or inset else "vertical 2:3"} illustration for a history documentary '
            f'video titled "{title}".\nThis shot plays while the narrator says: "{narration}"\n'
            f'Whole story, for context only: "{story[:900]}"\n\n{STYLE}\n'
            'Show the place, event, object or scene the narration describes, as it looked at the time. '
            + framing +
            'No text, letters, numbers, signs, captions, logos, watermarks, borders or frames. '
            'Do not paint a recognisable face of a real named person: show people at a distance, from behind '
            'or in silhouette (real portraits are added separately). No gore.')


def _fit(source: Path, dest: Path, w: int, h: int) -> Path:
    """Store as a compact JPEG for embedding: cover-cropped to the frame for a backdrop, or kept whole
    (at most the frame's longer side) for a framed picture, which the slot crops itself."""
    if mode() == 'backdrop':
        scale = f'scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}'
    else:
        side = max(w, h)
        scale = f'scale={side}:{side}:force_original_aspect_ratio=decrease'
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', str(source), '-vf', scale, '-q:v', '3', str(dest)],
                   check=True, timeout=120, capture_output=True)
    return dest


class Painter:
    """Paints in the background; collect() waits a bounded time and returns {scene index: jpg}."""

    def __init__(self, cfg, title: str, narrations: list[str], folder: Path, w: int, h: int):
        settings = cfg.get('local_production.painted_scenes', {}) or {}
        self.cfg, self.folder, self.w, self.h = cfg, Path(folder), w, h
        from . import plan_ai
        if not plan_ai.image_ready(cfg):  # Antigravity closed / out of quota and Codex capped: normal look
            raise plan_ai.PlanUnavailable('no image provider is ready (Antigravity or Codex)')
        self.limit = int(settings.get('max_images', 10)) if isinstance(settings, dict) else 10
        self.timeout = int(settings.get('timeout_seconds', 360)) if isinstance(settings, dict) else 360
        count = len(narrations)
        paints = min(count, max(1, self.limit))
        # More scenes than paintings (long videos): neighbouring scenes share one painting.
        self.source = [i * paints // count for i in range(count)]
        groups: dict[int, list[str]] = {}
        for scene, paint in enumerate(self.source):
            groups.setdefault(paint, []).append(narrations[scene])
        story = ' '.join(narrations)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.stopped = False
        self.pool = ThreadPoolExecutor(max_workers=int(settings.get('parallel', 3)) if isinstance(settings, dict) else 3)
        inset = mode() == 'inset'
        self.futures = {paint: self.pool.submit(self._paint, paint, _prompt(title, ' '.join(lines), story, w > h, inset))
                        for paint, lines in groups.items()}
        self.pool.shutdown(wait=False)

    def _paint(self, index: int, prompt: str) -> Path | None:
        final = self.folder / f'paint_{index:02d}.jpg'
        if final.is_file():  # a rerun of the same stage reuses its paintings
            return final
        if self.stopped:
            return None
        from . import plan_ai
        raw = self.folder / f'paint_{index:02d}.png'
        try:
            plan_ai.image(self.cfg, prompt, raw, timeout=self.timeout)
            return _fit(raw, final, self.w, self.h)
        except plan_ai.PlanUnavailable as exc:
            self.stopped = True  # the plan is resting: do not queue more requests
            print(f'  ! painted backdrops stopped: {exc}')
        except Exception as exc:  # noqa: BLE001 - this scene keeps its normal look
            print(f'  ! painting {index} failed: {str(exc)[:160]}')
        finally:
            raw.unlink(missing_ok=True)
        return None

    def settle(self, seconds: float = 20) -> bool:
        """Wait briefly for the providers to answer. A plan that refuses at once (quota used up) stops
        the painter here, before the scenes are written, so the writer is not told to leave a picture slot.
        Returns True while painting is still on."""
        wait(self.futures.values(), timeout=seconds, return_when=FIRST_COMPLETED)
        return not self.stopped

    def collect(self, grace: float = 240) -> dict[int, Path]:
        wait(self.futures.values(), timeout=grace)
        self.stopped = True  # paintings still queued are not started; the video goes on without them
        for future in self.futures.values():
            future.cancel()
        done = {paint: f.result() for paint, f in self.futures.items()
                if f.done() and not f.cancelled() and not f.exception()}
        ready = {scene: done[paint] for scene, paint in enumerate(self.source) if done.get(paint)}
        print(f'  ✓ painted backdrops ready for {len(ready)}/{len(self.source)} scenes')
        return ready


def apply_inset(html: str, image: Path | None, duration: float, index: int = 0) -> str:
    """Fill the scene's ``.histold-painting`` slot with the painting, a frame and a slow push.

    The scene keeps its own paper background. With no painting (it failed after the writer was told to
    expect one) the slot is hidden, so the video never shows an empty framed box (owner, 24 Nov). A scene
    that left out the slot is returned unchanged."""
    import re
    if 'histold-painting' not in html:
        return html
    if not image:
        style = '<style id="histold-paint">.histold-painting{display:none!important}</style>'
        head = re.search(r'</head\s*>', html, flags=re.I)
        return html[:head.start()] + style + html[head.start():] if head else style + html
    picture = f"url(data:image/jpeg;base64,{base64.b64encode(Path(image).read_bytes()).decode('ascii')}) center/cover no-repeat"
    drift = ('-2%,-1%', '2%,-1.5%', '-1.5%,1%', '1.5%,1.5%')[index % 4]
    style = ('<style id="histold-paint">'
             '.histold-painting{position:relative;overflow:hidden;border-radius:14px;'
             'border:5px solid color-mix(in srgb,var(--ink) 82%,var(--parch));'
             'box-shadow:0 14px 34px rgba(0,0,0,.28),inset 0 0 0 2px color-mix(in srgb,var(--parch) 60%,transparent);'
             'background:color-mix(in srgb,var(--parch) 88%,var(--ink))}'
             '.histold-painting:empty{min-height:120px}'
             '.histold-painting>.hp-pic{position:absolute;inset:-4%;pointer-events:none;'
             f'background:{picture};'
             f'animation:kit-fx-paint {max(duration, .5):.2f}s linear both}}'
             f'@keyframes kit-fx-paint{{from{{transform:scale(1) translate(0,0)}}to{{transform:scale(1.08) translate({drift})}}}}'
             '</style>')
    html = re.sub(r'(<div\b[^>]*\bclass="[^"]*\bhistold-painting\b[^"]*"[^>]*>)',
                  lambda m: m.group(1) + '<div class="hp-pic" aria-hidden="true"></div>', html, count=1)
    head = re.search(r'</head\s*>', html, flags=re.I)
    return html[:head.start()] + style + html[head.start():] if head else style + html


def apply(html: str, image: Path | None, duration: float, index: int = 0) -> str:
    """Lay the painting behind the scene. Its camera move is a kit-fx- animation, so the motion
    checks ignore it and judge only Claude's own layers.

    With no image (its painting failed after Claude was told to expect one) a dark palette backdrop
    takes its place, so text written for a painting never lands on a bare white page."""
    if image:
        picture = f"url(data:image/jpeg;base64,{base64.b64encode(Path(image).read_bytes()).decode('ascii')}) center/cover no-repeat"
    else:
        picture = ('radial-gradient(120% 75% at 50% 38%,color-mix(in srgb,var(--parch) 82%,var(--accent) 18%) 0%,'
                   'var(--parch) 62%,color-mix(in srgb,var(--parch) 70%,#000 30%) 100%)')
    drift = ('-2%,-1%', '2%,-1.5%', '-1.5%,1%', '1.5%,1.5%')[index % 4]
    style = ('<style id="histold-paint">html{background:#000!important}'
             'body,.k-stage{background:transparent!important;background-color:transparent!important}'
             '.k-stage{text-shadow:0 2px 12px rgba(0,0,0,.6)}'
             '.hp-paint{position:fixed;inset:-5%;z-index:-2;pointer-events:none;'
             f'background:{picture};'
             f'animation:kit-fx-paint {max(duration, .5):.2f}s linear both}}'
             '.hp-shade{position:fixed;inset:0;z-index:-1;pointer-events:none;background:'
             'linear-gradient(180deg,rgba(0,0,0,.7) 0%,rgba(0,0,0,.25) 28%,rgba(0,0,0,.08) 55%,rgba(0,0,0,.72) 100%)}'
             f'@keyframes kit-fx-paint{{from{{transform:scale(1) translate(0,0)}}to{{transform:scale(1.09) translate({drift})}}}}'
             '</style>')
    layers = '<div class="hp-paint" aria-hidden="true"></div><div class="hp-shade" aria-hidden="true"></div>'
    import re
    head = re.search(r'</head\s*>', html, flags=re.I)
    html = html[:head.start()] + style + html[head.start():] if head else style + html
    body = re.search(r'<body\b[^>]*>', html, flags=re.I)
    return html[:body.end()] + layers + html[body.end():] if body else html.replace('</html>', layers + '</html>')
