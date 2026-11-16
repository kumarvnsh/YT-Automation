#!/usr/bin/env python3
"""Read-only earnings access probe. Only encrypted results leave the Actions runner."""
import base64
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import yaml


def main():
    root = Path(__file__).resolve().parent.parent
    cfg = yaml.safe_load((root / 'config.yaml').read_text(encoding='utf-8'))
    token = os.environ['META_ACCESS_TOKEN']
    page = str(cfg['meta']['page_id'])
    configured = str(cfg['meta'].get('api_version', 'v21.0'))
    report = {'generated': datetime.now(timezone.utc).isoformat(), 'page_id': page,
              'configured_version': configured, 'checks': [], 'permissions': None}

    def get(version, node, credential, **params):
        try:
            response = requests.get(f'https://graph.facebook.com/{version}/{node}',
                                    headers={'Authorization': f'Bearer {credential}'}, params=params, timeout=35)
            data = response.json()
            if not response.ok or data.get('error'):
                error = data.get('error') or {}
                message = str(error.get('message', response.status_code)).replace(token, '[redacted]')
                message = message.replace(credential, '[redacted]')
                return {'status': 'error', 'code': error.get('code'), 'subcode': error.get('error_subcode'),
                        'message': message[:600]}
            # Never retain paging URLs: those can contain tokens.
            return {'status': 'ok', 'data': data.get('data'),
                    **({'access_token': data['access_token']} if 'access_token' in data else {})}
        except Exception as exc:
            return {'status': 'network_error', 'type': type(exc).__name__}

    permission = get(configured, 'me/permissions', token)
    report['permissions'] = permission
    index = json.loads((root / 'data/published_index.json').read_text())
    now = datetime.now(timezone.utc)
    videos = []
    for entry in reversed(index):
        if entry.get('channel', 'histold') != 'histold' or not entry.get('facebook_id'):
            continue
        published = datetime.fromisoformat(entry['published_at'].replace('Z', '+00:00'))
        video = str(entry['facebook_id'])
        if timedelta(hours=48) <= now-published <= timedelta(days=28) and video not in videos:
            videos.append(video)
        if len(videos) == 3:
            break
    for version in dict.fromkeys([configured, 'v27.0']):
        credentials = get(version, page, token, fields='access_token')
        page_token = credentials.pop('access_token', None)
        if not page_token:
            report['checks'].append({'version': version, 'scope': 'page_token', **credentials})
            print(f'{version}: page-token access unavailable', flush=True)
            continue
        for metric in ('content_monetization_earnings', 'monetization_approximate_earnings',
                       'creator_monetization_qualified_views'):
            result = get(version, f'{page}/insights', page_token, metric=metric,
                         period='day', date_preset='last_28d')
            report['checks'].append({'version': version, 'scope': 'page', 'metric': metric, **result})
            print(f'{version} page {metric}: {result["status"]}' +
                  (f' (code {result.get("code")})' if result.get('code') else ''), flush=True)
        for video in videos:
            for metric in ('creator_monetization_qualified_views', 'total_video_ad_break_earnings'):
                result = get(version, f'{video}/video_insights', page_token, metric=metric, period='lifetime')
                report['checks'].append({'version': version, 'scope': 'video', 'video_id': video,
                                         'metric': metric, **result})
                print(f'{version} video {metric}: {result["status"]}' +
                      (f' (code {result.get("code")})' if result.get('code') else ''), flush=True)

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    public_key = serialization.load_der_public_key(base64.b64decode(os.environ['PROBE_PUBLIC_KEY_B64']))
    key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    encrypted = AESGCM(key).encrypt(nonce, json.dumps(report).encode(), b'histold-meta-probe-v1')
    wrapped = public_key.encrypt(key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),
                                                 algorithm=hashes.SHA256(), label=None))
    envelope = {k: base64.b64encode(v).decode() for k, v in
                [('wrapped_key', wrapped), ('nonce', nonce), ('ciphertext', encrypted)]}
    output = root / 'output/private-meta-probe.enc.json'
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(envelope))
    print('Encrypted report ready; no raw earnings written to logs or artifacts.', flush=True)


if __name__ == '__main__':
    main()
