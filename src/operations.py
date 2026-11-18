"""Measured health alerts, audience summaries and advisory weekly reviews."""
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
EUROPE = set('AL AD AT BY BE BA BG HR CY CZ DK EE FI FR DE GR HU IS IE IT XK LV LI LT LU MT MD MC ME NL MK NO PL PT RO RU SM RS SK SI ES SE CH TR UA GB VA'.split())


def audience_summary(report):
    """Do not mix viewers, Page likes and Instagram followers into one audience rate."""
    output = {}
    for platform, data in (report.get('platforms') or {}).items():
        countries = data.get('countries') or {}
        total = sum(countries.values())
        us = countries.get('US', 0)
        europe = sum(v for k, v in countries.items() if k in EUROPE)
        output[platform] = {**data, 'reported_total': total, 'usa': us, 'europe': europe,
                            'usa_europe_share_of_reported': (us + europe) / total if total else None}
    return output


def health_alerts(data, runs=None, now=None):
    now = now or datetime.now(timezone.utc)
    alerts = []
    for name, hours in {'YouTube export': 3, 'Facebook/Instagram export': 3,
                        'Topic strategy': 72, 'Laptop dispatcher': 2}.items():
        stamp = data.get('freshness', {}).get(name)
        try:
            stale = (now - datetime.fromisoformat(stamp.replace('Z', '+00:00'))).total_seconds() > hours * 3600
        except (TypeError, ValueError, AttributeError):
            stale = True
        if stale:
            alerts.append({'id': 'stale:' + name, 'severity': 'warning', 'message': f'{name} is missing or older than {hours} hours.'})
    # A daily-cap cancellation is not a failed publish. Inspect only actual failure states.
    latest = {}
    for run in runs or []:
        if run.get('displayTitle') in ('Histold script preview','Histold render preview'):
            continue  # Draft validation is not a failed public posting cycle.
        workflow = run.get('workflowName', '')
        if run.get('status') != 'completed' or workflow in latest:
            continue
        if run.get('conclusion') in ('cancelled', 'skipped', 'neutral'):
            continue
        latest[workflow] = run
        if run.get('conclusion') in ('failure', 'timed_out', 'action_required'):
            alerts.append({'id': f'run:{run.get("databaseId")}', 'severity': 'error',
                           'message': f'{workflow}: {run["conclusion"]}', 'url': run.get('url')})
    for platform in ('facebook', 'youtube'):
        state = (data.get('earnings_status') or {}).get(platform, {})
        if state.get('status') not in (None, 'collected'):
            alerts.append({'id': 'earnings:' + platform, 'severity': 'warning',
                           'message': f'{platform} earnings: {state["status"]}'})
    return alerts


def snapshot(data, now):
    videos = {}
    for video in data.get('videos', []):
        # Preserve platform identity when an upload exists on only one platform.
        for platform, key in [('fb', 'fb_id'), ('ig', 'ig_id'), ('yt', 'yt_id')]:
            if video.get(key):
                videos.setdefault(platform + ':' + str(video[key]), {})[platform] = video.get(platform)
    return {'generated': now.isoformat(), 'videos': videos}


def observed_change(current, previous):
    """Subtract only like-for-like IDs and metrics present in both snapshots."""
    output = {}
    for platform in ('fb', 'ig', 'yt'):
        common = [(v.get(platform), previous.get('videos', {}).get(k, {}).get(platform))
                  for k, v in current.get('videos', {}).items()]
        common = [(a, b) for a, b in common if a is not None and b is not None]
        output[platform] = {'videos_compared': len(common), 'observed_view_change': sum(a - b for a, b in common)}
    return output


def weekly_review(data, now, current, previous=None):
    evidence = data.get('content_evidence') or {}
    daily = (data.get('audience') or {}).get('youtube_daily') or {}
    columns = daily.get('columns') or []
    records = [dict(zip(columns, row)) for row in daily.get('rows', [])]
    # Compare two fully returned 7-day windows ending at the latest reporting day.
    trend = None
    if records:
        end = max(datetime.fromisoformat(r['day']).date() for r in records)
        windows = []
        for offset in (0, 7):
            last = end - timedelta(days=offset)
            first = last - timedelta(days=6)
            rows = [r for r in records if str(first) <= r['day'] <= str(last)]
            windows.append({'start': str(first), 'end': str(last), 'days_returned': len(rows),
                            **{key: sum(r.get(key, 0) for r in rows) for key in ('views', 'subscribersGained', 'subscribersLost')},
                            'daily_retention': [{k: r.get(k) for k in ('day','averageViewDuration','averageViewPercentage')} for r in rows]})
        trend = {'current': windows[0], 'previous': windows[1], 'complete': all(w['days_returned'] == 7 for w in windows)}
    return {'generated': now.isoformat(), 'week': now.astimezone(IST).strftime('%G-W%V'),
            'window': 'Last 28 days; content findings use videos aged at least 48 hours.',
            'audience': audience_summary(data.get('audience') or {}),
            'working': evidence.get('working') or [], 'not_working': evidence.get('not_working') or [],
            'youtube_week_comparison': trend,
            'local_ai_analysis': data.get('ai_insights'),
            'matched_snapshot_change': observed_change(current, previous) if previous else None,
            'comparison_start': previous.get('generated') if previous else None,
            'earnings': {'facebook': data.get('monetization_api'), 'youtube': data.get('youtube_earnings')},
            'proposals': ['Review measured winning topics in Content intelligence.',
                          'Keep three automatic daily posts; consider timing changes only with adequate slot samples.',
                          'Confirm proposed changes in the dashboard before applying them.'],
            'limitations': ['Snapshot changes cover matched videos, not complete platform daily views.',
                            'Country shares use reported countries only; suppressed or unreturned countries are excluded.',
                            'Content subject region is not viewer geography. Earnings estimates are not payouts.',
                            'This report is advisory and does not publish or modify strategy.']}


