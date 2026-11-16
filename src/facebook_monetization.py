"""Period-bound Facebook dashboard earnings. No inferred revenue or API fields."""
import csv
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation


def summarize_api(report, bonus_report=None):
    """One Page earnings stream, never sum API versions or overlapping metric families."""
    if not report:
        return None
    checks = report.get('checks') or []
    versions = list(dict.fromkeys([report.get('configured_version')] + [c.get('version') for c in checks]))
    errors = list(dict.fromkeys(c.get('message', 'Unavailable') for c in checks
                               if c.get('scope') == 'video' and c.get('status') != 'ok'))
    result = {'checked': report.get('generated'), 'available': False, 'per_video_errors': errors,
              'bonus_earnings': None, 'total_including_bonuses': None,
              'bonus_status': 'Not available from the collected API metrics',
              'limitations': ['Estimated Page earnings, not a payout statement or per-Reel attribution.',
                              'monetization_approximate_earnings explicitly excludes bonuses; this is not an all-in earnings total.',
                              'Content Monetization and approximate earnings may overlap; they are not added together.',
                              'microAmount values are retained in the private source report without guessing a conversion.']}
    for version in versions:
        for check in checks:
            if (check.get('version') != version or check.get('scope') != 'page' or check.get('status') != 'ok'
                    or check.get('metric') != 'monetization_approximate_earnings'):
                continue
            for metric in check.get('data') or []:
                if metric.get('period') != 'day':
                    continue
                currency = re.search(r'\bin\s+([A-Z]{3})\b', metric.get('description') or '')
                days = {}
                for row in metric.get('values') or []:
                    value = row.get('value')
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not row.get('end_time'):
                        continue
                    amount = Decimal(str(value))
                    if amount.is_finite() and amount >= 0:
                        days[row['end_time']] = amount
                if days:
                    ordered = sorted(days.items())
                    return _bonus_summary({**result, 'available': True, 'version': version, 'metric': check['metric'],
                            'currency': currency[1] if currency else None,
                            'currency_source': metric.get('description'),
                            'total': str(sum(days.values(), Decimal(0))), 'daily_buckets': len(days),
                            'first_bucket_end': ordered[0][0], 'last_bucket_end': ordered[-1][0],
                            'daily': [{'end_time': stamp, 'amount': str(amount)} for stamp, amount in ordered]}, bonus_report)
    return result


def _bonus_summary(summary, report):
    """Keep returned bonus details and the latest rolling total separate from daily sums."""
    if not report:
        return summary
    checks = [c for c in report.get('checks', []) if c.get('version') == summary['version'] and c.get('status') == 'ok']
    for check in checks:
        for metric in check.get('data') or []:
            currency = re.search(r'\bin\s+([A-Z]{3})\b', metric.get('description') or '')
            if not currency or currency[1] != summary.get('currency'):
                continue
            if (check.get('scope') == 'page_breakdown' and check.get('metric') == 'content_monetization_earnings'
                    and metric.get('period') == 'day'):
                bonus = {}
                sources = {}
                for row in metric.get('values', []):
                    value = row.get('value')
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not row.get('end_time'):
                        continue
                    amount = Decimal(str(value))
                    if not amount.is_finite() or amount < 0:
                        continue
                    source = row.get('earning_source')
                    if not source:
                        continue
                    sources[(source, row['end_time'])] = amount
                    if source == 'extra_bonus':
                        bonus[row['end_time']] = amount
                if sources:
                    summary['content_sources'] = {name: str(sum((a for (s, _), a in sources.items() if s == name), Decimal(0)))
                                                 for name in sorted({s for s, _ in sources})}
                if bonus:
                    returned = sum(bonus.values(), Decimal(0))
                    summary.update(bonus_earnings=str(returned) if returned else None,
                                   bonus_api_returned=str(returned),
                                   bonus_status=('Extra Bonus returned by Meta (not all possible bonus programs)' if returned else
                                                 'API returned zero; bonus coverage is unverified. This does not establish that no bonuses were earned.'),
                                   bonus_daily_buckets=len(bonus), bonus_checked=report.get('generated'))
            if (check.get('scope') == 'page_aggregate' and check.get('metric') == summary['metric']
                    and metric.get('period') == 'days_28'):
                candidates = []
                for row in metric.get('values', []):
                    value = row.get('value')
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not row.get('end_time'):
                        continue
                    amount = Decimal(str(value))
                    if amount.is_finite() and amount >= 0:
                        candidates.append((datetime.fromisoformat(row['end_time']), row['end_time'], amount))
                if candidates:
                    _, stamp, amount = max(candidates)
                    summary.update(rolling_28_excluding_bonuses=str(amount), rolling_bucket_end=stamp,
                                   rolling_checked=report.get('generated'))
                    same_end = datetime.fromisoformat(stamp) == datetime.fromisoformat(summary['last_bucket_end'])
                    summary['reconciliation'] = ('Meta rolling 28-day estimate differs from the returned daily-detail subtotal; no all-in total is inferred.'
                        if not same_end or abs(amount-Decimal(summary['total'])) > Decimal('0.005') else
                        'The latest rolling estimate agrees with the returned daily-detail subtotal to the nearest cent.')
    return summary

