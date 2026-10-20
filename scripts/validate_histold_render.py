#!/usr/bin/env python3
"""Render three synthetic local fixtures without AI calls, uploads, or notifications."""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.config import Config
from src.claude_motion import bind_maps, inject_kit, inspect_scene, _render_scene
from src.map_assets import load_map
from src.captions import build_ass
from src.tts import WordTiming
from src.video_builder import finalize
from playwright.sync_api import sync_playwright


def main():
    root = Path(__file__).resolve().parent.parent / 'output/production-validation'
    root.mkdir(parents=True, exist_ok=True)
    cfg = Config({'production': {'enabled': True}, 'video': {
        'short': {'width': 1080, 'height': 1920, 'fps': 12},
        'branding': {'enabled': True}, 'background_music': {'enabled': False},
        'captions': {'enabled': True, 'font': 'Arial', 'fontsize_short': 60, 'margin_bottom': 220}}})
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 540, 'height': 960}, device_scale_factor=2)
        # The fixture uses local fallback fonts so this check is independent of font services.
        page.route('https://fonts.googleapis.com/**', lambda route: route.abort())
        for category, title, payoff in [('strange_geography', 'A BORDER DETOUR', 'ROAD THROUGH CANADA'),
                                         ('historical_disasters', 'ONE FAILED PART', 'A CHAIN REACTION'),
                                         ('unbelievable_true_history', 'CHECK THE RECORD', 'EVIDENCE FIRST')]:
            stage = root / category
            stage.mkdir(exist_ok=True)
            maps = '<histold-map id="canada"></histold-map><histold-map id="united_states"></histold-map><histold-map id="washington"></histold-map>' if category == 'strange_geography' else '<div class="k-huge k-count" style="--to:1846;--d:2s;--t:2s"></div>'
            html = '<html><head></head><body><div class="k-stage"><div class="k-title k-rise" style="font-size:42px">'+title+'</div>'+maps+'<div class="k-serif k-rise" style="font-size:22px;--d:2s">'+payoff+'</div></div></body></html>'
            if category == 'strange_geography':
                html = bind_maps(html, [load_map(name) for name in ('canada', 'united_states', 'washington')])
            html = inject_kit(html)
            (stage/'scene.html').write_text(html)
            report = inspect_scene(page, html, 5, 12, [{'time':2}], True)
            _render_scene(page, html, 5, 12, stage/'silent.mp4', 'ultrafast')
            subprocess.run(['ffmpeg','-y','-hide_banner','-loglevel','error','-f','lavfi','-i','sine=frequency=220:duration=5','-c:a','libmp3lame',str(stage/'voiceover.mp3')],check=True)
            words = [WordTiming(word, i*.7, i*.7+.6) for i,word in enumerate('Synthetic render fixture check only'.split())]
            build_ass(cfg,words,stage/'captions.ass','short')
            finalize(cfg,stage/'silent.mp4',stage/'voiceover.mp3',stage/'captions.ass',stage/'video.mp4',stage,root)
            page.evaluate('document.getAnimations().forEach(a=>a.currentTime=3000)')
            page.screenshot(path=str(stage/'preview.png'))
            subprocess.run(['ffmpeg','-y','-hide_banner','-loglevel','error','-ss','3','-i',str(stage/'video.mp4'),'-frames:v','1',str(stage/'composed-preview.png')],check=True)
            results.append({'category':category, **report})
        browser.close()
    (root/'report.json').write_text(json.dumps(results,indent=2))
    print(json.dumps(results,indent=2))
    return int(any(item['errors'] for item in results))


if __name__ == '__main__':
    raise SystemExit(main())
