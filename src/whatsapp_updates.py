"""Owner-only OpenWA delivery with persistent deduplication. No chat commands or calls."""
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse, quote


def send_update(config, text, logical_id, state_path):
    if not config.get('enabled'):
        return {'status': 'disabled'}
    base = config.get('base_url', '').rstrip('/')
    parsed = urlparse(base)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Configure an HTTP(S) OpenWA base URL without embedded credentials.')
    if parsed.scheme != 'https' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('Remote OpenWA connections require HTTPS.')
    session, recipient = config.get('session_id'), config.get('recipient')
    if not session or not recipient or not config.get('api_key'):
        return {'status': 'configuration_required'}
    # One explicitly configured owner; never accept a recipient from an AI response.
    number = str(recipient).lstrip('+')
    if not number.isdigit() or not 7 <= len(number) <= 15:
        raise ValueError('Recipient must be one international phone number including country code.')
    path = Path(state_path)
    state = json.loads(path.read_text()) if path.exists() else {}
    key = hashlib.sha256((base + ':' + str(session) + ':' + number + ':' + logical_id).encode()).hexdigest()
    if key in state:
        return {'status': state[key]['status']}
    # Persist the exact body before sending. A timeout must not cause an automatic duplicate.
    state[key] = {'status': 'outcome_unknown', 'logical_id': logical_id}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding='utf-8')
    import requests
    try:
        response = requests.post(base + '/api/sessions/' + quote(str(session), safe='') + '/messages/send-text',
                                 headers={'X-API-Key': config['api_key'], 'Idempotency-Key': 'histold-' + key},
                                 json={'chatId': number + '@c.us', 'text': text}, timeout=30)
        # HTTP acceptance is not a WhatsApp delivery/read receipt.
        status = 'accepted' if response.ok else 'request_failed_check_gateway'
    except requests.RequestException:
        status = 'outcome_unknown'
    state[key]['status'] = status
    path.write_text(json.dumps(state, indent=2), encoding='utf-8')
    return {'status': status}
