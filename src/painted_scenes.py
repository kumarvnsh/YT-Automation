"""Painted backdrops for Histold scenes made on the laptop.

While Claude writes each scene's animation, Codex paints one full-screen illustration per scene on the
owner's plan. The painting sits behind the scene with a slow camera push; Claude's labels, diagrams,
maps and portraits stay on top. Only the laptop runner turns this on (HISTOLD_PAINTED=1, set from
local_production.painted_scenes); cloud runs never paint. A painting that fails leaves that scene
exactly as it renders today.
"""
from __future__ import annotations

import base64
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, wait
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

STYLE = ('Rich painterly illustration in the manner of an editorial gouache or a museum mural: period-accurate '
         'architecture, clothing, tools and objects, cinematic lighting, atmospheric depth. Palette of deep teal, '
         'navy, warm cream, terracotta and muted brass.')


def enabled() -> bool:
    return os.environ.get('HISTOLD_PAINTED') == '1'


def _prompt(title: str, narration: str, story: str, landscape: bool) -> str:
    return (f'Paint one {"landscape 3:2" if landscape else "vertical 2:3"} illustration for a history documentary '
            f'video titled "{title}".\nThis shot plays while the narrator says: "{narration}"\n'
            f'Whole story, for context only: "{story[:900]}"\n\n{STYLE}\n'
            'Show the place, event, object or scene the narration describes, as it looked at the time. '
            'Main subject in the middle third; keep the top sixth and the bottom quarter calmer and darker, '
            'because a headline and captions are laid over them. '
            'No text, letters, numbers, signs, captions, logos, watermarks, borders or frames. '
            'Do not paint a recognisable face of a real named person: show people at a distance, from behind '
            'or in silhouette (real portraits are added separately). No gore.')


def _fit(source: Path, dest: Path, w: int, h: int) -> Path:
    """Cover-crop to the output frame and store as a compact JPEG for embedding."""
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', str(source), '-vf',
                    f'scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}', '-q:v', '3', str(dest)],
                   check=True, timeout=120, capture_output=True)
    return dest


class Painter:
    """Paints in the background; collect() waits a bounded time and returns {scene index: jpg}."""

    def __init__(self, cfg, title: str, narrations: list[str], folder: Path, w: int, h: int):
        settings = cfg.get('local_production.painted_scenes', {}) or {}
        self.cfg, self.folder, self.w, self.h = cfg, Path(folder), w, h
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
        self.futures = {paint: self.pool.submit(self._paint, paint, _prompt(title, ' '.join(lines), story, w > h))
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
            plan_ai.codex_image(self.cfg, prompt, raw, timeout=self.timeout)
            return _fit(raw, final, self.w, self.h)
        except plan_ai.PlanUnavailable as exc:
            self.stopped = True  # the plan is resting: do not queue more requests
            print(f'  ! painted backdrops stopped: {exc}')
        except Exception as exc:  # noqa: BLE001 - this scene keeps its normal look
            print(f'  ! painting {index} failed: {str(exc)[:160]}')
        finally:
            raw.unlink(missing_ok=True)
        return None

    def collect(self, grace: float = 240) -> dict[int, Path]:
        wait(self.futures.values(), timeout=grace)
        done = {paint: f.result() for paint, f in self.futures.items() if f.done() and not f.exception()}
        ready = {scene: done[paint] for scene, paint in enumerate(self.source) if done.get(paint)}
        print(f'  ✓ painted backdrops ready for {len(ready)}/{len(self.source)} scenes')
        return ready


def apply(html: str, image: Path, duration: float, index: int = 0) -> str:
    """Lay the painting behind the scene. Its camera move is a kit-fx- animation, so the motion
    checks ignore it and judge only Claude's own layers."""
    data = base64.b64encode(Path(image).read_bytes()).decode('ascii')
    drift = ('-2%,-1%', '2%,-1.5%', '-1.5%,1%', '1.5%,1.5%')[index % 4]
    style = ('<style id="histold-paint">html{background:#000!important}'
             'body,.k-stage{background:transparent!important;background-color:transparent!important}'
             '.k-stage{text-shadow:0 2px 12px rgba(0,0,0,.6)}'
             '.hp-paint{position:fixed;inset:-5%;z-index:-2;pointer-events:none;'
             f'background:url(data:image/jpeg;base64,{data}) center/cover no-repeat;'
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
