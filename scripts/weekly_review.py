#!/usr/bin/env python3
"""Weekly "what's working" review of recent Histold Shorts, written by a local LLM (Ollama).

Reuses the strategist's data and scoring (src/strategy.py), so every number in the report
comes from code; the model only interprets them. Writes data/reviews/<ISO week>.md.
Advisory only: nothing here changes strategy.json, topics or publishing.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.config import load_config  # noqa: E402
from src.strategy import WINDOW_DAYS, build, peaks, rows, score  # noqa: E402
from src.ollama_policy import bounded_request,server_environment  # noqa: E402

MIN_VIDEOS = 10
EACH_END = 25        # rows sent to the model: best 25 + worst 25 (stats still cover every video)
NUM_CTX = 8192       # fits 50 rows + instructions + answer and keeps an 8B model fully in 8 GB VRAM
NUM_PREDICT = 2000   # hard stop: a full answer is ~800 tokens; small models can loop on JSON lists


def ollama(host: str, path: str, payload: dict | None = None, timeout: int = 10) -> dict:
    if payload is not None and path in ('/api/chat','/api/generate'):
        payload=bounded_request(payload)
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(host + path, data=data, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def ensure_server(host: str) -> None:
    """Start `ollama serve` in the background if nothing answers (e.g. while logged off)."""
    try:
        ollama(host, '/api/version')
        return
    except (urllib.error.URLError, OSError):
        pass
    exe = os.environ.get('OLLAMA_EXE') or shutil.which('ollama')
    if not exe:
        raise SystemExit('ollama server not running and no ollama executable found (set OLLAMA_EXE)')
    flags = 0
    if os.name == 'nt':
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    subprocess.Popen([exe, 'serve'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True,env=server_environment(os.environ))
    for _ in range(60):
        time.sleep(1)
        try:
            ollama(host, '/api/version')
            return
        except (urllib.error.URLError, OSError):
            continue
    raise SystemExit(f'ollama serve did not come up at {host}')


def pct(x: float | None, digits: int = 0) -> str:
    """A 0-1 rate as a percentage."""
    return '-' if x is None else f'{x * 100:.{digits}f}%'


def viewed(x: float | None) -> str:
    """YouTube avg_view_pct is already a percentage (and exceeds 100 when Shorts loop)."""
    return '-' if x is None else f'{x:.0f}%'


def collect(cfg, now: datetime) -> tuple[list[dict], dict]:
    since = (now - timedelta(days=WINDOW_DAYS + 3)).date().isoformat()
    index = json.loads((ROOT / 'data/published_index.json').read_text())
    best = peaks(ROOT, since)
    videos = rows(index, best, now)
    videos = [v for v in videos if v['yt_like_rate'] is not None]
    score(videos, 'youtube', like_rate_only=True)
    unique = {}
    for v in videos:  # republished titles appear twice in the index; keep the better copy
        if v['title'] not in unique or v['score'] > unique[v['title']]['score']:
            unique[v['title']] = v
    videos = list(unique.values())
    published = {e.get('title', ''): e.get('published_at', '') for e in index}
    week_ago = now - timedelta(days=7)
    for v in videos:
        at = published.get(v['title'], '')
        v['published_at'] = at[:10]
        v['new'] = bool(at) and datetime.fromisoformat(at.replace('Z', '+00:00')) >= week_ago
        v['views'] = round(math.expm1(v['log_views'])) if v['log_views'] is not None else None
    videos.sort(key=lambda v: v['score'], reverse=True)
    return videos, build(cfg, index, best, now)


def prompt_for(cfg, videos: list[dict], strategy: dict) -> str:
    series = '\n'.join(f"- {s['id']}: {s['name']}" for s in cfg.get('editorial.series') or [])
    regions = '\n'.join(f"- {r['id']}: {r['label']}" for r in cfg.get('editorial.regions') or [])
    if len(videos) > 2 * EACH_END:
        videos = videos[:EACH_END] + videos[-EACH_END:]
    table = '\n'.join(
        f"{v['score']:.2f} | {v['series']} | {v['region']} | {v['duration_variant']} | {v.get('fb_views', '-')} | "
        f"{viewed(v['yt_pct'])} | {pct(v['yt_like_rate'], 1)} | {pct(v['ig_like_rate'], 1)} | "
        f"{v['views'] if v['views'] is not None else '-'} | {'new' if v['new'] else ''} | {v['title']}"
        for v in videos)
    stats = json.dumps(strategy['stats'], separators=(',', ':'))
    return f"""You analyse performance data for Histold, a history Shorts channel on Facebook, YouTube and Instagram. Facebook is the priority: it is the only monetized platform. The target audience is in the USA and Europe.

