"""Growth plan: which UTC hours scheduled publishes run at, chosen from performance data.

The always-on laptop dispatches publish.yml at the plan's hours (scripts/dispatch.py),
and the publish-hour gate in the workflow re-checks every scheduled run against the
same plan (data/growth_plan.json, written by the laptop growth agent), so a stray or
duplicate trigger cannot publish. How MANY videos a day is fixed by
growth.posts_per_day in config.yaml, never by the plan, so the agent can move posting
times but cannot raise spend.

Every plan is validated here, in the same code the gate runs; an invalid, missing or
stale plan falls back to growth.default_hours_utc.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

PLAN = 'data/growth_plan.json'
# Last-resort hours if config.yaml itself cannot be read: the gate must always decide.
FALLBACK_HOURS = [4, 7, 12, 16, 18]
SHRINK = 3            # pseudo-videos at score 0.5 per hour
EXPLORE_BONUS = 0.15  # optimism for hours with little data, shrinking with 1/sqrt(n+1)
SWAP_MARGIN = 0.03    # a new hour must look this much better than the one it replaces
PUBLISH_LAG = timedelta(minutes=5)  # dispatched at HH:00, uploads land at HH:05-HH:25
WINDOW_DAYS = 28
MIN_AGE_HOURS = 48
# This Week in History runs in the Sunday IST evening slot (14:00-24:00 IST); keep at
# least one hour there (09-18 UTC) so that slot still exists.
IST_EVENING_UTC = range(9, 19)


def settings(cfg) -> dict:
    g = cfg.get('growth') or {}
    return {
        'posts_per_day': int(g.get('posts_per_day', len(FALLBACK_HOURS))),
        'default_hours_utc': sorted(int(h) for h in g.get('default_hours_utc', FALLBACK_HOURS)),
        'allowed_hours_utc': sorted(int(h) for h in g.get('allowed_hours_utc', range(24))),
        'min_gap_hours': int(g.get('min_gap_hours', 2)),
        'plan_max_age_days': float(g.get('plan_max_age_days', 7)),
        'change_interval_days': float(g.get('change_interval_days', 3)),
        'min_samples': int(g.get('min_samples', 6)),
    }


def gaps_ok(hours: list[int], min_gap: int) -> bool:
    """Every pair of hours at least min_gap apart, counting across midnight."""
    hs = sorted(hours)
    return all((hs[(i + 1) % len(hs)] - h) % 24 >= min_gap for i, h in enumerate(hs)) if len(hs) > 1 else True


def problem(hours, s: dict) -> str | None:
    """Why a proposed hour list is not acceptable, or None when it is."""
    if not isinstance(hours, list) or not all(isinstance(h, int) and not isinstance(h, bool) for h in hours):
        return 'hours must be a list of integers'
    if len(hours) != s['posts_per_day']:
        return f"{len(hours)} hours but growth.posts_per_day is {s['posts_per_day']}"
    if len(set(hours)) != len(hours):
        return 'duplicate hours'
    if outside := sorted(h for h in hours if h not in s['allowed_hours_utc']):
        return f'hours outside growth.allowed_hours_utc: {outside}'
    if not gaps_ok(hours, s['min_gap_hours']):
        return f"hours closer than {s['min_gap_hours']}h apart"
    if not any(h in IST_EVENING_UTC for h in hours):
        return 'no hour in the IST evening slot (09-18 UTC) used by This Week in History'
    return None


def active_hours(root: Path, cfg, now: datetime | None = None) -> tuple[list[int], str]:
    """Hours the gate (and the dispatcher) should allow now, and where they came from."""
    s = settings(cfg)
    try:
        plan = json.loads((root / PLAN).read_text())
    except FileNotFoundError:
        return s['default_hours_utc'], 'default (no growth plan yet)'
    except (OSError, ValueError) as exc:
        return s['default_hours_utc'], f'default (unreadable growth plan: {exc})'
    return hours_from_plan(plan, cfg, now)


def hours_from_plan(plan: dict, cfg, now: datetime | None = None) -> tuple[list[int], str]:
    s = settings(cfg)
    now = now or datetime.now(timezone.utc)
    try:
        age = now - datetime.fromisoformat(plan['updated'])
    except (KeyError, TypeError, ValueError):
        return s['default_hours_utc'], 'default (growth plan has no valid "updated")'
    if age > timedelta(days=s['plan_max_age_days']):
        return s['default_hours_utc'], f'default (growth plan is {age.days} days old)'
    hours = plan.get('hours_utc')
    if hours is None:
        return s['default_hours_utc'], f"default (growth plan not steering hours yet: {plan.get('status', 'no reason given')})"
    if why := problem(hours, s):
        return s['default_hours_utc'], f'default (growth plan rejected: {why})'
    return sorted(hours), f"growth plan of {plan['updated'][:16]}"


def dedupe(index: list[dict]) -> list[dict]:
    """One entry per video: the index can hold several records for the same upload
    (e.g. a later record adding platform IDs). Fields merge, first non-empty value wins."""
    merged: dict[str, dict] = {}
    for e in index:
        key = e.get('video_id') or f"{e.get('title')}|{e.get('published_at')}"
        row = merged.setdefault(key, {})
        for k, v in e.items():
            if row.get(k) in (None, '', [], {}) and v not in (None, ''):
                row[k] = v
    return list(merged.values())


def dispatch_hour(published_at: str) -> int:
    return (datetime.fromisoformat(published_at.replace('Z', '+00:00')) - PUBLISH_LAG).astimezone(timezone.utc).hour


def _rank(values: list[float]):
    vs = sorted(values)
    return lambda x: (sum(y < x for y in vs) + sum(y == x for y in vs) / 2) / len(vs)


def hour_stats(index: list[dict], best: dict, now: datetime) -> dict[int, dict]:
    """Per dispatch hour: how scheduled Shorts published then did on reach.

    Posting time mostly moves early reach, so the score is reach only: Facebook views
    (double weight, the monetized platform) and total views across platforms, each as a
    percentile rank among the window's scheduled Shorts.
    """
    videos = []
    for e in dedupe(index):
        if not e.get('scheduled') or e.get('channel', 'histold') != 'histold' or not e.get('published_at'):
            continue
        age = (now - datetime.fromisoformat(e['published_at'].replace('Z', '+00:00'))).total_seconds() / 3600
        if not MIN_AGE_HOURS <= age <= WINDOW_DAYS * 24:
            continue
        fb = best['fb'].get(str(e.get('facebook_id')), {}).get('views')
        total = [p.get('views') for p in (best['yt'].get(e.get('video_id'), {}),
                                          best['ig'].get(str(e.get('instagram_id')), {}),
                                          best['fb'].get(str(e.get('facebook_id')), {}))
                 if p.get('views') is not None]
        videos.append({'hour': dispatch_hour(e['published_at']),
                       'fb': math.log1p(fb) if fb is not None else None,
                       'total': math.log1p(sum(total)) if total else None})
    ranks = {m: _rank([v[m] for v in videos if v[m] is not None])
             for m in ('fb', 'total') if sum(v[m] is not None for v in videos) > 1}
    groups = defaultdict(list)
    for v in videos:
        parts = [(w, ranks[m](v[m])) for m, w in (('fb', 2), ('total', 1)) if m in ranks and v[m] is not None]
        if parts:
            groups[v['hour']].append(sum(w * r for w, r in parts) / sum(w for w, _ in parts))
    return {h: {'n': len(sc), 'mean': round(sum(sc) / len(sc), 3),
                'shrunk': round((sum(sc) + SHRINK * 0.5) / (len(sc) + SHRINK), 3)} for h, sc in groups.items()}


def optimistic(stats: dict, hour: int) -> float:
    st = stats.get(hour, {'n': 0, 'shrunk': 0.5})
    return st['shrunk'] + EXPLORE_BONUS / math.sqrt(st['n'] + 1)


def propose(current: list[int], stats: dict, s: dict) -> tuple[list[int], dict | None]:
    """At most one hour swap: off-audience hours first, else the weakest proven hour
    for the most promising allowed hour (untried hours get an exploration bonus)."""
    allowed = s['allowed_hours_utc']
    off = [h for h in current if h not in allowed]
    if off:
        out, why = off[0], 'outside the US/Europe audience hours'
    else:
        proven = [h for h in current if stats.get(h, {}).get('n', 0) >= s['min_samples']]
        if not proven:
            return current, None
        out = min(proven, key=lambda h: stats[h]['shrunk'])
        why = f"weakest proven hour (score {stats[out]['shrunk']:.2f}, n={stats[out]['n']})"
    rest = [h for h in current if h != out]
    candidates = [h for h in allowed if h not in current and not problem(sorted(rest + [h]), s)]
    if not candidates:
        return current, None
    def spread(h):  # ties go to the hour furthest from the others: tests a new part of the day
        return min(min((h - r) % 24, (r - h) % 24) for r in rest) if rest else 0
    best = max(candidates, key=lambda h: (optimistic(stats, h), spread(h)))
    if not off and optimistic(stats, best) < stats[out]['shrunk'] + SWAP_MARGIN:
        return current, None
    st = stats.get(best, {'n': 0, 'shrunk': 0.5})
    return sorted(rest + [best]), {'removed': out, 'added': best, 'reason': why,
                                   'added_score': st['shrunk'], 'added_n': st['n']}
