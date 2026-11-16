#!/usr/bin/env python3
"""Report the voice-title-2x2 A/B test: per cell and per factor, from peak metrics in git history.

Analytics exports keep only the latest ~50 videos, so peak values are read from every
committed snapshot of data/analytics.json and data/meta_analytics.json. Run locally
on a full clone. Videos younger than --min-hours are left out (counts still growing).
"""
from __future__ import annotations
import argparse
import json
import statistics
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def snapshots(path: str):
    commits = subprocess.run(['git', 'log', '--format=%h', '--since=2026-11-15', '--', path],
                             cwd=ROOT, capture_output=True, text=True).stdout.split()
    for commit in reversed(commits):
        try:
            yield json.loads(subprocess.run(['git', 'show', f'{commit}:{path}'], cwd=ROOT,
                                            capture_output=True, text=True).stdout)
        except ValueError:
            continue
    yield json.loads((ROOT / path).read_text())


def peaks() -> dict:
    best = {'yt': {}, 'ig': {}, 'fb': {}}

    def keep(platform, video):
        row = best[platform].setdefault(str(video['id']), {})
        if (video.get('views') or 0) >= row.get('views', -1):  # latest snapshot with the most views wins
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


def summarize(rows: list[dict]) -> str:
    def med(key):
        values = [r[key] for r in rows if r.get(key) is not None]
        return f'{statistics.median(values):6.1f}' if values else '     -'

    def per_k(platform):
        views = sum(r.get(f'{platform}_views') or 0 for r in rows)
        likes = sum(r.get(f'{platform}_likes') or 0 for r in rows)
        return f'{1000 * likes / views:5.1f}' if views else '    -'

    return (f'n={len(rows):2}  YT %viewed {med("yt_pct")}  YT likes/1k {per_k("yt")}  '
            f'YT views {med("yt_views")}  IG likes/1k {per_k("ig")}  IG views {med("ig_views")}  '
            f'IG skip {med("ig_skip")}  IG watch s {med("ig_watch")}')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--id', default='voice-title-2x2')
    parser.add_argument('--min-hours', type=float, default=48)
    args = parser.parse_args()
    best = peaks()
    now = datetime.now(timezone.utc)
    rows, young = [], 0
    for entry in json.loads((ROOT / 'data/published_index.json').read_text()):
        arm = entry.get('ab_test') or {}
        if arm.get('id') != args.id:
            continue
        if (now - datetime.fromisoformat(entry['published_at'])).total_seconds() < args.min_hours * 3600:
            young += 1
            continue
        yt = best['yt'].get(entry['video_id'], {})
        ig = best['ig'].get(str(entry.get('instagram_id')), {})
        rows.append({'title_style': arm['title_style'],
                     'voice': entry.get('voice') or arm['voice'],  # actual engine; Kokoro can fall back to Edge
                     'yt_views': yt.get('views'), 'yt_likes': yt.get('likes'), 'yt_pct': yt.get('avg_view_pct'),
                     'ig_views': ig.get('views'), 'ig_likes': ig.get('likes'),
                     'ig_skip': ig.get('skip_rate'), 'ig_watch': ig.get('average_watch_time_seconds')})
    print(f'{args.id}: {len(rows)} videos >= {args.min_hours:g}h old ({young} younger skipped)\n')
    for factor in ('title_style', 'voice'):
        groups = defaultdict(list)
        for row in rows:
            groups[row[factor]].append(row)
        print(f'by {factor}:')
        for name, group in sorted(groups.items()):
            print(f'  {name:10} {summarize(group)}')
    cells = defaultdict(list)
    for row in rows:
        cells[(row['title_style'], row['voice'])].append(row)
    print('\nby cell:')
    for (title, voice), group in sorted(cells.items()):
        print(f'  {title:9} + {voice:6} {summarize(group)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
