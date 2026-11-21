"""Bot workspace in Studio HQ: a read-only view of the existing bot office and validated owner actions.

The original dashboard's Workspace stays the only writer; Studio HQ reads its SQLite database
read-only and sends every change to /api/bots/action, which validates it again.
"""
from __future__ import annotations

import ast
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3

CHANNELS = ('histold', 'shotsnip')
INTERVALS = (0, 3600, 21600, 86400)
ACTIONS = ('assign', 'cancel', 'retry', 'choose_decision', 'dismiss_decision', 'reopen_decision',
           'report_options', 'approve_problem', 'verify_problem', 'memory', 'routine')
IDENTITY = re.compile(r'[0-9a-f]{32}')
REPORT_CHARS = 8000


def bot_list():
    """BOTS from src/bot_workspace.py without importing it (it needs packages this Python may lack)."""
    source = (Path(__file__).resolve().parents[1]/'src/bot_workspace.py').read_text(encoding='utf-8')
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(getattr(t, 'id', None) == 'BOTS' for t in node.targets):
            return ast.literal_eval(node.value)
    return []


def stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if isinstance(value, (int, float)) else None


def snapshot(config):
    key = config.get('dashboard_control_key')
    if not isinstance(key, str):
        return None
    directory = Path(key).parent/'bot-workspace'
    database = directory/'tasks.sqlite'
    if not database.is_file():
        return None
    bots = bot_list()
    try:
        from src import bot_findings
    except ImportError:
        bot_findings = None
    result = {'scope': 'Analysis and recommendations. Bots never publish or run code from Studio HQ.', 'channels': {}}
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
        db.row_factory = sqlite3.Row
        for channel in CHANNELS:
            tasks = []
            for index, row in enumerate(db.execute('SELECT * FROM tasks WHERE channel=? ORDER BY created DESC LIMIT 30', (channel,))):
                report = row['result'] or ''
                tasks.append({'id': row['id'], 'bot': row['bot'], 'engine': row['engine'], 'status': row['status'],
                              'stage': (row['stage'] or '')[:200], 'instruction': (row['instruction'] or '')[:600],
                              'created': stamp(row['created']), 'updated': stamp(row['updated']),
                              'error': (row['error'] or '')[:300] or None, 'has_report': bool(report),
                              'report': report[:REPORT_CHARS] if index < 15 else report[:400],
                              'events': [{'at': stamp(e['at']), 'note': (e['note'] or '')[:160]} for e in
                                         db.execute('SELECT at,note FROM events WHERE task=? ORDER BY at DESC LIMIT 5', (row['id'],))]})
            decisions = []
            for row in db.execute("SELECT * FROM decisions WHERE channel=? ORDER BY CASE WHEN status='pending' THEN 0 ELSE 1 END, updated DESC LIMIT 20", (channel,)):
                try:
                    proposal = json.loads(row['payload'])
                except ValueError:
                    continue
                decisions.append({'id': row['id'], 'bot': row['bot'], 'status': row['status'], 'selected': row['selected'],
                                  'version': row['updated'], 'updated': stamp(row['updated']),
                                  'title': str(proposal.get('title', ''))[:200], 'summary': str(proposal.get('summary', ''))[:600],
                                  'evidence': str(proposal.get('evidence', ''))[:1500],
                                  'success_metric': str(proposal.get('success_metric', ''))[:300],
                                  'options': [{'id': str(o.get('id')), 'label': str(o.get('label', ''))[:200], 'plan': str(o.get('plan', ''))[:600]}
                                              for o in proposal.get('options', []) if isinstance(o, dict)]})
            problems = []
            if bot_findings:
                try:
                    for item in bot_findings.problems(directory, directory.parent/'operator', channel):
                        problems.append({'key': item['key'], 'title': str(item.get('title', ''))[:200], 'plain': str(item.get('plain', ''))[:400],
                                         'evidence': str(item.get('evidence', ''))[:400], 'last_seen': item.get('last_seen'),
                                         'fix': {'state': item['fix'].get('state'), 'label': str(item['fix'].get('label', ''))[:200]}})
                except (OSError, ValueError, KeyError):
                    pass
            result['channels'][channel] = {
                'bots': [{k: b[k] for k in ('id', 'name', 'role', 'mission')} for b in bots if b.get('scope') in ('both', channel)],
                'tasks': tasks,
                'notes': {r['bot']: r['text'] for r in db.execute('SELECT bot,text FROM notes WHERE channel=?', (channel,))},
                'routines': [{'bot': r['bot'], 'engine': r['engine'], 'instruction': (r['instruction'] or '')[:600],
                              'interval': r['interval'], 'next_at': stamp(r['next_at'])}
                             for r in db.execute('SELECT * FROM routines WHERE channel=?', (channel,))],
                'decisions': decisions, 'problems': problems,
            }
    return result


def check(body):
    """Shape checks before queueing; the bot workspace validates everything again when it runs."""
    action, channel = body.get('bot_action'), body.get('channel')
    if action not in ACTIONS or channel not in CHANNELS:
        raise ValueError('Choose a bot action for Histold or Shotsnip.')
    payload = {'action': action, 'channel': channel}
    if action in ('assign', 'memory', 'routine'):
        bot = body.get('bot')
        if not isinstance(bot, str) or bot not in {b['id'] for b in bot_list()}:
            raise ValueError('Choose a bot.')
        payload['bot'] = bot
    if action in ('assign', 'routine'):
        instruction = body.get('instruction', '')
        if not isinstance(instruction, str) or len(instruction) > 4000:
            raise ValueError('Keep the assignment under 4000 characters.')
        if body.get('engine', 'analyst') not in ('analyst', 'superbot'):
            raise ValueError('Choose the analyst or Superbot engine.')
        payload.update(instruction=instruction.strip(), engine=body.get('engine', 'analyst'))
    if action == 'routine':
        if type(body.get('interval')) is not int or body['interval'] not in INTERVALS:
            raise ValueError('Choose off, hourly, every 6 hours, or daily.')
        payload['interval'] = body['interval']
    if action == 'memory':
        text = body.get('text', '')
        if not isinstance(text, str) or len(text) > 6000:
            raise ValueError('Keep bot notes under 6000 characters.')
        payload['text'] = text
    if action in ('cancel', 'retry', 'choose_decision', 'dismiss_decision', 'reopen_decision', 'report_options'):
        if not isinstance(body.get('id'), str) or not IDENTITY.fullmatch(body['id']):
            raise ValueError('Choose an item.')
        payload['id'] = body['id']
    if action == 'choose_decision':
        if not isinstance(body.get('option'), str) or not body['option']:
            raise ValueError('Choose one of the listed options.')
        payload['option'] = body['option']
    if action in ('choose_decision', 'dismiss_decision') and isinstance(body.get('version'), (int, float)):
        payload['version'] = body['version']
    if action in ('approve_problem', 'verify_problem'):
        if not isinstance(body.get('key'), str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,80}', body['key']):
            raise ValueError('Choose a problem.')
        payload['key'] = body['key']
    return payload


def perform(config, body):
    from .dashboard_controls import owner_request
    reply = owner_request(config, '/api/bots/action', check(body), timeout=30)
    return str(reply.get('message') or 'Bot workspace updated.')[:300]
