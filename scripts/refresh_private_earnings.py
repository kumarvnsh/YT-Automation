#!/usr/bin/env python3
"""Read-only Meta/YouTube earnings collection. Financial reports stay in data/private/."""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.config import load_config, base_dir
from scripts.export_meta_analytics import GRAPH

MONEY_SCOPE = 'https://www.googleapis.com/auth/yt-analytics-monetary.readonly'
READ_SCOPE = 'https://www.googleapis.com/auth/youtube.readonly'


def graph_get(node, token, version, **params):
    import requests
    try:
        response = requests.get(f'{GRAPH}/{version}/{node}', headers={'Authorization': f'Bearer {token}'},
                                params=params, timeout=30)
        payload = response.json()
        if not response.ok or payload.get('error'):
            error = payload.get('error') or {}
            return {'status': 'error', 'code': error.get('code'),
                    'message': str(error.get('message', 'Request rejected')).replace(token, '[redacted]')[:300]}
        # Drop paging URLs, which can contain credentials.
        return {'status': 'ok', 'data': payload.get('data', []),
                'after': (payload.get('paging', {}).get('cursors', {}).get('after')
                          if payload.get('paging', {}).get('next') else None)}
    except (requests.RequestException, ValueError):
        return {'status': 'fetch_failed', 'message': 'Request failed; retry next collection.'}


def collect_meta(cfg, root):
    token = os.environ.get('META_EARNINGS_ACCESS_TOKEN') or os.environ.get('META_ACCESS_TOKEN')
    credential_path = root.parent / 'secrets/meta-earnings-token.txt'
    if not token and credential_path.exists():
        token = credential_path.read_text(encoding='utf-8-sig').strip()
    if not token:
        return {'status': 'credentials_missing', 'message': 'Meta token is available in Actions only; no local credential configured.'}
    from src.meta_uploader import _page_access_token
    ver = cfg.get('meta.api_version', 'v21.0')
    page = str(cfg.get('meta.page_id', ''))
    try:
        page_token = _page_access_token(token, page, ver)
    except Exception:
        return {'status': 'access_denied', 'message': 'Unable to obtain the configured Page token.'}
    report = {'generated': datetime.now(timezone.utc).isoformat(), 'configured_version': ver, 'checks': []}
    for scope, metric, period, extra in [
        ('page', 'monetization_approximate_earnings', 'day', {}),
        ('page_aggregate', 'monetization_approximate_earnings', 'days_28', {}),
        ('page_breakdown', 'content_monetization_earnings', 'day', {'breakdown': 'earning_source'}),
    ]:
        result = graph_get(f'{page}/insights', page_token, ver, metric=metric, period=period,
                           date_preset='last_28d', **extra)
        report['checks'].append({'scope': scope, 'version': ver, 'metric': metric, **result})
    index_path = root / 'data/published_index.json'
    index = json.loads(index_path.read_text(encoding='utf-8')) if index_path.exists() else []
    cutoff = datetime.now(timezone.utc) - timedelta(days=28)
    post_ids = {}
    cursor = None
    for _ in range(10):
        feed = graph_get(f'{page}/published_posts', page_token, ver,
                         fields='id,attachments{target{id},subattachments{target{id}}}',
                         since=int(cutoff.timestamp()), limit=100, **({'after': cursor} if cursor else {}))
        for post in feed.get('data', []):
            for attachment in post.get('attachments', {}).get('data', []):
                targets = [attachment] + attachment.get('subattachments', {}).get('data', [])
                for target in targets:
                    media = target.get('target', {}).get('id')
                    if media:
                        post_ids[str(media)] = post['id']
        cursor = feed.get('after')
        if not cursor:
            break
    seen = set()
    for entry in reversed(index):
        if entry.get('channel', 'histold') != 'histold' or not entry.get('facebook_id'):
            continue
        try:
            published = datetime.fromisoformat(entry['published_at'].replace('Z', '+00:00'))
        except (KeyError, ValueError):
            continue
        if published < cutoff:
            continue
        video_id = str(entry['facebook_id'])
        if video_id in seen:
            continue
        seen.add(video_id)
        # Resolve the Page post ID instead of assuming video IDs are post IDs.
        post_id = entry.get('facebook_post_id') or post_ids.get(video_id)
        if not post_id:
            report['checks'].append({'scope': 'post_resolution', 'version': ver, 'video_id': video_id,
                                     'status': 'unmapped', 'message': 'No Page post attachment matched this video; no post ID was guessed.'})
            continue
        for metric in ('monetization_approximate_earnings', 'content_monetization_earnings', 'creator_monetization_qualified_views'):
            result = graph_get(f'{post_id}/insights', page_token, ver, metric=metric, period='lifetime')
            report['checks'].append({'scope': 'post', 'version': ver, 'video_id': video_id,
                                     'post_id': post_id, 'metric': metric, **result})
    if not any(c['scope'] == 'page' and c['status'] == 'ok' and c.get('data') for c in report['checks']):
        return {'status': 'access_or_metric_error', 'message': 'Page earnings unavailable; retained the previous successful snapshot.'}
    return {'status': 'collected', 'report': report}


