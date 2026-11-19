"""Content evidence for local AI: deduplicated mature videos, measured in code."""
from datetime import datetime, timedelta, timezone
from statistics import median

MIN_SAMPLE = 6


def evidence(videos: list[dict], now: datetime | None = None, *, like_rate_only=False) -> dict:
    now = now or datetime.now(timezone.utc)
    if like_rate_only:
        from .strategy import like_rate
        videos = [{**v, 'fb_like_rate': like_rate({'likes': v.get('fb_likes'), 'views': v.get('fb')})}
                  for v in videos if v.get('format', 'short') == 'short']
    metric = 'fb_like_rate' if like_rate_only else 'fb'
    mature = [v for v in videos if (like_rate_only or v.get('score') is not None) and
              timedelta(hours=48) <= now - datetime.fromisoformat(v['at']) <= timedelta(days=28)]
    known = [v for v in mature if v.get(metric) is not None]
    baseline = median(v[metric] for v in known) if known else None
    groups = []
    for dim in ('region', 'series', 'length', 'design') if like_rate_only else ('region', 'series', 'length'):
        for option in sorted({v[dim] for v in mature if v.get(dim)}):
            rows = [v for v in mature if v.get(dim) == option]
            fb = [v[metric] for v in rows if v.get(metric) is not None]
            yt = [v['yt_pct'] for v in rows if v.get('yt_pct') is not None]
            skip = [v['ig_skip'] for v in rows if v.get('ig_skip') is not None]
            med = median(fb) if fb else None
            groups.append({'id': f'{dim}:{option}', 'dimension': dim, 'option': option,
                           'n': len(rows), 'fb_n': len(fb), 'fb_median': med,
                           'fb_vs_baseline_pct': round((med / baseline - 1) * 100, 1) if baseline and med is not None else None,
                           'yt_viewed_median': round(median(yt), 1) if yt else None,
                           'ig_skip_median': round(median(skip), 1) if skip else None,
                           'enough_data': len(fb) >= MIN_SAMPLE})
    proven = [g for g in groups if g['enough_data']]
    working = sorted([g for g in proven if (g['fb_vs_baseline_pct'] or 0) > 0],
                     key=lambda g: -g['fb_vs_baseline_pct'])
    weak = sorted([g for g in proven if (g['fb_vs_baseline_pct'] or 0) < 0],
                  key=lambda g: g['fb_vs_baseline_pct'])

    def row(v):
        keys = ('title', 'series', 'region', 'length', 'at', 'yt_id')
        return {k: v.get(k) for k in keys + (('fb_like_rate', 'design') if like_rate_only else ('fb', 'ig', 'yt', 'yt_pct', 'score'))}

    ranked = sorted(known, key=lambda v: (-v[metric], -(v.get('score') or 0)))
    return {'window_days': 28, 'min_age_hours': 48, 'min_sample': MIN_SAMPLE,
            'mature_videos': len(mature), 'fb_coverage': len(known), 'fb_baseline_median': baseline,
            'metric': metric,
            'groups': groups, 'working': working, 'not_working': weak,
            'best_videos': [row(v) for v in ranked[:5]],
            'weak_videos': [row(v) for v in reversed(ranked[-5:])],
            'limitations': [('Likes divided by the exported Facebook view count. Snapshot ages and view definitions can differ.'
                             if like_rate_only else 'Peak exported views, not matched-age views: older posts have had longer to accumulate views.'),
                            'These are associations, not proof that a title, region or series caused performance.',
                            'Facebook per-video watch time, shares, followers and earnings are unavailable in the current performance exports. Page earnings, when present separately, cannot be attributed to videos.',
                            'No viewer geography or audience-active-hours data is available; targeting is not verified audience location.']}
