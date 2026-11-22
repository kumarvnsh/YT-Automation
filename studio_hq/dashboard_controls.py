"""Phase 2 command adapter for the existing queue and maintenance workers."""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from contextlib import closing, ExitStack
from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid

from .archive import (archive_tick, atomic_json, checked_manifest, read_json, restore,
                      stamp, within, worker_lock)

IDENTITY = re.compile(r'[a-f0-9-]{36}')
JOB_ID = re.compile(r'[A-Za-z0-9_-]{1,100}')
CONFIRMED = {'uploaded', 'facebook_published'}
CADENCE_MINUTES = (60, 120, 180, 240, 360, 480, 720, 1440)
TARGET_ID = re.compile(r'[A-Za-z0-9_.@-]{1,80}')
REVISION = re.compile(r'[0-9a-f]{64}')
TARGET_ACTIONS = ('target_toggle', 'target_add')
AGENT_ACTIONS = ('agent_learning', 'agent_review')
SHOTSNIP_OWNER_ACTIONS = TARGET_ACTIONS + AGENT_ACTIONS
ASSISTANT_ACTIONS = ('assistant_ask', 'assistant_apply')
LIVE_ACTIONS = ('live_run_now', 'live_stop_after_stage', 'shotsnip_commentary', 'shotsnip_watch_worker', 'manual_spacing')
QUEUE_ACTIONS = ('move_earlier', 'move_later', 'prioritize', 'schedule')
HISTOLD_OWNER_ACTIONS = ('histold_strategy', 'histold_research', 'ai_settings')
BOT_ACTIONS = ('bot_action', 'draft_preview')
from .histold_live import ACTIONS as HISTOLD_LIVE_ACTIONS
COMMENTARY_MODES = ('off', 'intro', 'full')
ACTIVE = {'queued', 'queued_publish', 'running', 'publishing', 'ready', 'partial', 'attention'}


class WorkerBusy(Exception):
    pass


def config_root(config):
    return Path(config['roots']['shotsnip']).absolute()


def manual_module(root):
    sys.path.insert(0, str(root))
    import manual_jobs
    return manual_jobs


def ensure_receipts(db):
    db.execute('''CREATE TABLE IF NOT EXISTS studio_hq_requests (
                  id TEXT PRIMARY KEY, digest TEXT NOT NULL, response TEXT NOT NULL)''')


def replay(db, identity, request_digest):
    row = db.execute('SELECT digest,response FROM studio_hq_requests WHERE id=?', (identity,)).fetchone()
    if row:
        if row[0] != request_digest:
            raise ValueError('This request ID belongs to a different action.')
        return json.loads(row[1])
    return None


def receipt(db, identity, request_digest, response):
    db.execute('INSERT INTO studio_hq_requests VALUES (?,?,?)',
               (identity, request_digest, json.dumps(response)))
    return response


def request_digest(body):
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def preview_key(options):
    key = dict(options)
    key.pop('ranges', None)
    key['platforms'] = sorted(key['platforms'])
    return json.dumps(key, sort_keys=True)