def daily_briefing(data, alerts, now):
    """Compact WhatsApp briefing with explicit metric periods and local times."""
    local = now.astimezone(IST)
    lines = ['*JARVIS | HISTOLD*', local.strftime('%a, %d %b %Y | %I:%M %p IST'), '', '*SYSTEM STATUS*']
    errors = sum(a.get('severity') == 'error' for a in alerts)
    warnings = len(alerts) - errors
    lines.append(f'{errors} issue(s) need attention; {warnings} warning(s).' if alerts else 'All monitored checks passed.')
    lines.extend('- ' + a['message'] for a in alerts[:3])
    if len(alerts) > 3:
        lines.append(f'+ {len(alerts)-3} more in the dashboard.')
    hours = sorted(data.get('hours') or [])
    lines.extend(['', '*PUBLISHING*'])
    if hours:
        candidates = [now.astimezone(timezone.utc).replace(hour=h, minute=0, second=0, microsecond=0) for h in hours]
        candidates = [at if at > now else at + timedelta(days=1) for at in candidates]
        upcoming = min(candidates).astimezone(IST)
        lines.append('Next scheduled slot: ' + upcoming.strftime('%d %b, %I:%M %p IST'))
        times = sorted((h*60+330) % 1440 for h in hours)
        lines.append('Daily slots: ' + ', '.join(f'{m//60:02d}:{m%60:02d}' for m in times) + ' IST')
    else:
        lines.append('No posting slots available in this snapshot.')
    videos = data.get('videos') or []
    today = 0
    for video in videos:
        try:
            if datetime.fromisoformat(video['at'].replace('Z', '+00:00')).astimezone(IST).date() == local.date():
                today += 1
        except (KeyError, TypeError, ValueError):
            pass
    lines.append(f'Published today: {today} tracked video(s)')
    lines.extend(['', '*PERFORMANCE SNAPSHOT*', 'Views on videos published in the last 28 days:'])
    for name, key in [('YouTube', 'yt'), ('Facebook', 'fb'), ('Instagram', 'ig')]:
        values = [v[key] for v in videos if isinstance(v.get(key), (int, float))]
        lines.append(f'- {name}: {sum(values):,.0f} views ({len(values)} measured videos)' if values else f'- {name}: unavailable')
    lines.append('These are accumulated video views, not views earned today.')
    evidence = data.get('content_evidence') or {}
    lines.extend(['', '*CONTENT SIGNALS*'])
    found = False
    for label, groups in [('Stronger', evidence.get('working') or []), ('Weaker', evidence.get('not_working') or [])]:
        if groups:
            group = groups[0]
            median_views = group.get('fb_median')
            measured = f'{median_views:,.0f}' if isinstance(median_views, (int, float)) else 'unavailable'
            lines.append(f"- {label}: {str(group.get('option', '')).replace('_', ' ').title()} | median FB views {measured} | {group.get('n', 0)} videos")
            found = True
    if not found:
        lines.append('Not enough comparable evidence to name a stronger format yet.')
    else:
        lines.append('Observed associations; results are not proof of cause.')
    interpretation = (data.get('ai_insights') or {}).get('interpretation') or {}
    lines.extend(['', '*NEXT ACTION*'])
    if errors:
        lines.append('Check the issues above in the dashboard.')
    elif interpretation.get('next_test'):
        lines.append(str(interpretation['next_test'])[:240] + ' (approval needed)')
    elif alerts:
        lines.append('Review the warnings above in the dashboard.')
    else:
        lines.append('Keep the current schedule and collect more comparable results.')
    lines.extend(['', 'Dashboard: https://vnsh.tail9bdcf7.ts.net/#/overview', 'Briefing only; no strategy changes applied.'])
    return '\n'.join(lines)


def alert_message(alert, now):
    return '\n'.join(['*JARVIS | HISTOLD ALERT*',
                      now.astimezone(IST).strftime('%d %b, %I:%M %p IST'),
                      '', '*Issue needing attention*', str(alert['message']),
                      '', 'Check current status and recovery details:',
                      'https://vnsh.tail9bdcf7.ts.net/#/overview'])