Score is the percentile rank of YouTube likes per view. Evaluate changes by likes per view on each platform; reach and retention are context. "new" marks videos published in the last 7 days.

Series:
{series}

Regions:
{regions}

Per-option stats (n videos, mean score): {stats}

Videos ({'best and worst ' + str(EACH_END) + ' of ' + str(strategy['videos_scored']) if strategy['videos_scored'] > 2 * EACH_END else 'all'}), best first (score | series | region | length | FB views | YT % viewed | YT like rate | IG like rate | total views | new | title):
{table}

Write this week's review. Rules:
- Base every finding on the data above; cite real titles from the table, copied exactly, as examples.
- Treat options with fewer than 6 videos as too little data, not as winners or losers.
- A missing metric is unavailable, not zero or negative. Never infer failure from missing data. A zero-view post needs a distribution/reporting check before judging its topic.
- These snapshots are not matched for video age, so differences do not establish causation. Do not infer earnings or viewer locations.
- Look at what the titles themselves do (question form, specificity, named places or people, surprise) not only at series and region.
- Topic ideas must be NEW: never repeat or rephrase a title from the table. They must be real, documented history that fits a listed series and region and would interest a US or European viewer; do not invent events. They will be fact-checked before use.
- Be concise and specific."""


def schema(cfg) -> dict:
    """Bounded JSON schema: Ollama turns it into a grammar, so list sizes are hard limits."""
    def text(n):
        return {'type': 'string', 'maxLength': n}

    def items(of, lo, hi):
        return {'type': 'array', 'items': of, 'minItems': lo, 'maxItems': hi}

    finding = {'type': 'object', 'properties': {'finding': text(300), 'examples': items(text(140), 1, 3)},
               'required': ['finding', 'examples']}
    idea = {'type': 'object', 'properties': {
        'title': text(120),
        'series': {'type': 'string', 'enum': [s['id'] for s in cfg.get('editorial.series') or []]},
        'region': {'type': 'string', 'enum': [r['id'] for r in cfg.get('editorial.regions') or []]},
        'why': text(250)}, 'required': ['title', 'series', 'region', 'why']}
    return {'type': 'object', 'properties': {
        'headline': text(200),
        'working': items(finding, 1, 3),
        'not_working': items(finding, 1, 3),
        'title_patterns': items(text(200), 1, 4),
        'topic_ideas': items(idea, 3, 5),
        'experiment': text(400)},
        'required': ['headline', 'working', 'not_working', 'title_patterns', 'topic_ideas', 'experiment']}


def render(review: dict, videos: list[dict], strategy: dict, model: str, now: datetime, week: str,
           seconds: float) -> str:
    titles = {v['title'].casefold() for v in videos}

    def example(t: str) -> str:
        return f'"{t}"' if t.casefold() in titles else f'"{t}" *(not found in data)*'

    def findings(items: list[dict]) -> list[str]:
        out = []
        for f in items:
            out.append(f"- {f['finding']}")
            out += [f'  - {example(t)}' for t in f.get('examples', [])]
        return out or ['- (none)']

    lines = [f'# Histold weekly review {week}', '',
             f"Generated {now.strftime('%Y-%m-%d %H:%M UTC')} by `{model}` (local, {seconds:.0f}s) from "
             f"{len(videos)} Shorts published in the last {WINDOW_DAYS} days "
             f"({sum(v['new'] for v in videos)} new this week). Advisory only: it does not change "
             f'`strategy.json` or publishing.', '',
             f"**{review['headline']}**", '',
             "## What's working", *findings(review['working']), '',
             "## What's not working", *findings(review['not_working']), '',
             '## Title patterns', *([f'- {p}' for p in review['title_patterns']] or ['- (none)']), '',
             '## Topic ideas (unverified: fact-check before use)']
    fresh = [i for i in review['topic_ideas'] if i['title'].casefold() not in titles]
    for idea in fresh:
        lines.append(f"- **{idea['title']}** ({idea['series']}, {idea['region']}): {idea['why']}")
    if len(fresh) < len(review['topic_ideas']):
        lines.append(f"- *({len(review['topic_ideas']) - len(fresh)} idea(s) dropped: already published)*")
    lines += ['', '## Suggested experiment', review['experiment'] or '(none)', '',
              '## The numbers (computed, not model-written)', '',
              '| Dimension | Option | Videos | Mean score | Next-week weight |', '|---|---|---|---|---|']
    for dim, opts in strategy['stats'].items():
        for opt, s in sorted(opts.items(), key=lambda kv: -(kv[1]['mean_score'] or 0)):
            if s['n']:
                lines.append(f"| {dim} | {opt} | {s['n']} | {s['mean_score']:.2f} | "
                             f"{strategy['weights'].get(dim, {}).get(opt, 0):.0%} |")
    lines += ['', '| | Score | Series | Region | YT % viewed | Views | Title |', '|---|---|---|---|---|---|---|']
    ranked = videos[:5] + ([None] if len(videos) > 10 else []) + (videos[-5:] if len(videos) > 10 else [])
    for v in ranked:
        if v is None:
            lines.append('| ... | | | | | | |')
            continue
        lines.append(f"| {'top' if v in videos[:5] else 'bottom'} | {v['score']:.2f} | {v['series']} | {v['region']} | "
                     f"{viewed(v['yt_pct'])} | {v['views'] if v['views'] is not None else '-'} | {v['title']} |")
    return '\n'.join(lines) + '\n'


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--model', default=os.environ.get('REVIEW_MODEL', 'qwen3:8b'))
    ap.add_argument('--host', default=os.environ.get('OLLAMA_HOST_URL', 'http://127.0.0.1:11434'))
    ap.add_argument('--out', help='output path (default data/reviews/<ISO week>.md)')
    ap.add_argument('--stdout', action='store_true', help='print the report instead of writing it')
    ap.add_argument('--if-missing', action='store_true', help="skip if this week's report already exists")
    args = ap.parse_args()

    cfg = load_config()
    now = datetime.now(timezone.utc)
    year, wk, _ = now.isocalendar()
    week = f'{year}-W{wk:02d}'
    if args.if_missing and not args.out and (ROOT / 'data' / 'reviews' / f'{week}.md').exists():
        print(f'review: {week} already written, skipping')
        return 0
    videos, strategy = collect(cfg, now)
    if len(videos) < MIN_VIDEOS:
        print(f'review: only {len(videos)} videos scored, skipping')
        return 0

    ensure_server(args.host)
    started = time.monotonic()
    resp = ollama(args.host, '/api/generate', {
        'model': args.model, 'prompt': prompt_for(cfg, videos, strategy), 'stream': False, 'think': False,
        'format': schema(cfg), 'options': {'temperature': 0.3, 'num_ctx': NUM_CTX, 'num_predict': NUM_PREDICT,
                    'repeat_penalty': 1.1}}, timeout=900)
    if resp.get('done_reason') == 'length':
        raise SystemExit(f'review: model hit the {NUM_PREDICT}-token cap without finishing; not writing a report')
    seconds = time.monotonic() - started
    try:
        review = json.loads(resp['response'])
    except (KeyError, ValueError):
        raise SystemExit(f"review: model returned unparseable output: {resp.get('response', '')[:300]}")

    report = render(review, videos, strategy, args.model, now, week, seconds)
    if args.stdout:
        print(report)
    else:
        out = Path(args.out) if args.out else ROOT / 'data' / 'reviews' / f'{week}.md'
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding='utf-8')
        print(f'review: {len(videos)} videos, {seconds:.0f}s, wrote {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}')
    print(f"  prompt {resp.get('prompt_eval_count')} tokens, answer {resp.get('eval_count')} tokens")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