def manual_command(config, body):
    root = config_root(config)
    module = manual_module(root)
    action = body['action']
    request_id = body['request_id']
    digest = request_digest(body)
    with module.connection(root) as db:
        db.execute('BEGIN IMMEDIATE')
        ensure_receipts(db)
        previous = replay(db, request_id, digest)
        if previous:
            return previous
        if action == 'create_preview':
            mode = body.get('mode')
            if mode not in ('replay', 'exact'):
                raise ValueError('Choose a replay or exact timestamps.')
            targets = body.get('platforms')
            if not isinstance(targets, list) or not targets or any(p not in ('youtube', 'facebook') for p in targets):
                raise ValueError('Choose at least one publishing destination.')
            options = module.validate({
                'mode': mode, 'url': body.get('url', ''), 'ranges': body.get('ranges', ''),
                'platforms': targets, 'privacy': 'public', 'publish': False,
            })
            requested = [dict(options, window=window) for window in options['ranges'] or [None]]
            existing = {}
            for row in db.execute('SELECT id,status,options FROM jobs'):
                if row['status'] in ACTIVE:
                    existing[preview_key(json.loads(row['options']))] = row['id']
            ids = []
            for item in requested:
                key = preview_key(item)
                if key in existing:
                    ids.append(existing[key])
                else:
                    new_id = module.enqueue_job(db, item)
                    existing[key] = new_id
                    ids.append(new_id)
            result = {'message': 'Shotsnip preview is in the existing manual queue.', 'job_ids': ids}
        else:
            job_id = body.get('job_id', '')
            revision = body.get('revision')
            if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id) or type(revision) not in (int, float):
                raise ValueError('Open the current job before sending a command.')
            row = module.decode(db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())
            if not row or row['updated'] != revision:
                raise ValueError('The job changed. Refresh and review it again.')
            if action in ('publish', 'retry'):
                publication = row['result'].get('publication') or {}
                ledger = read_json(within(root/'work/scheduler/state.json', root))
                source = row['result'].get('file')
                match = [item for item in ledger['uploads'] if source and item.get('file') and Path(item['file']) == Path(source)]
                if publication or match:
                    raise ValueError('The job has a publication record. Review reconciliation in the existing dashboard.')
                if action == 'publish':
                    base = root/'work/manual'/job_id
                    if not source or not within(source, base).is_file() or Path(source).suffix.lower() != '.mp4':
                        raise ValueError('The reviewed local video is unavailable.')
                    targets = row['options'].get('platforms', [])
                    if not targets or set(targets) - {'youtube', 'facebook'} or body.get('platforms') != targets:
                        raise ValueError('Publishing destinations changed. Review the current job again.')
            value = None
            if action == 'schedule':
                value = body.get('not_before')
                if value is not None and (type(value) not in (int, float)):
                    raise ValueError('Choose a publish time.')
            if action in ('schedule',):
                module.control_job(db, job_id, action, expected=revision, value=value)
            else:
                module.control_job(db, job_id, action, expected=revision)
            result = {'message': {
                'publish': 'Approved and queued for the existing publishing worker.',
                'cancel': 'Cancellation recorded.',
                'retry': 'Processing retry queued.',
                'move_earlier': 'Moved one place earlier in the publishing queue.',
                'move_later': 'Moved one place later in the publishing queue.',
                'prioritize': 'Moved to the front of the publishing queue.',
                'schedule': 'Publish time cleared; the clip goes out in queue order.' if value is None else 'Publish time set. The clip waits until then.',
            }[action]}
        return receipt(db, request_id, digest, result)


def controls_db(config):
    directory = Path(config['state_dir'])
    db = sqlite3.connect(directory/'hub-commands.sqlite', timeout=5)
    db.row_factory = sqlite3.Row
    db.execute('''CREATE TABLE IF NOT EXISTS commands (
                  id TEXT PRIMARY KEY, action TEXT NOT NULL, target TEXT NOT NULL,
                  digest TEXT NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL,
                  message TEXT NOT NULL, updated REAL NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS command_aliases (
                  id TEXT PRIMARY KEY, digest TEXT NOT NULL, command_id TEXT NOT NULL)''')
    return db


