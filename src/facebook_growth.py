"""Explicit dashboard observations and exploratory Facebook-only comparisons."""
import csv
from datetime import datetime
import math
from src.experiments import capture_snapshots
from src.facebook_monetization import build_report as earnings_report

EXPERIMENT = 'facebook-world-history-4w'
FIELDS = ('views', 'shares', 'followers_gained', 'average_watch_time_seconds', 'three_second_retention', 'completion_rate')
REQUIRED = ('media_id', 'observed_at', 'period_start', 'period_end')


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Timestamps require an explicit timezone')
    return parsed


def parse_insights(stream):
    reader = csv.DictReader(stream)
    if not set(REQUIRED).issubset(reader.fieldnames or []):
        raise ValueError('Required headers: ' + ','.join(REQUIRED))
    rows, seen = [], set()
    for line, raw in enumerate(reader, 2):
        try:
            row = {field: (raw.get(field) or '').strip() for field in REQUIRED}
            if not row['media_id'].isdigit():
                raise ValueError('media_id must be a numeric Facebook Reel ID')
            start, end, observed = [timestamp(row[key]) for key in ('period_start','period_end','observed_at')]
            if not start <= end <= observed:
                raise ValueError('Require period_start <= period_end <= observed_at')
            for field in FIELDS:
                value = (raw.get(field) or '').strip()
                number = float(value) if value else None
                if number is not None and (not math.isfinite(number) or number < 0):
                    raise ValueError(f'{field} must be finite and nonnegative')
                if number is not None and field in ('views','shares','followers_gained') and not number.is_integer():
                    raise ValueError(f'{field} must be an integer')
                if number is not None and field in ('three_second_retention','completion_rate') and number > 1:
                    raise ValueError(f'{field} must be a fraction from 0 to 1')
                row[field] = number
            key = (row['media_id'], observed)
            if key in seen:
                raise ValueError('Duplicate Reel observation')
            seen.add(key)
            row['source'] = 'manual_facebook_dashboard'
            row['view_definition'] = (raw.get('view_definition') or '').strip() or None
            rows.append(row)
        except (ValueError, TypeError) as exc:
            raise ValueError(f'CSV line {line}: {exc}') from exc
    if not rows:
        raise ValueError('No observations in CSV')
    return rows


def capture_manual(posts, experiment, observations, existing):
    result = list(existing)
    indexed = {str(post.get('facebook_id')): post for post in posts if post.get('facebook_id')}
    for row in sorted(observations, key=lambda item: timestamp(item['observed_at'])):
        post = indexed.get(row['media_id'])
        if not post or not post.get('published_at'):
            continue
        # Equal-age cumulative snapshots require the entire lifetime to observation.
        if timestamp(row['period_start']) != timestamp(post['published_at']) or timestamp(row['period_end']) != timestamp(row['observed_at']):
            continue
        video = {**row, 'id':row['media_id'], 'publishedAt':post['published_at']}
        same_source = [snap for snap in result if snap.get('source') == row['source'] and snap.get('view_definition') == row.get('view_definition')]
        updated = capture_snapshots([post], experiment, {'facebook':{'updated':row['observed_at'],'videos':[video]}}, same_source)
        for snap in updated[len(same_source):]:
            snap['source'] = row['source']
            snap['view_definition'] = row.get('view_definition')
            for metric in ('shares_per_1000_views','followers_per_1000_views'):
                snap['metrics'][metric]['denominator']['source'] = row['source'] + '.views'
                snap['metrics'][metric]['denominator']['period'] = {key:row[key] for key in ('period_start','period_end')}
                if not row.get('view_definition'):
                    snap['metrics'][metric]['value'] = None
                    snap['metrics'][metric]['reason'] = 'Requires an explicit dashboard view definition.'
            snap['reporting_period'] = {key:row[key] for key in ('period_start','period_end')}
        result.extend(updated[len(same_source):])
    return result


