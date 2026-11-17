#!/usr/bin/env python3
"""Private audience geography. Viewer views, Page likes and IG followers stay separate."""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.config import load_config
from scripts.refresh_private_earnings import graph_get, MONEY_SCOPE, READ_SCOPE


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, required=True)
    args = parser.parse_args()
    root = args.data_root
    cfg = load_config()
    report = {'generated': datetime.now(timezone.utc).isoformat(), 'platforms': {}}
    end = datetime.now(timezone.utc).date() - timedelta(days=2)
    start = end - timedelta(days=27)
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        credentials = Credentials.from_authorized_user_file(str(root/'secrets/earnings-token.json'), [MONEY_SCOPE, READ_SCOPE])
        if not credentials.valid:
            credentials.refresh(Request())
        channels = build('youtube', 'v3', credentials=credentials).channels().list(part='id,statistics', mine=True).execute().get('items', [])
        if not channels or channels[0]['id'] != cfg.get('youtube.expected_channel_id'):
            raise ValueError('Channel mismatch')
        result = build('youtubeAnalytics', 'v2', credentials=credentials).reports().query(
            ids='channel==MINE', startDate=str(start), endDate=str(end), dimensions='country',
            metrics='views', maxResults=250).execute()
        report['platforms']['YouTube'] = {'status': 'collected', 'basis': 'views by viewer country',
            'requested_start': str(start), 'requested_end': str(end),
            'countries': {r[0]: r[1] for r in result.get('rows', [])},
            'note': 'Report may omit suppressed countries or recent unprocessed days; percentages use returned rows.'}
        daily = build('youtubeAnalytics', 'v2', credentials=credentials).reports().query(
            ids='channel==MINE', startDate=str(start), endDate=str(end), dimensions='day', sort='day',
            metrics='views,subscribersGained,subscribersLost,averageViewDuration,averageViewPercentage').execute()
        report['youtube_daily'] = {'columns': [h['name'] for h in daily.get('columnHeaders', [])], 'rows': daily.get('rows', []),
                                   'note': 'Actual daily report; retention percentages are not pooled across days.'}
        report['subscriber_snapshot'] = channels[0].get('statistics', {})
    except Exception as exc:
        report['platforms']['YouTube'] = {'status': 'unavailable', 'reason': type(exc).__name__}
    token_file = root.parent/'secrets/meta-earnings-token.txt'
    token = os.environ.get('META_ACCESS_TOKEN') or (token_file.read_text().strip() if token_file.exists() else None)
    if token:
        from src.meta_uploader import _page_access_token
        version, page = cfg.get('meta.api_version', 'v21.0'), str(cfg.get('meta.page_id'))
        try:
            page_token = _page_access_token(token, page, version)
            response = graph_get(page+'/insights', page_token, version, metric='page_fans_country', period='day', date_preset='last_7d')
            rows = [v for item in response.get('data', []) for v in item.get('values', [])]
            latest = max(rows, key=lambda r: r.get('end_time', ''), default={})
            countries = latest.get('value')
            report['platforms']['Facebook'] = {'status': 'collected' if isinstance(countries, dict) and countries else 'unavailable',
                'basis': 'Page likes by country (not video views)', 'countries': countries if isinstance(countries, dict) else {},
                'as_of': latest.get('end_time'), 'note': 'Meta reports at most 45 countries.', 'reason': response.get('message')}
        except Exception as exc:
            report['platforms']['Facebook'] = {'status': 'unavailable', 'reason': type(exc).__name__}
        ig = str(cfg.get('meta.ig_user_id', ''))
        response = graph_get(ig+'/insights', token, version, metric='follower_demographics', period='lifetime',
                             metric_type='total_value', breakdown='country', timeframe='this_month')
        countries = {}
        for item in response.get('data', []):
            for breakdown in item.get('total_value', {}).get('breakdowns', []):
                for row in breakdown.get('results', []):
                    dimensions = row.get('dimension_values') or []
                    if dimensions and isinstance(row.get('value'), (int, float)):
                        countries[dimensions[0]] = row['value']
        report['platforms']['Instagram'] = {'status': 'collected' if countries else 'unavailable',
            'basis': 'followers by country (not Reel views)', 'countries': countries, 'reason': response.get('message'),
            'note': 'Demographics may be unavailable for small or ineligible audiences.'}
    path = root/'data/private/audience.json'
    path.parent.mkdir(exist_ok=True, parents=True)
    path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('Audience collection:', {k: v['status'] for k, v in report['platforms'].items()})


if __name__ == '__main__':
    main()