REQUIRED = ('media_id', 'period_start', 'period_end', 'currency')
COUNTS = ('qualified_views', 'stars_count')
MONEY = ('content_earnings', 'stars_earnings')


def _number(value, count=False):
    if value is None or not value.strip():
        return None
    try:
        number = Decimal(value.strip())
    except InvalidOperation as exc:
        raise ValueError(f'Invalid numeric value: {value!r}') from exc
    if not number.is_finite() or number < 0 or (count and number != number.to_integral_value()):
        raise ValueError(f'Invalid nonnegative {"integer" if count else "amount"}: {value!r}')
    return int(number) if count else format(number, 'f')


def _key(row):
    return tuple(row[field] for field in REQUIRED)


def parse_export(stream, mapping=None):
    reader = csv.DictReader(stream)
    mapping = mapping or {}
    headers = reader.fieldnames or []
    if any(mapping.get(name, name) not in headers for name in REQUIRED):
        raise ValueError('Export needs media_id, period_start, period_end and currency headers or explicit mappings')
    records, seen = [], set()
    for line, raw in enumerate(reader, 2):
        row = {name: (raw.get(mapping.get(name, name)) or '').strip() for name in REQUIRED + COUNTS + MONEY}
        try:
            start, end = date.fromisoformat(row['period_start']), date.fromisoformat(row['period_end'])
            if start > end or not row['media_id'].isdigit():
                raise ValueError('Use a numeric Reel ID and an ordered reporting period')
            row['currency'] = row['currency'].upper()
            if len(row['currency']) != 3 or not row['currency'].isalpha():
                raise ValueError('Use the three-letter currency code from the export')
            for name in COUNTS + MONEY:
                row[name] = _number(row[name], name in COUNTS)
            if _key(row) in seen:
                raise ValueError('Duplicate Reel/period/currency row')
        except ValueError as exc:
            raise ValueError(f'CSV line {line}: {exc}') from exc
        seen.add(_key(row))
        records.append(row)
    if not records:
        raise ValueError('Export contains no earnings rows')
    return merge_records([], records)


def merge_records(existing, incoming):
    merged = {_key(row): row for row in existing}
    merged.update({_key(row): row for row in incoming})
    rows = sorted(merged.values(), key=_key)
    for index, row in enumerate(rows):
        for other in rows[:index]:
            if (row['media_id'] == other['media_id'] and
                    row['period_start'] <= other['period_end'] and other['period_start'] <= row['period_end']):
                raise ValueError(f'Reporting periods overlap for Reel {row["media_id"]}; import matching revisions or disjoint periods')
    return rows


def build_report(records, published):
    posts = {str(post.get('facebook_id')): post for post in published if post.get('facebook_id')}
    reels = []
    for row in records:
        post = posts.get(row['media_id'], {})
        views, earnings = row.get('qualified_views'), row.get('content_earnings')
        rate = (Decimal(earnings) * 1000 / views).quantize(Decimal('0.0001')) if views and earnings is not None else None
        reels.append({**row, 'title': post.get('title'), 'category': post.get('category'),
                      'experiment': post.get('experiment'),
                      'content_earnings_per_1000_qualified_views': str(rate) if rate is not None else None,
                      'rate_definition': 'Content Monetization earnings / qualified views × 1000, same exported period and currency.'})
    return {'schema_version': 1, 'reels': reels,
            'limitations': ['Imported dashboard amounts may be estimated or revised; these are not payout statements.',
                           'Stars counts are never converted to earnings; import reported Stars earnings separately.',
                           'No pooling across currencies, periods or audience observation windows.']}