def build_report(posts, snapshots, earnings):
    indexed = {str(post.get('facebook_id')): post for post in posts if post.get('facebook_id')}
    observations, groups = [], {}
    for snap in snapshots:
        if snap.get('platform') != 'facebook' or snap.get('experiment_id') != EXPERIMENT:
            continue
        post = indexed.get(str(snap['media_id']), {})
        metadata = {key:post.get(key) for key in ('editorial_region','editorial_series','duration_variant')}
        source = snap.get('source') or 'meta_api_unverified_views'
        definition = snap.get('view_definition') or None
        metrics = dict(snap['metrics'])
        if not definition:
            for name in ('shares_per_1000_views','followers_per_1000_views'):
                metrics[name] = {**metrics.get(name,{}), 'value':None, 'reason':'Requires an explicit verified view definition.'}
        observations.append({**snap, **metadata, 'source':source, 'view_definition':definition, 'metrics':metrics})
        if snap.get('late') or snap.get('target_age_hours') not in (24,72,168):
            continue
        # Unknown definitions remain one-Reel observations, never pooled rates.
        key = (snap['target_age_hours'], *metadata.values(), source, definition, None if definition else str(snap['media_id']))
        groups.setdefault(key, []).append(snap)
    comparisons = []
    for key, rows in groups.items():
        group = dict(zip(('target_age_hours','editorial_region','editorial_series','duration_variant','source','view_definition','unverified_definition_media_id'),key))
        group['sample_size'] = len(rows)
        group['actual_age_hours_range'] = [min(row['actual_age_hours'] for row in rows), max(row['actual_age_hours'] for row in rows)]
        views = [row['metrics'].get('views',{}).get('value') for row in rows]
        group['total_views'] = sum(value for value in views if value is not None) if any(value is not None for value in views) else None
        for metric, label in [('shares','shares_per_1000_views'),('followers_gained','followers_per_1000_views')]:
            pairs = [(row['metrics'].get(metric,{}).get('value'),row['metrics'].get('views',{}).get('value')) for row in rows]
            pairs = [(count,views) for count,views in pairs if group['view_definition'] and count is not None and views is not None and views > 0]
            group[label] = sum(count for count,views in pairs) / sum(views for count,views in pairs) * 1000 if pairs else None
            group[label + '_sample_size'] = len(pairs)
        comparisons.append(group)
    eligible_ids = {str(row['media_id']) for row in observations}
    return {'experiment_id':EXPERIMENT,'observations':observations,'comparisons':comparisons,
            'earnings_by_period':earnings_report([row for row in earnings if str(row['media_id']) in eligible_ids],posts),
            'limitations':['Exploratory descriptive comparisons only; small unrandomized samples do not establish causal winners.',
                          'Only on-time cumulative observations are compared at the same target age; late observations remain visible.',
                          'Sources and explicit view definitions are separate comparison groups. Unknown view definitions have no derived rates and are not pooled across Reels.',
                          'Missing measures remain null. Rate denominators include only Reels with both measures available.',
                          'Region, series and duration may be confounded. Unknown metadata is grouped as null.',
                          'Earnings retain their own reporting periods and currencies; never divide period earnings by snapshot views.']}


NATIVE_FIELDS = {'Duration (sec)':'duration_seconds','Views':'views','Shares':'shares',
                 'Net follows':'net_follows','Average Seconds viewed':'average_seconds_viewed',
                 'Seconds viewed':'seconds_viewed','Viewers':'viewers','Reactions':'reactions',
                 'Comments':'comments','Interactions':'interactions','Saves':'saves',
                 'Approximate earnings (USD)':'approximate_earnings_usd',
                 'Content monetisation (USD)':'content_monetisation_usd','Stars (USD)':'stars_usd'}


