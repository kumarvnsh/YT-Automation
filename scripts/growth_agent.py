#!/usr/bin/env python3
"""Growth agent (laptop, daily): picks the UTC publish hours in data/growth_plan.json.

Scores each dispatch hour on reach (Facebook views x2 + total views) over the last 28
days, and swaps at most one hour every growth.change_interval_days: hours outside the
US/Europe audience window first, then the weakest proven hour for the most promising
allowed one. The laptop dispatcher publishes at these hours; the workflow's gate
re-checks them. Rules live in src/growth.py.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src import bot_findings  # noqa: E402
from src.config import load_config  # noqa: E402
from src.growth import PLAN, WINDOW_DAYS, hour_stats, problem, propose, settings  # noqa: E402
from src.strategy import peaks  # noqa: E402

REFRESH_HOURS = 20         # --if-stale: skip when the plan is younger than this


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--if-stale', action='store_true', help=f'skip if the plan is under {REFRESH_HOURS}h old')
    ap.add_argument('--dry-run', action='store_true', help='print the plan without writing it')
    args = ap.parse_args()

    cfg = load_config()
    s = settings(cfg)
    now = datetime.now(timezone.utc)
    path = ROOT / PLAN
    try:
        prev = json.loads(path.read_text())
    except (OSError, ValueError):
        prev = {}
    if args.if_stale and prev.get('updated'):
        if now - datetime.fromisoformat(prev['updated']) < timedelta(hours=REFRESH_HOURS):
            print('growth: plan is fresh, skipping')
            return 0

    index = json.loads((ROOT / 'data/published_index.json').read_text())
    best = peaks(ROOT, (now - timedelta(days=WINDOW_DAYS + 3)).date().isoformat())
    best, masked = bot_findings.mask_suspect_metrics(ROOT.parent / 'bot-workspace', 'histold', index, best, now)
    stats = hour_stats(index, best, now)

    current = prev.get('hours_utc') if not problem(prev.get('hours_utc'), s) else s['default_hours_utc']
    changed_at = prev.get('changed_at')
    pinned_until = prev.get('pinned_until')
    if pinned_until and now >= datetime.fromisoformat(pinned_until):
        pinned_until = None
    change = None
    if pinned_until:
        hours, status = current, f'pinned by you until {pinned_until[:16]} UTC'
    elif changed_at and now - datetime.fromisoformat(changed_at) < timedelta(days=s['change_interval_days']):
        hours, status = current, f"holding: last change {changed_at[:10]}, measuring its effect"
    else:
        hours, change = propose(current, stats, s)
        status = 'changed one hour' if change else 'no hour clearly better; holding'
        if change:
            changed_at = now.isoformat()
    if hours is not None and problem(hours, s):
        status = f'no valid plan ({problem(hours, s)}); gate uses defaults'
        hours = None

    history = prev.get('history', [])
    if change:
        history = (history + [{'at': changed_at, **change}])[-30:]
    plan = {
        'updated': now.isoformat(),
        'status': status,
        'hours_utc': hours,
        'posts_per_day': s['posts_per_day'],
        'changed_at': changed_at,
        'pinned_until': pinned_until,
        'history': history,
        'hour_stats': {f'{h:02d}': stats[h] for h in sorted(stats)},
        'masked_metrics': masked,
        'method': __doc__.strip().splitlines()[0],
    }
    print(f"growth: {status}")
    print(f"  hours_utc: {hours if hours is not None else 'defaults ' + str(s['default_hours_utc'])}")
    if change:
        print(f"  swap {change['removed']:02d} -> {change['added']:02d} UTC: {change['reason']}")
    print('  reach by hour: ' + ', '.join(f"{h:02d}h {st['shrunk']:.2f} (n={st['n']})" for h, st in sorted(stats.items())))
    if not args.dry_run:
        path.write_text(json.dumps(plan, indent=2) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
