"""Data-driven topic strategist: scores recent editorial Shorts across platforms and
turns the scores into sampling weights for the next topic assignments.

A video's score is the weighted mean percentile rank of the metrics it has: YouTube
% viewed (counted twice: it is the cleanest content signal), Facebook views (counted
twice: Facebook is the only monetized platform, and its API exposes no per-Reel watch
time or shares), YouTube like-rate, Instagram like-rate and log views summed over all
platforms.

Per option (a series, region or length) the mean score is shrunk toward 0.5 by
SHRINK pseudo-videos so two lucky posts cannot take over, then weights mix 30%
uniform back in so every option keeps getting tried.
"""
from __future__ import annotations

import json
import math
import random
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

SHRINK = 3          # pseudo-videos at score 0.5 per option
EXPLORE = 0.3       # share of each draw left uniform
MIN_AGE_HOURS = 48  # younger videos are still accumulating
WINDOW_DAYS = 28
DIMENSIONS = {'series': 'category', 'region': 'editorial_region', 'duration_variant': 'duration_variant'}


def peaks(root: Path, since: str) -> dict:
    """Highest-view snapshot per video from every committed analytics export since `since`.

    Exports only keep the latest ~50 videos, so history is the only full record.
    """
    best = {'yt': {}, 'ig': {}, 'fb': {}}

    def snapshots(path):
        commits = subprocess.run(['git', 'log', '--format=%h', f'--since={since}', '--', path],
                                 cwd=root, capture_output=True, text=True).stdout.split()
        for commit in reversed(commits):
            try:
                yield json.loads(subprocess.run(['git', 'show', f'{commit}:{path}'], cwd=root,
                                                capture_output=True, text=True).stdout)
            except ValueError:
                continue
        if (root / path).exists():
            yield json.loads((root / path).read_text())

    def keep(platform, video):
        row = best[platform].setdefault(str(video['id']), {})
        if (video.get('views') or 0) >= row.get('views', -1):
            row.update({k: video.get(k) for k in ('views', 'likes', 'avg_view_pct', 'skip_rate',
                                                  'average_watch_time_seconds')})

    for data in snapshots('data/analytics.json'):
        for video in data.get('channels', {}).get('histold', {}).get('videos', []):
            keep('yt', video)
    for data in snapshots('data/meta_analytics.json'):
        for platform, key in (('instagram', 'ig'), ('facebook', 'fb')):
            for video in data.get(platform, {}).get('videos', []):
                keep(key, video)
    return best


def rows(index: list[dict], best: dict, now: datetime) -> list[dict]:
    """Joined per-video metrics for editorial Shorts old enough to judge."""
    out = []
    for entry in index:
        if entry.get('channel', 'histold') != 'histold' or entry.get('editorial_slot') is None:
            continue
        age = (now - datetime.fromisoformat(entry['published_at'].replace('Z', '+00:00'))).total_seconds() / 3600
        if not MIN_AGE_HOURS <= age <= WINDOW_DAYS * 24:
            continue
        yt = best['yt'].get(entry['video_id'], {})
        ig = best['ig'].get(str(entry.get('instagram_id')), {})
        fb = best['fb'].get(str(entry.get('facebook_id')), {})
        views = [p.get('views') for p in (yt, ig, fb) if p.get('views') is not None]
        out.append({
            'title': entry.get('title', ''),
            **{dim: entry.get(field) for dim, field in DIMENSIONS.items()},
            'yt_pct': yt.get('avg_view_pct'),
            'yt_like_rate': yt['likes'] / yt['views'] if yt.get('views') else None,
            'ig_like_rate': ig['likes'] / ig['views'] if ig.get('views') else None,
            'fb_views': math.log1p(fb['views']) if fb.get('views') is not None else None,
            'log_views': math.log1p(sum(views)) if views else None,
        })
    return out


METRIC_WEIGHTS = {'yt_pct': 2, 'fb_views': 2, 'yt_like_rate': 1, 'ig_like_rate': 1, 'log_views': 1}


def score(videos: list[dict]) -> None:
    """Add 'score' in [0, 1]: weighted mean percentile rank of each video's available metrics."""
    ranks = {}
    for metric in METRIC_WEIGHTS:
        values = sorted(v[metric] for v in videos if v[metric] is not None)
        if len(values) > 1:
            ranks[metric] = lambda x, vs=values: (sum(y < x for y in vs) + sum(y == x for y in vs) / 2) / len(vs)
    for video in videos:
        parts = [(METRIC_WEIGHTS[m], rank(video[m])) for m, rank in ranks.items() if video[m] is not None]
        total = sum(w for w, _ in parts)
        video['score'] = sum(w * r for w, r in parts) / total if total else 0.5


def weights(videos: list[dict], dim: str, options: list[str]) -> dict:
    """Sampling weight per option: shrunk mean score, squared for contrast, then mixed with uniform."""
    groups = defaultdict(list)
    for video in videos:
        groups[video[dim]].append(video['score'])
    shrunk = {o: (sum(groups[o]) + SHRINK * 0.5) / (len(groups[o]) + SHRINK) for o in options}
    raw = {o: s ** 2 for o, s in shrunk.items()}
    total = sum(raw.values())
    return {o: round(EXPLORE / len(options) + (1 - EXPLORE) * raw[o] / total, 4) for o in options}


def build(cfg, index: list[dict], best: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    videos = rows(index, best, now)
    score(videos)
    options = {'series': [s['id'] for s in cfg.get('editorial.series') or []],
               'region': [r['id'] for r in cfg.get('editorial.regions') or []],
               'duration_variant': [v['id'] for v in cfg.get('editorial.duration_variants') or []]}
    ranked = sorted(videos, key=lambda v: v['score'], reverse=True)
    stats = {}
    for dim, opts in options.items():
        groups = defaultdict(list)
        for video in videos:
            groups[video[dim]].append(video['score'])
        stats[dim] = {o: {'n': len(groups[o]), 'mean_score': round(sum(groups[o]) / len(groups[o]), 3) if groups[o] else None}
                      for o in opts}
    return {
        'updated': now.isoformat(),
        'videos_scored': len(videos),
        'weights': {dim: weights(videos, dim, opts) for dim, opts in options.items() if opts},
        'stats': stats,
        'winners': [v['title'] for v in ranked[:5]],
        'losers': [v['title'] for v in ranked[-5:]] if len(ranked) >= 10 else [],
        'method': __doc__.strip().splitlines()[0],
    }


def pick(options: list[dict], weights: dict | None, seed: int) -> dict:
    """Deterministic weighted choice: the same ordinal always gets the same pick."""
    if not weights:
        return None
    w = [weights.get(o['id'], 0) for o in options]
    return random.Random(seed).choices(options, weights=w)[0] if sum(w) > 0 else None


def load(root: Path, max_age_days: int = 3) -> dict | None:
    """data/strategy.json if present and fresh; stale strategy falls back to plain rotation."""
    path = root / 'data/strategy.json'
    try:
        data = json.loads(path.read_text())
        age = datetime.now(timezone.utc) - datetime.fromisoformat(data['updated'])
    except (OSError, ValueError, KeyError):
        return None
    return data if age <= timedelta(days=max_age_days) and data.get('videos_scored', 0) >= 10 else None
