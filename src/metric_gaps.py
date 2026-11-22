"""Which missing platform metrics are normal reporting behaviour, not pipeline faults.

YouTube Analytics returns no viewing percentage for a video nobody has watched, and it
publishes per-video metrics two to three days after upload. Only a gap outside those two
cases is a fault. The Operations monitor gets these statuses in its evidence, and its
reports are checked against them in code, so a model cannot keep raising normal delays
as an active failure.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

REPORT_DELAY = timedelta(days=3)
STATUSES = ('reported', 'no_views_yet', 'reporting_delay', 'missing')
RULE = ('yt_pct (YouTube average percentage viewed) is normally absent for a video with no YouTube views yet '
        "(yt_pct_status 'no_views_yet') and for about 3 days after upload ('reporting_delay'). Those are not faults "
        "and must not be reported as active failures or suspect metrics. Only yt_pct_status 'missing' is a fault.")
# Reports about viewing percentage / retention gaps, whatever key or wording the model picks.
PERCENT_TOPIC = re.compile(r'view(ing|ed)?[\s_-]*(percent|percentage|pct|%)|percent(age)?[\s_-]*(of\s+)?(the\s+)?(video\s+)?'
                           r'(viewed|watched)|%[\s_-]*(viewed|watched)|yt[\s_-]*pct|average[\s_-]*view[\s_-]*(percent|percentage|duration)|retention',
                           re.IGNORECASE)


def _published(value) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def yt_pct_status(video: dict, now: datetime) -> str:
    if isinstance(video.get('yt_pct'), (int, float)):
        return 'reported'
    if not video.get('yt'):
        return 'no_views_yet'
    published = _published(video.get('at'))
    if published is None or now - published < REPORT_DELAY:
        return 'reporting_delay'
    return 'missing'


def annotate(videos: list[dict], now: datetime) -> tuple[list[dict], dict]:
    """Videos with yt_pct_status added, and counts by status (with titles of real faults)."""
    out, counts = [], {status: 0 for status in STATUSES}
    faults = []
    for video in videos:
        status = yt_pct_status(video, now)
        counts[status] += 1
        if status == 'missing':
            faults.append(str(video.get('title') or video.get('yt_id') or '')[:80])
        out.append({**video, 'yt_pct_status': status})
    return out, {**counts, 'missing_titles': faults[:10], 'rule': RULE}


def about_percentages(*texts) -> bool:
    return any(PERCENT_TOPIC.search(str(text or '')) for text in texts)


def screen(findings: dict, videos: list[dict], now: datetime) -> tuple[dict, list[dict]]:
    """Drop viewing-percentage reports the data shows are normal delays. Returns (findings, settled)."""
    _, counts = annotate(videos, now)
    statuses = {str(v.get('yt_id')): yt_pct_status(v, now) for v in videos if v.get('yt_id')}
    if counts['missing']:
        return findings, []  # a real gap exists: the report stands
    explanation = (f"Normal YouTube reporting: {counts['no_views_yet']} video(s) have no views yet and "
                   f"{counts['reporting_delay']} are under 3 days old; every older video with views has its viewing percentage.")
    kept, settled = [], []
    for item in findings.get('active_failures') or []:
        if about_percentages(item.get('key'), item.get('title'), item.get('plain'), item.get('evidence')):
            settled.append({'key': item['key'], 'title': item.get('title', ''), 'reason': explanation})
        else:
            kept.append(item)
    suspects = [s for s in findings.get('suspect_metrics') or []
                if not (s.get('platform') == 'yt' and about_percentages(s.get('reason'))
                        and statuses.get(s.get('video_id'), 'missing') != 'missing')]
    return {**findings, 'active_failures': kept, 'suspect_metrics': suspects}, settled
