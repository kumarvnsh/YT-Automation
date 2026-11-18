"""Telegram control channel: updates to the owner's phone, approvals and allowlisted actions.

Security model (enforced here, not trusted to anyone's message):
* Only one paired Telegram account (``owner_id``) in a private chat is obeyed; everything else
  is ignored silently. Pairing needs a one-time code printed on the laptop.
* Messages never become commands or shell text. Phone buttons carry only an approval id;
  the action and its arguments were fixed by this code when the approval was created.
* Approvals are one-time and expire. Each is recorded with who decided and the result.
* Admin actions go to the separate elevated runner (admin/runner.ps1), which re-checks
  its own allowlist from an Administrators-only folder.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import subprocess
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from src import bot_findings

HOME = Path(os.environ.get('HISTOLD_HOME', r'D:\histold'))
CODE = Path(__file__).resolve().parent.parent
CONFIG = HOME / 'secrets' / 'telegram.json'
CONTROL = HOME / 'control'
ADMIN = HOME / 'admin-runner'
OPERATOR = HOME / 'operator'
WORKSPACE = HOME / 'bot-workspace'
DASHBOARD = 'http://127.0.0.1:8799'
APPROVAL_TTL = 3600
TEXT_LIMIT = 3900
CHANNELS = ('histold', 'shotsnip')

# Every action the phone can trigger. Arguments are validated against these patterns.
ACTIONS = {
    'fix_problem': {'label': 'Fix this problem now', 'args': {'channel': '^(histold|shotsnip)$', 'key': '^[a-z0-9-]{1,60}$'}},
    'operator_run': {'label': 'Run the Operator now', 'args': {}},
    'operator_pause': {'label': 'Pause the Operator', 'args': {}},
    'operator_resume': {'label': 'Resume the Operator', 'args': {}},
    'review': {'label': 'Run an Operations review', 'args': {'channel': '^(histold|shotsnip)$'}},
    'restart_dashboard': {'label': 'Restart the dashboard', 'args': {}},
    'admin': {'label': 'Run an admin action', 'args': {'name': '^(ping|stop-elevated-dashboard|stop-stale-monitors|restart-tailscale)$'}},
}
ADMIN_ACTIONS = {
    'ping': 'Test that admin access works (changes nothing)',
    'stop-elevated-dashboard': 'Unstick the dashboard and restart it',
    'stop-stale-monitors': 'Stop stuck background checks',
    'restart-tailscale': 'Reconnect phone access to the dashboard',
}
# What a successful admin action means, in plain words.
ADMIN_DONE = {
    'ping': 'Admin access is working.',
    'stop-elevated-dashboard': 'The stuck dashboard was stopped.',
    'stop-stale-monitors': 'Stuck background checks were cleared.',
    'restart-tailscale': 'Phone access was reconnected.',
}
HELP = ('Here is what I can do, sir:\n\n'
        '/status - Is everything OK? A quick summary\n'
        '/problems - What is broken right now, with a Fix button\n'
        '/review - Ask the checker to look at a channel now\n'
        '/operator - Start, pause or resume the auto-fixer\n'
        '/dashboard - Restart the dashboard if it is stuck\n'
        '/admin - Laptop maintenance that needs admin rights\n\n'
        'Each button works once and stops working after an hour.')
NAMES = {'histold': 'Histold (history channel)', 'shotsnip': 'Shotsnip (gaming clips)'}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def load_config(path: Path = CONFIG) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(config: dict, path: Path = CONFIG) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Rewrite in place so the owner-only file permissions set by setup_telegram.ps1 are kept.
    path.write_text(json.dumps(config, indent=2), encoding='utf-8')


@contextmanager
def ipv4_only():
    """This laptop's IPv6 route to Telegram is broken: each IPv6 attempt stalls ~21 s before
    falling back, which makes Telegram expire button presses. Telegram calls use IPv4 only."""
    import socket
    import urllib3.util.connection as connection
    original = connection.allowed_gai_family
    connection.allowed_gai_family = lambda: socket.AF_INET
    try:
        yield
    finally:
        connection.allowed_gai_family = original


class Telegram:
    def __init__(self, token: str, session=requests):
        if not re.fullmatch(r'\d{5,15}:[A-Za-z0-9_-]{30,64}', token or ''):
            raise ValueError('Telegram bot token is missing or malformed')
        self.base = 'https://api.telegram.org/bot' + token + '/'
        self.session = session

    def call(self, method: str, http_timeout: float = 75, **params):
        with ipv4_only():
            response = self.session.post(self.base + method, json=params, timeout=http_timeout)
        try:
            data = response.json()
        except ValueError:
            raise RuntimeError(f'Telegram {method} failed (HTTP {response.status_code})') from None
        if not data.get('ok'):
            # Never include the URL: it contains the token.
            raise RuntimeError(f"Telegram {method} failed: {str(data.get('description'))[:200]}")
        return data['result']

    def send(self, chat_id: int, text: str, buttons: list[list[tuple[str, str]]] | None = None):
        params = {'chat_id': chat_id, 'text': text[:TEXT_LIMIT], 'disable_web_page_preview': True}
        if buttons:
            params['reply_markup'] = {'inline_keyboard': [[{'text': t[:60], 'callback_data': d} for t, d in row] for row in buttons]}
        return self.call('sendMessage', **params)

    def edit(self, chat_id: int, message_id: int, text: str):
        return self.call('editMessageText', chat_id=chat_id, message_id=message_id, text=text[:TEXT_LIMIT])

    def answer(self, callback_id: str, text: str = ''):
        return self.call('answerCallbackQuery', callback_query_id=callback_id, text=text[:180])

    def updates(self, offset: int, wait: int = 50):
        """Long poll: Telegram holds the request up to ``wait`` seconds. No inbound port is needed."""
        return self.call('getUpdates', http_timeout=wait + 15, offset=offset, timeout=wait,
                         allowed_updates=['message', 'callback_query'])


class Approvals:
    """One-time, expiring approvals. The action and args are fixed when the approval is created."""

    def __init__(self, directory: Path = CONTROL):
        self.path = Path(directory) / 'approvals.sqlite'

    @contextmanager
    def db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY, action TEXT, args TEXT, summary TEXT, '
                   'created REAL, expires REAL, status TEXT, decided REAL, result TEXT)')
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, action: str, args: dict, summary: str, ttl: int = APPROVAL_TTL) -> str:
        validate(action, args)
        identity = uuid.uuid4().hex[:16]
        stamp = time.time()
        with self.db() as db:
            db.execute('INSERT INTO approvals VALUES(?,?,?,?,?,?,?,?,?)',
                       (identity, action, json.dumps(args), summary[:500], stamp, stamp + ttl, 'pending', None, None))
        return identity

    def decide(self, identity: str, approve: bool) -> dict:
        if not re.fullmatch(r'[a-f0-9]{16}', identity or ''):
            raise ValueError('Unknown request')
        stamp = time.time()
        with self.db() as db:
            changed = db.execute("UPDATE approvals SET status=?, decided=? WHERE id=? AND status='pending' AND expires>?",
                                 ('approved' if approve else 'rejected', stamp, identity, stamp)).rowcount
            row = db.execute('SELECT * FROM approvals WHERE id=?', (identity,)).fetchone()
        if not row:
            raise ValueError('Unknown request')
        if not changed:
            raise ValueError('This button was already used' if row['status'] != 'pending' else 'This request expired')
        return {**dict(row), 'args': json.loads(row['args'])}

    def finish(self, identity: str, status: str, result: str) -> None:
        with self.db() as db:
            db.execute('UPDATE approvals SET status=?, result=? WHERE id=?', (status, result[:2000], identity))


def validate(action: str, args: dict) -> None:
    spec = ACTIONS.get(action)
    if not spec or not isinstance(args, dict) or set(args) != set(spec['args']):
        raise ValueError('Action is not allowlisted')
    for name, pattern in spec['args'].items():
        if not re.fullmatch(pattern, str(args[name])):
            raise ValueError('Invalid argument for ' + action)


def offer(approvals: Approvals, action: str, args: dict, label: str) -> list[tuple[str, str]]:
    """A button row: approve (runs the fixed action once) and dismiss."""
    identity = approvals.create(action, args, label)
    return [(label, 'a:' + identity + ':y'), ('Dismiss', 'a:' + identity + ':n')]


def notify(text: str, offers: list[tuple[str, dict, str]] | None = None, config_path: Path = CONFIG,
           control: Path = CONTROL, session=requests) -> str:
    """Send an update to the paired owner, optionally with approval buttons. Never raises."""
    config = load_config(config_path)
    if not config.get('token') or not config.get('owner_id'):
        return 'not_configured'
    try:
        approvals = Approvals(control)
        rows = [offer(approvals, action, args, label) for action, args, label in (offers or [])[:5]]
        Telegram(config['token'], session).send(int(config['owner_id']), text, rows or None)
        return 'sent'
    except Exception as exc:  # an update must never break the caller
        return 'failed: ' + type(exc).__name__


def run(cmd: list[str], timeout: int = 180) -> tuple[int, str]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return result.returncode, ((result.stdout or '') + (result.stderr or '')).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 124, type(exc).__name__ + ': ' + str(exc)


class Controller:
    """Handles Telegram updates. I/O helpers are attributes so tests can replace them."""

    def __init__(self, telegram: Telegram, config: dict, config_path: Path = CONFIG, control: Path = CONTROL,
                 admin: Path = ADMIN, operator: Path = OPERATOR, workspace: Path = WORKSPACE):
        self.telegram, self.config, self.config_path = telegram, config, config_path
        self.approvals = Approvals(control)
        self.admin, self.operator, self.workspace = Path(admin), Path(operator), Path(workspace)
        self.run = run
        self.dashboard_post = self._dashboard_post
        self.sleep = time.sleep

    # ------------------------------------------------------------ entry point
    def handle(self, update: dict) -> None:
        if 'callback_query' in update:
            return self.on_button(update['callback_query'])
        message = update.get('message') or {}
        chat, sender = message.get('chat') or {}, message.get('from') or {}
        text = str(message.get('text') or '').strip()
        if chat.get('type') != 'private' or not text:
            return
        if not self.config.get('owner_id'):
            return self.pair(chat, sender, text)
        if sender.get('id') != int(self.config['owner_id']) or chat.get('id') != int(self.config['owner_id']):
            return  # not the owner: ignore silently
        command = text.split()[0].split('@')[0].lower()
        handler = {'/start': self.help, '/help': self.help, '/status': self.status, '/problems': self.problems,
                   '/review': self.review_menu, '/operator': self.operator_menu, '/dashboard': self.dashboard_menu,
                   '/admin': self.admin_menu}.get(command, self.help)
        handler()

    def pair(self, chat: dict, sender: dict, text: str) -> None:
        code, expires = self.config.get('pair_code'), self.config.get('pair_expires', 0)
        parts = text.split()
        if not code or time.time() > expires or len(parts) != 2 or parts[0].lower() != '/pair':
            return
        if not secrets.compare_digest(parts[1], str(code)):
            return
        self.config.update(owner_id=int(sender['id']))
        self.config.pop('pair_code', None)
        self.config.pop('pair_expires', None)
        save_config(self.config, self.config_path)
        self.send('Connected. From now on I only take orders from this Telegram account.\n\n' + HELP, chat_id=chat['id'])

    def send(self, text: str, buttons=None, chat_id: int | None = None):
        return self.telegram.send(chat_id or int(self.config['owner_id']), text, buttons)

    # ------------------------------------------------------------ commands
    def help(self):
        self.send(HELP)

    def status(self):
        lines, trouble = [], False
        for channel in CHANNELS:
            open_problems = bot_findings.problems(self.workspace, self.operator, channel)
            if not open_problems:
                lines.append(f'✅ {NAMES[channel]}: all good, no problems found.')
                continue
            trouble = True
            lines.append(f'⚠️ {NAMES[channel]}: {len(open_problems)} problem(s)')
            lines += [f"   • {(p.get('plain') or p['title'])[:120]}\n     → {p['fix']['label']}" for p in open_problems[:4]]
        paused = (self.operator / 'PAUSE').exists()
        lines.append('')
        lines.append('🔧 Auto-fixer: ' + ('PAUSED. It will not fix anything until you turn it back on (/operator).'
                                         if paused else 'on. It repairs problems by itself and tells you.'))
        code, _ = self.run(['curl.exe', '-s', '-o', 'NUL', '-w', '%{http_code}', DASHBOARD + '/login'], timeout=15)
        lines.append('🖥️ Dashboard: ' + ('working.' if code == 0 else 'not responding. Try /dashboard.'))
        if not (self.admin / 'runner.ps1').exists():
            lines.append('🔒 Admin helper: not set up yet.')
        if trouble:
            lines.append('\nTap /problems to see each one with a Fix button.')
        self.send('\n'.join(lines))

    def problems(self):
        found = False
        for channel in CHANNELS:
            for p in bot_findings.problems(self.workspace, self.operator, channel):
                found = True
                buttons = None
                if p['fix']['state'] not in ('deployed', 'verified'):
                    buttons = [offer(self.approvals, 'fix_problem', {'channel': channel, 'key': p['key']}, '🔧 Fix it now')]
                self.send(f"⚠️ {NAMES[channel]}\n\nWhat is wrong: {p.get('plain') or p['title']}\n\n"
                          f"Status: {p['fix']['label']}"
                          + (f"\nNote: {p['fix']['detail']}" if p['fix'].get('detail') else ''), buttons)
        if not found:
            self.send('✅ Nothing is broken right now.')

    def review_menu(self):
        self.send('Which channel should the checker look at now? It takes a few minutes and tells you here if it finds anything.',
                  [offer(self.approvals, 'review', {'channel': c}, 'Check ' + NAMES[c])[:1] for c in CHANNELS])

    def operator_menu(self):
        paused = (self.operator / 'PAUSE').exists()
        rows = [offer(self.approvals, 'operator_run', {}, '▶️ Fix problems now')[:1],
                offer(self.approvals, 'operator_resume' if paused else 'operator_pause', {},
                      '✅ Turn auto-fixing back on' if paused else '⏸️ Pause auto-fixing')[:1]]
        self.send('The auto-fixer finds broken things and repairs them by itself.\n\nRight now it is '
                  + ('PAUSED.' if paused else 'ON.'), rows)

    def dashboard_menu(self):
        self.send('Restart the dashboard? Use this if the dashboard page is stuck or not loading.',
                  [offer(self.approvals, 'restart_dashboard', {}, '🔄 Restart dashboard')])

    def admin_menu(self):
        if not (self.admin / 'runner.ps1').exists():
            return self.send('The admin helper is not set up on the laptop yet.')
        self.send('Laptop maintenance (needs admin rights). Tap one:',
                  [offer(self.approvals, 'admin', {'name': n}, d[:58])[:1] for n, d in ADMIN_ACTIONS.items()])

    # ------------------------------------------------------------ buttons
    def on_button(self, query: dict) -> None:
        sender = (query.get('from') or {}).get('id')
        message = query.get('message') or {}
        if not self.config.get('owner_id') or sender != int(self.config['owner_id']):
            return  # not the owner: no answer, no action
        match = re.fullmatch(r'a:([a-f0-9]{16}):([yn])', str(query.get('data') or ''))
        try:
            approval = self.approvals.decide(match.group(1), match.group(2) == 'y') if match else None
            note = 'Unknown button' if not match else ('OK, ignored' if match.group(2) == 'n' else 'On it, sir...')
        except ValueError as exc:
            approval, note = None, str(exc)
        # Stop the button spinner right away. This is cosmetic: a late or failed answer must
        # never stop an approved action from running.
        try:
            self.telegram.answer(query['id'], note)
        except Exception:
            pass
        if not approval:
            if match and note != 'Unknown button':
                self.send(note + '.')
            return
        if match.group(2) == 'n':
            return self._edit(message, (message.get('text') or '') + '\n\nIgnored.')
        self._edit(message, (message.get('text') or '') + '\n\n⏳ Working on it...')
        try:
            ok, result = self.execute(approval['action'], approval['args'])
        except Exception as exc:
            ok, result = False, 'Something went wrong: ' + str(exc)[:300]
        self.approvals.finish(approval['id'], 'done' if ok else 'failed', result)
        self.send(('✅ Done. ' if ok else '❌ That did not work. ') + result)

    def _edit(self, message: dict, text: str) -> None:
        try:
            self.telegram.edit(message['chat']['id'], message['message_id'], text)
        except Exception:
            pass  # editing is cosmetic

    # ------------------------------------------------------------ actions
    def execute(self, action: str, args: dict) -> tuple[bool, str]:
        validate(action, args)
        if action == 'fix_problem':
            item = bot_findings.approve(self.workspace, args['channel'], args['key'])
            ok, out = self.start_operator()
            return ok, f"Fixing: {(item.get('plain') or item['title'])[:150]}\n\n" + out
        if action == 'operator_run':
            return self.start_operator()
        if action == 'operator_pause':
            self.operator.mkdir(parents=True, exist_ok=True)
            (self.operator / 'PAUSE').write_text('Paused from Telegram ' + now_utc().isoformat(), encoding='utf-8')
            return True, 'Auto-fixing is paused. Nothing will be changed until you turn it back on.'
        if action == 'operator_resume':
            (self.operator / 'PAUSE').unlink(missing_ok=True)
            return True, 'Auto-fixing is back on.'
        if action == 'review':
            body = {'action': 'assign', 'bot': 'operations', 'channel': args['channel'], 'engine': 'superbot',
                    'instruction': 'Review current pipeline health, recent upload outcomes and data freshness. Separate '
                                   'active failures from historical errors. Recommend the next recovery check and explain '
                                   'whether publishing is progressing. Do not execute changes.'}
            self.dashboard_post('/api/bots/action', body)
            return True, 'The checker is looking now. If it finds anything, I will tell you here within a few minutes.'
        if action == 'restart_dashboard':
            return self.restart_dashboard()
        if action == 'admin':
            ok, out = self.admin_request(args['name'])
            if ok and args['name'] == 'stop-elevated-dashboard':
                ok2, out2 = self.restart_dashboard(allow_admin=False)
                return ok2, ADMIN_DONE[args['name']] + ' ' + out2
            return ok, ADMIN_DONE[args['name']] if ok else out
        raise ValueError('Action is not allowlisted')

    def start_operator(self) -> tuple[bool, str]:
        if (self.operator / 'PAUSE').exists():
            return False, 'Auto-fixing is paused. Turn it back on first with /operator.'
        code, out = self.run(['schtasks', '/Run', '/TN', 'Histold Operator'], timeout=30)
        return code == 0, ('The auto-fixer has started. I will message you when it is fixed, or if it needs your help.'
                           if code == 0 else 'The auto-fixer could not start (' + out[-150:] + ').')

    def restart_dashboard(self, allow_admin: bool = True) -> tuple[bool, str]:
        script = str(CODE / 'scripts' / 'start_dashboard.ps1')
        command = ['powershell', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', script]
        code, out = self.run(command, timeout=180)
        if code != 0 and 'unmanaged process' in out and allow_admin:
            ok, admin_out = self.admin_request('stop-elevated-dashboard')
            if not ok:
                return False, 'The dashboard is stuck and the admin helper could not unstick it (' + admin_out[-150:] + ').'
            code, out = self.run(command, timeout=180)
        return code == 0, ('The dashboard is running again.' if code == 0 else
                           'The dashboard did not restart (' + (out.splitlines() or ['no details'])[-1][:200] + ').')

    def admin_request(self, name: str) -> tuple[bool, str]:
        if name not in ADMIN_ACTIONS:
            raise ValueError('Admin action is not allowlisted')
        requests_dir, results_dir = self.admin / 'requests', self.admin / 'results'
        if not (self.admin / 'runner.ps1').exists() or not requests_dir.is_dir():
            return False, 'The admin helper is not set up on the laptop yet.'
        identity = uuid.uuid4().hex
        temp = requests_dir / (identity + '.tmp')
        temp.write_text(json.dumps({'id': identity, 'action': name, 'created': now_utc().isoformat()}), encoding='utf-8')
        os.replace(temp, requests_dir / (identity + '.json'))
        self.run(['schtasks', '/Run', '/TN', 'Histold Admin Runner'], timeout=30)  # it also polls every minute
        result_path = results_dir / (identity + '.json')
        for _ in range(60):
            if result_path.exists():
                try:
                    result = json.loads(result_path.read_text(encoding='utf-8-sig'))
                except ValueError:
                    self.sleep(1)
                    continue
                return result.get('status') == 'ok', str(result.get('output') or '')[-1500:]
            self.sleep(3)
        return False, 'The admin helper did not answer within 3 minutes. It will still do it shortly.'

    def _dashboard_post(self, path: str, body: dict) -> dict:
        key = (HOME / 'dashboard_key.txt').read_text(encoding='utf-8').strip()
        response = requests.post(DASHBOARD + path, json=body, timeout=30, allow_redirects=False,
                                 headers={'Cookie': 'hk=' + key, 'X-Histold': '1'})
        if response.status_code != 200:
            raise RuntimeError(f'Dashboard request failed (HTTP {response.status_code})')
        return response.json()