def collect_youtube(cfg, root, authorize=False):
    token_path = root / 'secrets/earnings-token.json'
    scopes = [MONEY_SCOPE, READ_SCOPE]
    if authorize:
        from google_auth_oauthlib.flow import InstalledAppFlow
        client = Path(os.environ.get('YOUTUBE_CLIENT_SECRET_FILE', str(root / 'secrets/client_secret.json')))
        if not client.is_absolute():
            client = base_dir() / client
        flow = InstalledAppFlow.from_client_secrets_file(str(client), scopes)
        creds = flow.run_local_server(port=0, prompt='consent', access_type='offline')
    elif not token_path.exists():
        return {'status': 'authorization_required', 'message': 'Run refresh_private_earnings.py --authorize-youtube to sign in. Upload credentials are unchanged.'}
    else:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        creds = Credentials.from_authorized_user_file(str(token_path), scopes)
        if not creds.valid and creds.refresh_token:
            creds.refresh(Request())
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding='utf-8')
    from googleapiclient.discovery import build
    channels = build('youtube', 'v3', credentials=creds).channels().list(part='id', mine=True).execute().get('items', [])
    expected = cfg.get('youtube.expected_channel_id')
    if not channels or (expected and channels[0]['id'] != expected):
        return {'status': 'channel_mismatch', 'message': 'Earnings account is not the configured channel.'}
    end = datetime.now(timezone.utc).date() - timedelta(days=1)
    start = end - timedelta(days=27)
    response = build('youtubeAnalytics', 'v2', credentials=creds).reports().query(
        ids='channel==MINE', startDate=str(start), endDate=str(end), dimensions='day',
        metrics='estimatedRevenue', currency='USD', sort='day').execute()
    return {'status': 'collected', 'currency': 'USD', 'estimated_not_payout': True,
            'requested_start': str(start), 'requested_end': str(end),
            'latest_returned_day': max((r[0] for r in response.get('rows', [])), default=None), 'data': response}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config')
    parser.add_argument('--data-root', type=Path)
    parser.add_argument('--authorize-youtube', action='store_true')
    args = parser.parse_args()
    cfg = load_config(args.config)
    root = (args.data_root or base_dir()).resolve()
    private = root / 'data/private'
    private.mkdir(parents=True, exist_ok=True)
    statuses = {'checked': datetime.now(timezone.utc).isoformat()}
    for platform, collector in [('facebook', lambda: collect_meta(cfg, root)),
                                ('youtube', lambda: collect_youtube(cfg, root, args.authorize_youtube))]:
        try:
            result = collector()
        except Exception as exc:
            # Exception strings can contain request URLs or secrets. Record only the exception type.
            result = {'status': 'fetch_failed', 'message': f'{type(exc).__name__}; check authorization and API availability.'}
        statuses[platform] = {k: v for k, v in result.items() if k not in ('report', 'data')}
        if result['status'] == 'collected':
            target = private / ('facebook-api-live.json' if platform == 'facebook' else 'youtube-earnings.json')
            temp = target.with_suffix('.tmp')
            temp.write_text(json.dumps(result.get('report', result), indent=2), encoding='utf-8')
            temp.replace(target)
        print(f'{platform} earnings: {result["status"]}')
    (private / 'earnings-status.json').write_text(json.dumps(statuses, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
