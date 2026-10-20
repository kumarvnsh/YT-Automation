"""Period-bound Facebook dashboard earnings. No inferred revenue or API fields."""
import csv
from datetime import date
from decimal import Decimal, InvalidOperation

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
