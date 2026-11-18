"""Structured findings from Bot Office reports, shared with the rest of the system.

Bots end each report with one ``FINDINGS_JSON:`` line. Only that line is machine-read;
the prose stays advisory. Everything here treats model output as untrusted data:
fields are validated, sanitized and capped, and each consumer re-checks what it uses.

Consumers:
- WhatsApp: a new active failure from an Operations monitor report alerts the owner.
- Operator agent: an active failure seen in two consecutive Operations reports becomes
  an issue lead (src/operator_agent.py guardrails still apply).
- Strategist and growth agent: suspect metrics are masked only when the numbers
  themselves confirm the anomaly (see ``mask_suspect_metrics``).
"""
from __future__ import annotations

import copy
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

MARKER = 'FINDINGS_JSON:'
FILE = 'findings.json'
CHANNELS = ('histold', 'shotsnip')
PLATFORMS = ('fb', 'ig', 'yt')
MAX_FAILURES = 5
MAX_SUSPECTS = 40
SUSPECT_TTL = timedelta(days=14)
# A flagged platform metric is masked only if it is near zero while another platform
# shows real reach for the same video; otherwise the bot's suspicion is ignored.
SUSPECT_MAX_VIEWS = 10
OTHER_MIN_VIEWS = 50

PROMPT = (
    '\nAfter the report, add exactly one final line that starts with ' + MARKER + ' followed by compact JSON: '
    '{"active_failures":[{"key":"short-kebab-id","title":"one line","evidence":"what in the snapshot shows it"}],'
    '"suspect_metrics":[{"video_id":"the yt_id from the evidence","platform":"fb|ig|yt","reason":"one line"}]}. '
    'active_failures lists only problems the evidence shows are still happening now, not historical errors; '
    'use the same key for the same problem across reports. suspect_metrics lists per-video platform numbers '
    'that look like a pipeline or export fault rather than real audience response. Use [] when there are none.'
)


def _clean(value, limit: int) -> str:
    text = re.sub(r'[\x00-\x1f\x7f]+', ' ', str(value or ''))
    return ' '.join(text.split())[:limit]


def parse(text: str) -> tuple[str, dict | None]:
    """Split a report into (readable text, validated findings or None)."""
    lines = (text or '').rstrip().splitlines()
    for i in range(len(lines) - 1, -1, -1):
        line = lines[i].strip().strip('`').strip()
        if not line.startswith(MARKER):
            continue
        # Keep text after the marker too (the worker appends the provider line).
        body = '\n'.join(lines[:i] + lines[i + 1:]).rstrip()
        try:
            raw = json.loads(line[len(MARKER):].strip())
        except ValueError:
            return body, None
        return body, validate(raw)
    return text, None