def parse_native_export(stream):
    """Native Meta publish-time selection: keep Lifetime scope and timezone unknown."""
    import re
    reader = csv.DictReader(stream)
    required = {'Post ID','Post type','Date','Publish time','Views'}
    if not required.issubset(reader.fieldnames or []):
        raise ValueError('Not a supported native Meta content export')
    rows, seen = [], set()
    for line, raw in enumerate(reader,2):
        try:
            post_id = raw['Post ID'].strip()
            if not post_id.isdigit() or post_id in seen:
                raise ValueError('Post ID must be numeric and unique')
            if raw['Date'].strip() != 'Lifetime':
                raise ValueError('Only native Lifetime summary rows are supported; normalize other reporting periods explicitly')
            published = datetime.strptime(raw['Publish time'].strip(),'%m/%d/%Y %H:%M')
            row = {'post_id':post_id,'post_type':raw['Post type'].strip(),
                   'metric_scope':raw['Date'].strip(),'published_local':published.isoformat(),
                   'published_timezone':None,'title':(raw.get('Title') or '').split('\n')[0],
                   'permalink':raw.get('Permalink'),'data_comment':raw.get('Data comment')}
            match = re.search(r'/reel/(\d+)',row['permalink'] or '')
            row['reel_id'] = match.group(1) if match else None
            for header,field in NATIVE_FIELDS.items():
                value = (raw.get(header) or '').strip()
                number = float(value.replace(',','')) if value else None
                if number is not None and (not math.isfinite(number) or (number < 0 and field != 'net_follows')):
                    raise ValueError(f'Invalid {header}')
                if number is not None and field in ('views','shares','net_follows','viewers','reactions','comments','interactions','saves') and not number.is_integer():
                    raise ValueError(f'{header} must be an integer')
                row[field] = number
            rows.append(row)
            seen.add(post_id)
        except (ValueError,TypeError) as exc:
            raise ValueError(f'CSV line {line}: {exc}') from exc
    if not rows:
        raise ValueError('Native export contains no rows')
    return rows


def native_baseline(rows, observed_at=None):
    import statistics
    if observed_at:
        timestamp(observed_at)
    reels = [row for row in rows if row['post_type'] == 'Reel']
    def summarize(items):
        result = {'sample_size':len(items)}
        for key in ('views','shares','net_follows','reactions','comments','interactions','seconds_viewed','approximate_earnings_usd','content_monetisation_usd','stars_usd'):
            values = [row[key] for row in items if row.get(key) is not None]
            result[key] = round(sum(values),6) if values else None
        views = [row['views'] for row in items if row.get('views') is not None]
        watches = [row['average_seconds_viewed'] for row in items if row.get('average_seconds_viewed') is not None]
        result['median_views'] = statistics.median(views) if views else None
        result['median_average_seconds_viewed'] = statistics.median(watches) if watches else None
        pairs = [row for row in items if row.get('shares') is not None and (row.get('views') or 0)>0]
        result['shares_per_1000_views'] = sum(row['shares'] for row in pairs)/sum(row['views'] for row in pairs)*1000 if pairs else None
        return result
    groups = []
    for low,high in ((0,40),(40,50),(50,60),(60,float('inf'))):
        subset = [row for row in reels if row.get('duration_seconds') is not None and low<=row['duration_seconds']<high]
        if subset:
            groups.append({'duration_bucket':f'{low}–{high-1}' if math.isfinite(high) else '60+',**summarize(subset)})
    return {'source':'native_meta_content_publish_time_summary','observed_at':observed_at,
            'metric_scopes':sorted({row['metric_scope'] for row in rows}),
            'selected_publish_local_range':[min(row['published_local'] for row in rows),max(row['published_local'] for row in rows)],
            'all_post_totals':summarize(rows),'totals':summarize(reels),'duration_groups':groups,
            'top_reels_by_views':sorted(reels,key=lambda row:row.get('views') or 0,reverse=True)[:12],
            'reels_with_shares':sorted([row for row in reels if (row.get('shares') or 0)>0],key=lambda row:row['shares'],reverse=True),
            'reels_with_net_follows':sorted([row for row in reels if (row.get('net_follows') or 0)!=0],key=lambda row:row['net_follows'],reverse=True),
            'limitations':['Publish-time selection is not the metric reporting period: Date=Lifetime is retained explicitly.',
                          'Export capture time and publish timezone are unknown unless separately supplied; no equal-age snapshots generated.',
                          'Older Reels have more exposure time. Duration and topic comparisons are descriptive, not causal.',
                          'Net follows are signed net changes, not gross attributed followers gained; no follows-per-view conversion.',
                          'Page screenshot follows and engagement, and lifetime audience demographics, are separate scopes.',
                          'Average Seconds viewed is used as reported; do not infer retention or completion from it.',
                          'Interaction totals need not equal summed reactions/comments/shares; definitions can differ.',
                          'Approximate earnings are reported estimates, not payouts or period RPM.']}