def queue_control(config, body):
    action = body['action']
    target = ''
    if action in HISTOLD_LIVE_ACTIONS:
        from .histold_live import check
        target = check(body)
    if action == 'scheduler':
        if type(body.get('enabled')) is not bool or type(body.get('expected')) is not bool:
            raise ValueError('Refresh the scheduler status before changing it.')
        if 'end_hold' in body and type(body['end_hold']) is not bool:
            raise ValueError('Refresh the scheduler status before changing it.')
        target = str(body['enabled'])
    if action == 'live_run_now':
        if type(body.get('cancel')) is not bool:
            raise ValueError('Refresh the live status before requesting a cycle.')
        target = 'cancel' if body['cancel'] else 'run'
    if action == 'live_stop_after_stage':
        target = 'stop'
    if action == 'manual_spacing':
        if type(body.get('minutes')) is not int or not 30 <= body['minutes'] <= 480:
            raise ValueError('Choose a gap between 30 minutes and 8 hours.')
        target = str(body['minutes'])
    if action == 'shotsnip_commentary':
        if body.get('mode') not in COMMENTARY_MODES:
            raise ValueError('Choose off, intro, or full commentary.')
        target = body['mode']
    if action == 'shotsnip_watch_worker':
        if type(body.get('start')) is not bool:
            raise ValueError('Choose to start or stop the watch worker.')
        target = str(body['start'])
    if action in HISTOLD_OWNER_ACTIONS and action != 'bot_action' and body.get('channel') != 'histold':
        raise ValueError('This control belongs to Histold.')
    if action == 'histold_strategy':
        name, args = body.get('name'), body.get('args', {})
        if name not in ('pin_hours', 'unpin_hours', 'pause_option', 'boost_option', 'reset_option', 'refresh_analytics')                 or not isinstance(args, dict):
            raise ValueError('Choose a Histold strategy control.')
        try:
            import yaml
            from src import control
            from src.config import Config
            with (Path(__file__).resolve().parents[1]/'config.yaml').open(encoding='utf-8') as stream:
                args, _ = control.validate(name, args, Config(yaml.safe_load(stream) or {}))
        except ImportError:
            pass  # Without PyYAML here, the owner's own validation (always run) decides.
        body['args'] = args
        target = name + ':' + json.dumps(args, sort_keys=True)
    if action == 'histold_research':
        target = 'research'
    if action == 'bot_action':
        from .bots import check
        target = json.dumps(check(body), sort_keys=True)
    if action == 'draft_preview':
        stage, run_id = body.get('stage'), body.get('run_id')
        if (body.get('channel') != 'histold' or not isinstance(stage, str) or not re.fullmatch(r'\d{8}_\d{6}(?:_\d{6})?_(?:short|long)', stage)
                or not isinstance(run_id, str) or not re.fullmatch(r'\d{6,20}', run_id)):
            raise ValueError('Choose a Histold draft.')
        target = stage
    if action == 'ai_settings':
        mode, provider = body.get('mode'), body.get('provider')
        if mode is not None:
            if mode not in ('auto', 'claude', 'codex', 'local'):
                raise ValueError('Choose which AI answers.')
            target = 'mode:' + mode
        elif provider in ('claude', 'codex'):
            caps = {k: body.get(k) for k in ('per_5h', 'per_week') if k in body}
            if any(type(v) is not int or not 0 <= v <= 1000 for v in caps.values()) or (not caps and body.get('clear_cooldown') is not True):
                raise ValueError('Usage caps must be whole numbers from 0 to 1000.')
            target = provider + ':' + json.dumps(caps, sort_keys=True) + (':clear' if body.get('clear_cooldown') is True else '')
        else:
            raise ValueError('Choose an AI setting.')
    if action == 'scheduler_cadence':
        minutes, revision = body.get('minutes'), body.get('revision')
        if type(minutes) is not int or minutes not in CADENCE_MINUTES:
            raise ValueError('Choose one of the existing Shotsnip cadences.')
        if not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{64}', revision):
            raise ValueError('Refresh the calendar before changing cadence.')
        target = str(minutes)
    if action == 'target_toggle':
        identity, enabled, revision = body.get('id'), body.get('enabled'), body.get('revision')
        if not isinstance(identity, str) or not TARGET_ID.fullmatch(identity) or type(enabled) is not bool:
            raise ValueError('Choose a target to pause or enable.')
        if not isinstance(revision, str) or not REVISION.fullmatch(revision):
            raise ValueError('Refresh the target list before changing it.')
        target = identity + ':' + str(enabled)
    if action == 'target_add':
        from scripts.clipping_dashboard import target_url
        _, canonical = target_url(body.get('url') if isinstance(body.get('url'), str) else '')
        body['url'] = canonical
        target = canonical.lower()
    if action == 'agent_learning':
        if type(body.get('enabled')) is not bool or type(body.get('expected')) is not bool:
            raise ValueError('Refresh the agent status before changing learning.')
        target = str(body['enabled'])
    if action == 'agent_review':
        if body.get('mode') not in ('review', 'refresh'):
            raise ValueError('Choose a review or an analytics refresh.')
        target = body['mode']
    if action in ASSISTANT_ACTIONS and body.get('channel') != ('shotsnip' if body.get('scope') == 'shotsnip' else 'histold'):
        raise ValueError('The assistant answers Histold strategy questions.')
    if action == 'assistant_ask':
        from .assistant import check_request
        scope, job = check_request(body)
        target = hashlib.sha256(json.dumps([scope, job, body['question'].strip()]).encode()).hexdigest()
    if action == 'assistant_apply':
        identity, index = body.get('answer_id'), body.get('index')
        if not isinstance(identity, str) or not re.fullmatch(r'[0-9a-f]{32}', identity) or type(index) is not int:
            raise ValueError('Choose a proposal to apply.')
        target = f'{identity}:{index}'
    if action == 'restore':
        target = body.get('archive_id', '')
        if not isinstance(target, str) or not re.fullmatch(r'[a-f0-9]{24}', target):
            raise ValueError('Select a verified archive.')
    if action == 'story_draft':
        if body.get('channel') != 'histold' or body.get('format') not in ('short', 'long'):
            raise ValueError('Choose a Histold draft format.')
        target = body['channel'] + ':' + body['format']
    if action == 'story_approve':
        stage, run_id = body.get('stage'), body.get('run_id')
        if (body.get('channel') != 'histold' or not isinstance(stage, str)
                or not re.fullmatch(r'\d{8}_\d{6}(?:_\d{6})?_(?:short|long)', stage)
                or not isinstance(run_id, str) or not re.fullmatch(r'\d{1,20}', run_id)
                or body.get('privacy') not in ('private', 'unlisted', 'public')):
            raise ValueError('Choose a Histold draft and its YouTube visibility.')
        target = stage
    digest = request_digest(body)
    with closing(controls_db(config)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        previous = db.execute('''SELECT id,digest,message FROM commands WHERE id=?
                              UNION ALL SELECT commands.id,command_aliases.digest,commands.message
                              FROM command_aliases JOIN commands ON commands.id=command_aliases.command_id
                              WHERE command_aliases.id=?''', (body['request_id'], body['request_id'])).fetchone()
        if previous:
            if previous['digest'] != digest:
                raise ValueError('This request ID belongs to a different action.')
            return {'message': previous['message'], 'command_id': previous['id']}
        active = db.execute("""SELECT id,message FROM commands WHERE action=? AND target=? AND
                              (status IN ('queued','running','review') OR
                               (status='complete' AND (?='story_approve' OR (?='story_draft' AND updated>?))))
                              ORDER BY updated DESC LIMIT 1""",
                            (action, target, action, action, time.time()-3600)).fetchone()
        if active:
            db.execute('INSERT INTO command_aliases VALUES (?,?,?)', (body['request_id'], digest, active['id']))
            return {'message': active['message'], 'command_id': active['id']}
        message = 'Request queued on the Windows host.'
        db.execute('INSERT INTO commands VALUES (?,?,?,?,?,?,?,?)',
                   (body['request_id'], action, target, digest, json.dumps(body), 'queued', message, time.time()))
        return {'message': message, 'command_id': body['request_id']}


def refresh_snapshot(config):
    """Rebuild the private snapshot now, so a finished command shows without waiting for the 30-second cycle."""
    private = config.get('dashboard_private_snapshot')
    if not isinstance(private, str):
        return
    try:
        from .dashboard_snapshot import snapshot
        output = Path(private)
        previews, media = {}, {}
        data = snapshot(config, previews, include_finance=True, media=media)
        atomic_json(output.with_name('dashboard-previews.json'), previews)
        atomic_json(output.with_name('dashboard-media.json'), media)
        atomic_json(output, data)
    except Exception:  # noqa: BLE001 - the regular refresh cycle still runs; a command never fails here
        pass


def submit(config, body):
    result = route(config, body)
    refresh_snapshot(config)
    return result


def route(config, body):
    if not isinstance(body, dict) or not isinstance(body.get('request_id'), str) or not IDENTITY.fullmatch(body['request_id']):
        raise ValueError('Missing request ID.')
    if body.get('channel') != 'shotsnip' and body.get('action') in ('create_preview','publish','cancel','retry','scheduler','scheduler_cadence') + SHOTSNIP_OWNER_ACTIONS + LIVE_ACTIONS + QUEUE_ACTIONS:
        raise ValueError('This queue belongs to Shotsnip.')
    if body.get('channel') != 'histold' and body.get('action') in HISTOLD_LIVE_ACTIONS:
        raise ValueError('These controls belong to Histold.')
    action = body.get('action')
    if action in ('create_preview', 'publish', 'cancel', 'retry') + QUEUE_ACTIONS:
        return manual_command(config, body)
    if action in ('scheduler', 'scheduler_cadence', 'archive_now', 'restore', 'strategist', 'story_draft', 'story_approve') + SHOTSNIP_OWNER_ACTIONS + ASSISTANT_ACTIONS + LIVE_ACTIONS + HISTOLD_OWNER_ACTIONS + BOT_ACTIONS + HISTOLD_LIVE_ACTIONS:
        return queue_control(config, body)
    raise ValueError('Unsupported action.')


def owner_request(config, path, payload, *, timeout=15):
    """POST to the existing dashboard (the owner of these settings) and return its JSON reply."""
    key = Path(config['dashboard_control_key']).read_text(encoding='utf-8-sig').strip()
    if not re.fullmatch(r'[A-Za-z0-9_.~-]+', key):
        raise RuntimeError('Existing dashboard key is unavailable')
    conn = http.client.HTTPConnection('127.0.0.1', 8799, timeout=timeout)
    try:
        conn.request('POST', path, json.dumps(payload),
                     {'Content-Type':'application/json','Cookie':'hk='+key,'X-Histold':'1'})
        res = conn.getresponse()
        raw = res.read(256 * 1024)
    finally:
        conn.close()
    try:
        reply = json.loads(raw.decode('utf-8'))
    except ValueError:
        reply = {}
    reply = reply if isinstance(reply, dict) else {}
    if res.status in (400, 429) and isinstance(reply.get('error'), str):
        raise ValueError(reply['error'][:200])  # The owner's own validation or busy message.
    if res.status not in (200, 202):
        raise RuntimeError('Existing dashboard did not accept the request')
    return reply


def call_scheduler(config, payload, *, timeout=15):
    """Hand a change to the existing Shotsnip dashboard, which owns these files."""
    reply = owner_request(config, '/api/shotsnip/action', payload, timeout=timeout)
    return reply.get('message') if isinstance(reply.get('message'), str) else ''


def watchlist_state(root):
    raw = (root/'watchlist.json').read_bytes()
    data = json.loads(raw.decode('utf-8-sig'))
    return [row for row in data.get('creators', []) if isinstance(row, dict)], hashlib.sha256(raw).hexdigest()


def target_change(config, body):
    root = config_root(config)
    creators, revision = watchlist_state(root)
    if body['action'] == 'target_toggle':
        if revision != body['revision']:
            raise ValueError('The target list changed. Refresh and review it again.')
        target = next((row for row in creators if row.get('id') == body['id']), None)
        if target is None:
            raise ValueError('That target is no longer on the watchlist.')
        name = str(target.get('name') or body['id'])[:80]
        if (target.get('enabled') is True) is body['enabled']:
            return f"{name} is already {'enabled' if body['enabled'] else 'paused'}."
        call_scheduler(config, {'name': 'set_target', 'id': body['id'], 'enabled': body['enabled']})
        creators, _ = watchlist_state(root)
        target = next((row for row in creators if row.get('id') == body['id']), {})
        if (target.get('enabled') is True) is not body['enabled']:
            raise RuntimeError('Could not verify the target change')
        return f"{name} {'enabled' if body['enabled'] else 'paused'}. Shotsnip uses this from its next scan."
    # Resolving a YouTube channel takes the owner up to 45 seconds.
    message = call_scheduler(config, {'name': 'add_target', 'url': body['url']}, timeout=75)
    creators, _ = watchlist_state(root)
    listed = any(str(source.get('url', '')).lower() == body['url'].lower()
                 for row in creators for source in row.get('sources', []) if isinstance(source, dict))
    if not listed and 'already' not in message:
        raise RuntimeError('Could not verify the new target')
    return (message or 'Target added.')[:200]


def cadence_settings(path):
    raw = path.read_bytes()
    return json.loads(raw.decode('utf-8-sig')), hashlib.sha256(raw).hexdigest()


def scheduler_change(config, body):
    root = config_root(config)
    work = root/'work/scheduler'
    # This is the existing scheduler/manual worker byte lock. The dashboard's
    # own handler serializes its configuration writes and stays the owner.
    with ExitStack() as stack:
        try:
            stack.enter_context(worker_lock(work, 'worker.lock'))
        except OSError as exc:
            raise WorkerBusy from exc
        filename = root/'scheduler.json'
        if body['action'] == 'scheduler_cadence':
            current, revision = cadence_settings(filename)
            if not isinstance(current, dict) or revision != body['revision']:
                raise ValueError('Shotsnip schedule changed. Refresh and review it again.')
            if current.get('live_operations_batch_hold') or current.get('pending_post_minutes'):
                raise ValueError('The scheduler has an active hold or a pending cadence. Review it before making a change.')
            if current.get('scan_minutes') == body['minutes'] == current.get('post_minutes'):
                return 'Shotsnip cadence is already set to those intervals.'
            call_scheduler(config, {'name': 'set_cadence', 'minutes': body['minutes']})
            updated, _ = cadence_settings(filename)
            if updated.get('scan_minutes') != body['minutes'] or updated.get('post_minutes') != body['minutes']:
                raise RuntimeError('Could not verify the new scheduler cadence')
            return f"Shotsnip scan and posting intervals updated to {body['minutes']} minutes."
        current = read_json(filename)
        if current.get('enabled') is body['enabled']:
            return 'Shotsnip posting is already in the requested state.'
        if current.get('enabled') is not body['expected']:
            raise ValueError('Scheduler changed. Refresh and review it again.')
        end_hold = body.get('end_hold') is True
        if body['enabled']:
            ledger = read_json(work/'state.json')
            if current.get('live_operations_batch_hold') and not end_hold:
                raise ValueError('Shotsnip is on a library batch hold. Confirm ending the hold to resume now.')
            if any(row.get('status') not in CONFIRMED | {'rejected', 'quarantined'}
                   or (row.get('facebook') or {}).get('status') in ('pending', 'uncertain', 'error', 'failed')
                   for row in ledger['uploads']):
                raise ValueError('A publication result is unresolved, so live operations stay paused to avoid a duplicate post.')
        message = call_scheduler(config, {'name': 'set_scheduler', 'enabled': body['enabled'], 'end_hold': end_hold})
        updated = read_json(filename)
        if updated.get('enabled') is not body['enabled'] or (end_hold and updated.get('live_operations_batch_hold')):
            raise RuntimeError('Could not verify the scheduler change')
        if body['enabled']:
            return message or 'Shotsnip live operations resumed.'
        return 'Shotsnip live operations paused. Manual and batch clips keep their own queue.'


def live_change(config, body):
    """Run-now and stop-after-stage requests, handed to the existing Shotsnip dispatcher."""
    root = config_root(config)
    if body['action'] == 'live_stop_after_stage':
        phase = read_json(root/'work/scheduler/state.json').get('phase')
        if phase not in ('capturing', 'analyzing', 'finishing'):
            raise ValueError('No live stage is running right now.')
        message = call_scheduler(config, {'name': 'stop_auto_after_stage'})
        if not (root/'work/scheduler/stop-after-stage').exists():
            raise RuntimeError('Could not verify the stop request')
        return message or 'The live job will stop after this stage.'
    if body['action'] == 'manual_spacing':
        message = call_scheduler(config, {'name': 'set_manual_spacing', 'minutes': body['minutes']})
        if read_json(root/'scheduler.json').get('manual_post_minutes') != body['minutes']:
            raise RuntimeError('Could not verify the new gap')
        return message or 'Manual clip gap updated.'
    if body['action'] == 'shotsnip_commentary':
        message = call_scheduler(config, {'name': 'set_commentary', 'mode': body['mode']})
        commentary = read_json(root/'scheduler.json').get('commentary') or {}
        actual = commentary.get('mode') if commentary.get('enabled') else 'off'
        if actual != body['mode']:
            raise RuntimeError('Could not verify the commentary change')
        return message or 'Commentary updated.'
    if body['action'] == 'shotsnip_watch_worker':
        message = call_scheduler(config, {'name': 'start_worker' if body['start'] else 'stop_worker'})
        if not body['start'] and not (root/'work/dashboard/stop-worker').exists():
            raise RuntimeError('Could not verify the stop request')
        return message or ('Watch worker started.' if body['start'] else 'Watch worker stop requested.')
    flag = root/'work/dashboard/run-now'
    if body['cancel']:
        call_scheduler(config, {'name': 'cancel_run_now'})
        if flag.exists():
            raise RuntimeError('Could not verify the cancellation')
        return 'Run-now request withdrawn.'
    message = call_scheduler(config, {'name': 'run_auto_now'})
    if not flag.exists():
        raise RuntimeError('Could not verify the run-now request')
    return message or 'Live cycle requested. It starts when the worker is free.'


def agent_file(root, name):
    try:
        value = read_json(root/'work/dashboard'/name)
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def agent_change(config, body, *, wait=10.0):
    root = config_root(config)
    if body['action'] == 'agent_learning':
        current = agent_file(root, 'agent-policy.json').get('learning_enabled', True) is not False
        if current is body['enabled']:
            return 'Source-priority learning is already ' + ('enabled.' if current else 'paused.')
        if current is not body['expected']:
            raise ValueError('Agent learning changed. Refresh and review it again.')
        call_scheduler(config, {'name': 'set_learning', 'enabled': body['enabled']})
        if (agent_file(root, 'agent-policy.json').get('learning_enabled', True) is not False) is not body['enabled']:
            raise RuntimeError('Could not verify the learning change')
        return ('Source-priority learning enabled. The next review recomputes priorities.' if body['enabled']
                else 'Source-priority learning paused. Existing priorities stay until it is enabled again.')
    before = agent_file(root, 'agent-review.json').get('checked_at')
    message = call_scheduler(config, {'name': body['mode']})
    if body['mode'] == 'refresh':
        return (message or 'Analytics refresh started.') + ' The review follows when the sync finishes.'
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if agent_file(root, 'agent-review.json').get('checked_at') not in (None, before):
            return 'Agent review finished. Findings are updated.'
        time.sleep(0.5)
    return (message or 'Agent review started.') + ' Findings update when it finishes.'


def perform(config, body):
    action = body['action']
    state = Path(config['state_dir'])
    if action in ('scheduler', 'scheduler_cadence'):
        return scheduler_change(config, body)
    if action in TARGET_ACTIONS:
        return target_change(config, body)
    if action in AGENT_ACTIONS:
        return agent_change(config, body)
    if action in LIVE_ACTIONS:
        return live_change(config, body)
    if action in HISTOLD_LIVE_ACTIONS:
        from .histold_live import perform as histold_live_perform
        return histold_live_perform(config, body)
    if action == 'histold_strategy':
        reply = owner_request(config, '/api/action', {'name': body['name'], 'args': body['args']}, timeout=60)
        return str(reply.get('message') or 'Strategy setting saved.')[:300]
    if action == 'bot_action':
        from .bots import perform as bot_perform
        return bot_perform(config, body)
    if action == 'draft_preview':
        from .media import prepare_draft
        pending = read_json(Path(config['dashboard_private_snapshot'])).get('approvals', [])
        if not any(row.get('stage') == body['stage'] and row.get('run_id') == body['run_id'] for row in pending):
            raise ValueError('That draft is no longer waiting for approval.')
        return prepare_draft(config, dict(body, _pending=pending))
    if action == 'ai_settings':
        if body.get('mode') is not None:
            payload = {'mode': body['mode']}
        else:
            payload = {body['provider']: {**{k: body[k] for k in ('per_5h', 'per_week') if k in body},
                                          **({'clear_cooldown': True} if body.get('clear_cooldown') is True else {})}}
        reply = owner_request(config, '/api/ai', payload, timeout=20)
        if body.get('mode') is not None and reply.get('mode') != body['mode']:
            raise RuntimeError('Could not verify the AI setting')
        return 'AI answering set to ' + body['mode'] + '.' if body.get('mode') else 'AI usage settings saved.'
    if action == 'histold_research':
        reply = owner_request(config, '/api/research', {}, timeout=20)
        return str(reply.get('message') or 'Content research started. Results appear in the Histold analysis when it finishes.')[:300]
    if action == 'assistant_ask':
        from .assistant import start
        return start(config, body)
    if action == 'assistant_apply':
        from .assistant import apply
        return apply(config, body)
    if action == 'archive_now':
        roots = {channel: config['roots'][channel] for channel in ('histold', 'shotsnip')}
        result = archive_tick(dict(config, roots=roots), 1)
        if result['failed']:
            raise RuntimeError('Archive verification did not complete')
        return 'One eligible package was archived and verified.' if result['archived'] else 'No new eligible local packages.'
    if action == 'restore':
        with worker_lock(state):
            identity = body['archive_id']
            manifest = checked_manifest(read_json(state/'manifests'/f'{identity}.json'))
            if manifest['archive_id'] != identity or manifest.get('status') != 'verified_local_retained':
                raise ValueError('The archive is not verified.')
            if manifest['channel'] not in ('histold', 'shotsnip'):
                raise ValueError('This archive is outside Studio HQ.')
            destination = state/'restores'
            usage = sum(path.stat().st_size for path in destination.rglob('*') if path.is_file())
            required = sum(item['size'] for item in manifest['files'])
            if usage+required > 1024**3 or shutil.disk_usage(state).free < required+1024**3:
                raise ValueError('The 1 GiB restore cache or available disk space is insufficient.')
            active = config
            if identity in config.get('legacy_drive_archive_ids', []):
                active = read_json(config['legacy_archive_config'])
            restore(manifest, active)
        return 'Restored and verified on Windows under StudioHQ state/restores.'
    if action == 'strategist':
        task = subprocess.run(['schtasks.exe', '/Run', '/TN', 'Histold Strategist'],
                              capture_output=True, timeout=20)
        if task.returncode:
            raise RuntimeError('Windows did not accept the Strategist task request')
        return 'Requested a run from the existing Windows Strategist task.'
    if action == 'story_draft':
        if body['channel'] != 'histold':
            raise ValueError('This draft is outside Studio HQ.')
        dispatch_story(config, 'publish.yml', {'no_upload': 'true', 'scheduled': 'false', 'format': body['format']})
        return 'Draft render requested. The workflow will render a preview without publishing.'
    if action == 'story_approve':
        repo = config['github_repo']
        pending = github_json(config, f'repos/{repo}/contents/data/pending_approvals.json?ref=master')
        rows = json.loads(base64.b64decode(pending['content']))
        if not any(row.get('stage_dir_name') == body['stage'] and str(row.get('run_id')) == body['run_id'] for row in rows):
            raise ValueError('The draft is no longer on the upstream approval list.')
        artifacts = github_json(config, f"repos/{repo}/actions/runs/{body['run_id']}/artifacts")
        if not any(row.get('name') == 'run-'+body['stage'] and row.get('expired') is False for row in artifacts['artifacts']):
            raise ValueError('The draft artifact is no longer available.')
        dispatch_story(config, 'approve.yml', {'stage_dir_name':body['stage'], 'source_run_id':body['run_id'],
                                               'privacy_status':body['privacy']})
        return 'Approval sent to the existing Histold publishing workflow.'
    raise ValueError('Unsupported maintenance command.')


def github_json(config, endpoint):
    proc = subprocess.run([config['github_cli'], 'api', endpoint], capture_output=True, timeout=25)
    if proc.returncode:
        raise RuntimeError('GitHub request could not be confirmed')
    return json.loads(proc.stdout)


def dispatch_story(config, workflow, inputs):
    proc = subprocess.run([config['github_cli'], 'workflow', 'run', workflow, '--repo', config['github_repo'],
                           '--ref', 'master', '--json'], input=json.dumps(inputs).encode(),
                          capture_output=True, timeout=30)
    if proc.returncode:
        raise RuntimeError('GitHub workflow request could not be confirmed')


def maintenance_tick(config):
    state = Path(config['state_dir'])
    with worker_lock(state, 'hub-maintenance.lock'):
        with closing(controls_db(config)) as db:
            with db:
                db.execute('BEGIN IMMEDIATE')
                db.execute("UPDATE commands SET status='review',message='Interrupted. Check the existing worker before sending another request.',updated=? WHERE status='running'",
                           (time.time(),))
                row = db.execute("SELECT id,body FROM commands WHERE status='queued' ORDER BY updated LIMIT 1").fetchone()
                if row:
                    db.execute("UPDATE commands SET status='running',message='Work is running on Windows.',updated=? WHERE id=?",
                               (time.time(),row['id']))
            if row:
                status = 'complete'
                body = json.loads(row['body'])
                try:
                    message = perform(config, body)
                except WorkerBusy:
                    status, message = 'queued', 'Waiting for the existing Shotsnip worker.'
                except ValueError as exc:
                    status, message = 'rejected', str(exc)
                except (OSError, RuntimeError, TimeoutError, KeyError, subprocess.TimeoutExpired):
                    status = 'review' if body['action'] in ('scheduler','scheduler_cadence','strategist','story_approve','story_draft','assistant_apply') + SHOTSNIP_OWNER_ACTIONS + LIVE_ACTIONS + HISTOLD_OWNER_ACTIONS + BOT_ACTIONS + HISTOLD_LIVE_ACTIONS else 'failed'
                    message = 'The action could not be confirmed. Check the existing worker or archive report.'
                with db:
                    db.execute('UPDATE commands SET status=?,message=?,updated=? WHERE id=?',
                               (status,message,time.time(),row['id']))
                refresh_snapshot(config)
        backup_queue(config)
        if private_snapshot := config.get('dashboard_private_snapshot'):
            index_root = Path(private_snapshot).parent
            marker = index_root/'phase3-index-status.json'
            if not marker.is_file() or time.time()-marker.stat().st_mtime >= 300:
                from .__main__ import sync_events
                roots = {channel: config['roots'][channel] for channel in ('histold', 'shotsnip')}
                sync_events(dict(config, roots=roots, state_dir=index_root))
                atomic_json(marker, {'at': stamp()})


def backup_queue(config):
    """Use SQLite online backup and atomically rotate the latest daily copy."""
    state = Path(config['state_dir'])
    directory = Path(config['dashboard_backup_dir'])
    directory.mkdir(exist_ok=True)
    status = state/'backups/status.json'
    if status.is_file() and time.time()-status.stat().st_mtime < 86400:
        return
    for name, source in {
        'manual-queue': config_root(config)/'work/manual/jobs.sqlite',
        'hub-commands': state/'hub-commands.sqlite',
    }.items():
        target = directory/f'{name}.sqlite'
        temporary = directory/f'{name}-{uuid.uuid4().hex}.tmp'
        try:
            with closing(sqlite3.connect(source.as_uri()+'?mode=ro', uri=True, timeout=2)) as live:
                with closing(sqlite3.connect(temporary)) as copy:
                    live.backup(copy)
                    if copy.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                        raise RuntimeError('Queue backup integrity check failed')
            if target.is_file():
                shutil.copy2(target, directory/f'{name}.previous.sqlite')
            os.replace(temporary,target)
        finally:
            temporary.unlink(missing_ok=True)
    status.parent.mkdir(exist_ok=True)
    atomic_json(status, {'at':stamp(),'status':'verified'})


def overview(config):
    path = Path(config['state_dir'])/'hub-commands.sqlite'
    if not path.is_file():
        return []
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=1)) as db:
        return [{'id': row[4], 'action': row[0], 'status': row[1], 'message': row[2],
                 'at': datetime.fromtimestamp(row[3], timezone.utc).isoformat()}
                for row in db.execute('SELECT action,status,message,updated,id FROM commands ORDER BY updated DESC LIMIT 12')]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path)
    parser.add_argument('mode', choices=['submit','maintain'])
    args = parser.parse_args()
    config = read_json(args.config)
    if args.mode == 'maintain':
        try:
            maintenance_tick(config)
        except (OSError, sqlite3.Error, RuntimeError):
            return 1
        try:
            from .video_insights import maybe_start
            maybe_start(config, args.config.resolve())
        except OSError:
            pass
        return 0
    try:
        print(json.dumps(submit(config,json.loads(sys.stdin.buffer.read(16384)))))
    except ValueError as exc:
        print(json.dumps({'message':str(exc)}))
        return 2
    except (OSError, sqlite3.Error, RuntimeError, KeyError, TypeError):
        print(json.dumps({'message':'The local worker state is unavailable.'}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
