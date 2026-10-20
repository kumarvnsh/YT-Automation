"""Six-Reel assignments and immutable, platform-specific observation snapshots."""
from __future__ import annotations

from datetime import datetime, timezone
import math

METRICS = {
    'views': ('views', 'Platform-reported cumulative views; platform definitions differ.'),
    'average_watch_time_seconds': ('average_watch_time_seconds', 'Platform-reported average watch duration in seconds.'),
    'three_second_retention': ('three_second_retention', 'Platform-reported fraction reaching three seconds; unavailable without a supported retention endpoint.'),
    'completion_rate': ('completion_rate', 'Platform-reported completed plays divided by eligible plays; not average view percentage.'),
    'shares': ('shares', 'Platform-reported cumulative shares.'),
    'followers_gained': ('subs_gained', 'YouTube subscribers gained attributed to this video; Meta per-media follows if available.'),
}


def _experiment_id(entry):
    value = entry.get('experiment_id', entry.get('experiment'))
    return value.get('id') if isinstance(value, dict) else value


def select_slot(entries: list[dict], experiment: dict) -> dict | None:
    used = {e.get('experiment_slot', e.get('slot')) for e in entries if _experiment_id(e) == experiment['id']}
    return next((dict(s) for s in experiment['slots'] if s['slot'] not in used), None)


def _date(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result


def _number(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def metric_values(video: dict, platform: str = "youtube") -> dict:
    result = {}
    unavailable = video.get('unavailable_metrics', {})
    for name, (source, definition) in METRICS.items():
        value = video.get(source)
        if name == 'followers_gained':
            value = video.get('followers_gained', value)
        result[name] = {'value': value if _number(value) else None, 'definition': definition,
                        'reason': None if _number(value) else unavailable.get(source, 'Not returned by this export or unavailable for this media/account.')}
    for name, numerator in [('shares_per_1000_views','shares'), ('followers_per_1000_views','followers_gained')]:
        count = result[numerator]['value']
        period = video.get('analytics_period') if platform == 'youtube' else None
        views = video.get('analytics_views') if platform == 'youtube' else result['views']['value']
        compatible = platform != 'youtube' or (isinstance(period, dict) and bool(period.get('start_date')) and bool(period.get('end_date')))
        valid = compatible and _number(views) and views > 0 and count is not None
        denominator = {'value': views if _number(views) else None,
                       'source': 'youtube_analytics_report.views' if platform == 'youtube' else f'{platform}_media_insights.views',
                       'period': period}
        result[name] = {'value': count / views * 1000 if valid else None,
                        'denominator': denominator,
                        'reason': None if valid else 'Requires numerator and positive views from a compatible report period.',
                        'definition': f'{numerator} / compatible report views × 1000.'}
    return result


def capture_snapshots(entries: list[dict], experiment: dict, exports: dict, existing: list[dict], now=None) -> list[dict]:
    """Capture only the latest due window; never backfill old windows with newer counts."""
    now = now or datetime.now(timezone.utc)
    result = list(existing)
    keys = {(r['platform'], r['media_id'], r['target_age_hours']) for r in existing}
    for entry in entries:
        if _experiment_id(entry) != experiment['id']:
            continue
        ids = {'youtube': entry.get('video_id', entry.get('youtube_id')),
               'instagram': entry.get('instagram_id'), 'facebook': entry.get('facebook_id')}
        for platform, media_id in ids.items():
            export = exports.get(platform, {})
            video = next((v for v in export.get('videos', []) if str(v['id']) == str(media_id)), None) if media_id else None
            if video is None or not export.get('updated'):
                continue
            published = video.get('publishedAt') or entry.get('published_at')
            if not published:
                continue
            observed = _date(export['updated'])
            if observed > now:
                continue
            age = (observed - _date(published)).total_seconds() / 3600
            due = [h for h in experiment['observation_hours'] if age >= h]
            if not due:
                continue
            target = max(due)
            key = (platform, media_id, target)
            if key in keys:
                continue
            result.append({'experiment_id':experiment['id'], 'slot':entry.get('experiment_slot', entry.get('slot')),
                           'category':entry.get('category'), 'hook':entry.get('hook'), 'duration':entry.get('duration'),
                           'profile':entry.get('production_profile', entry.get('profile')), 'platform':platform, 'media_id':media_id,
                           'published_at':published, 'observed_at':observed.isoformat(),
                           'target_age_hours':target, 'actual_age_hours':round(age,4),
                           'late':age > target + experiment.get('window_tolerance_hours', 1),
                           'missed_windows':[h for h in due if h < target and (platform,media_id,h) not in keys],
                           'metrics':metric_values(video, platform)})
            keys.add(key)
    return result