def validate(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    failures, seen = [], set()
    for item in raw.get('active_failures') or []:
        if not isinstance(item, dict):
            continue
        key = re.sub(r'[^a-z0-9]+', '-', str(item.get('key') or item.get('title') or '').lower()).strip('-')[:60]
        title = _clean(item.get('title'), 120)
        if key and title and key not in seen:
            seen.add(key)
            failures.append({'key': key, 'title': title, 'evidence': _clean(item.get('evidence'), 400)})
    suspects, pairs = [], set()
    for item in raw.get('suspect_metrics') or []:
        if not isinstance(item, dict):
            continue
        video, platform = str(item.get('video_id') or ''), str(item.get('platform') or '').lower()
        if re.fullmatch(r'[A-Za-z0-9_-]{6,20}', video) and platform in PLATFORMS and (video, platform) not in pairs:
            pairs.add((video, platform))
            suspects.append({'video_id': video, 'platform': platform, 'reason': _clean(item.get('reason'), 200)})
    return {'active_failures': failures[:MAX_FAILURES], 'suspect_metrics': suspects[:MAX_SUSPECTS]}


def load(directory) -> dict:
    try:
        data = json.loads((Path(directory) / FILE).read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(directory, data: dict) -> None:
    path = Path(directory) / FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + str(os.getpid()) + '.tmp')
    temp.write_text(json.dumps(data, indent=2), encoding='utf-8')
    os.replace(temp, path)


def record(directory, channel: str, bot: str, task: str, findings: dict, now: datetime | None = None) -> list[dict]:
    """Store a report's findings. Returns active failures that are new in this report."""
    if channel not in CHANNELS:
        raise ValueError('Unknown channel')
    now = now or datetime.now(timezone.utc)
    stamp = now.isoformat()
    data = load(directory)
    state = data.setdefault('channels', {}).setdefault(channel, {})
    new = []
    if bot == 'operations':
        # The Operations monitor owns active failures: absent from its latest report means resolved.
        previous = state.get('active_failures') or {}
        current = {}
        for item in findings['active_failures']:
            old = previous.get(item['key'])
            current[item['key']] = {**item, 'first_seen': old['first_seen'] if old else stamp, 'last_seen': stamp,
                                    'streak': old['streak'] + 1 if old else 1, 'task': task}
            if not old:
                new.append(current[item['key']])
        state['active_failures'] = current
        state['operations_report_at'] = stamp
    suspects = {(s['video_id'], s['platform']): s for s in state.get('suspect_metrics') or []
                if now - datetime.fromisoformat(s['reported']) < SUSPECT_TTL}
    for item in findings['suspect_metrics']:
        suspects[(item['video_id'], item['platform'])] = {**item, 'reported': stamp, 'bot': bot}
    state['suspect_metrics'] = sorted(suspects.values(), key=lambda s: s['reported'], reverse=True)[:MAX_SUSPECTS * 3]
    data['updated'] = stamp
    _save(directory, data)
    return new


def known_keys(directory, channel: str) -> str:
    """Prompt suffix listing open failure keys, so the same problem keeps the same key."""
    state = (load(directory).get('channels') or {}).get(channel) or {}
    items = [k + ' (' + v.get('title', '')[:80] + ')' for k, v in (state.get('active_failures') or {}).items()]
    return (' Currently open failure keys, reuse them if the same problem is still active: ' + '; '.join(items) + '.') if items else ''


def persistent_failures(directory, channel: str, now: datetime | None = None,
                        min_streak: int = 2, max_age: timedelta = timedelta(hours=3)) -> list[dict]:
    """Active failures confirmed by consecutive Operations reports, from a recent report."""
    now = now or datetime.now(timezone.utc)
    state = (load(directory).get('channels') or {}).get(channel) or {}
    out = []
    for item in (state.get('active_failures') or {}).values():
        try:
            fresh = now - datetime.fromisoformat(item['last_seen']) <= max_age
        except (KeyError, TypeError, ValueError):
            continue
        if fresh and int(item.get('streak') or 0) >= min_streak:
            out.append(item)
    return out


def mask_suspect_metrics(directory, channel: str, index: list[dict], best: dict,
                         now: datetime | None = None) -> tuple[dict, list[dict]]:
    """Copy of ``best`` with bot-flagged metrics removed where the data confirms the anomaly.

    A masked metric becomes missing (unavailable), never zero, so scoring falls back to the
    video's other platforms.
    """
    now = now or datetime.now(timezone.utc)
    state = (load(directory).get('channels') or {}).get(channel) or {}
    entries = {e.get('video_id'): e for e in index if e.get('video_id')}
    ids = {'yt': 'video_id', 'ig': 'instagram_id', 'fb': 'facebook_id'}
    out, masked = copy.deepcopy(best), []
    for item in state.get('suspect_metrics') or []:
        try:
            if now - datetime.fromisoformat(item['reported']) >= SUSPECT_TTL:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        entry, platform = entries.get(item.get('video_id')), item.get('platform')
        if not entry or platform not in PLATFORMS:
            continue
        key = str(entry.get(ids[platform]))
        views = (out.get(platform, {}).get(key) or {}).get('views')
        others = [(out.get(p, {}).get(str(entry.get(ids[p]))) or {}).get('views') for p in PLATFORMS if p != platform]
        if views is None or views > SUSPECT_MAX_VIEWS or max([o for o in others if o is not None], default=0) < OTHER_MIN_VIEWS:
            continue
        out[platform].pop(key, None)
        masked.append({'video_id': item['video_id'], 'platform': platform, 'views': views, 'reason': item.get('reason', '')})
    return out, masked
