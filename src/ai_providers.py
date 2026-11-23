"""Answer providers for the dashboard assistant: local Ollama, Claude Code (`claude -p`) and Codex (`codex exec`).

The cloud CLIs run headless on the owner's own subscriptions, with no tools: an empty working directory, a
read-only sandbox for Codex, and every built-in tool disabled for Claude. They only write the answer text. Setting
changes still go through the local proposal + Confirm flow; providers never touch settings or the channel.

Usage is capped per provider over a rolling 5-hour window and a rolling 7-day window (the dashboard cannot see the
subscription's real remaining quota, so these caps keep its share small). When a CLI reports a usage limit, the
provider rests until the reset time it reports (or one hour) and the next provider in the chain answers.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

LABELS = {'local': 'Local qwen3:8b', 'claude': 'Claude (your plan)', 'codex': 'Codex (your plan)'}
DEFAULTS = {'mode': 'auto', 'order': ['codex', 'claude', 'local'],
            'claude': {'per_5h': 20, 'per_week': 120},
            'codex': {'per_5h': 20, 'per_week': 120}}
WINDOW_5H, WINDOW_WEEK = 5 * 3600, 7 * 86400
LIMIT_RE = re.compile(r'usage limit|rate limit|limit reached|hit your .*limit|quota|too many requests|429', re.I)
RESET_RE = re.compile(r'(?:try again at|resets?(?: at)?)\s+(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?', re.I)


def find_claude() -> str | None:
    for c in (os.environ.get('HISTOLD_CLAUDE_EXE'), shutil.which('claude'),
              str(Path(os.environ.get('APPDATA', '')) / 'npm' / 'claude.cmd'),
              str(Path.home() / '.local' / 'bin' / 'claude.exe'), str(Path.home() / '.local' / 'bin' / 'claude')):
        if c and Path(c).exists():
            return c
    return None


def find_codex() -> str | None:
    for c in (os.environ.get('HISTOLD_CODEX_EXE'), shutil.which('codex'),
              str(Path(os.environ.get('APPDATA', '')) / 'npm' / 'codex.cmd')):
        if c and Path(c).exists():
            return c
    return _codex_app_cli()


_APP_CLI: list = []


def _codex_app_cli() -> str | None:
    """The Codex desktop app (Microsoft Store) bundles its CLI; WindowsApps can't be listed, so ask Windows once."""
    if not _APP_CLI:
        found = None
        if os.name == 'nt':
            try:
                loc = subprocess.run(['powershell', '-NoProfile', '-Command', '(Get-AppxPackage OpenAI.Codex).InstallLocation'],
                                     capture_output=True, text=True, timeout=20,
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout.strip().splitlines()
                exe = Path(loc[-1]) / 'app' / 'resources' / 'codex.exe' if loc else None
                found = str(exe) if exe and exe.exists() else None
            except (OSError, subprocess.SubprocessError):
                pass
        _APP_CLI.append(found)
    return _APP_CLI[0]


_DATED_RE = re.compile(r'(?:try again at|resets?(?: at| on)?)\s+([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4}),?\s+'
                       r'(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)', re.I)


def reset_time(text: str, now: datetime | None = None) -> float | None:
    """'try again at 3:35 AM' / 'resets 3am' -> epoch seconds of the next such local time."""
    now = now or datetime.now()
    dated = _DATED_RE.search(text or '')  # Codex: 'try again at Nov 25th, 2026 10:47 PM'
    if dated:
        try:
            day = datetime.strptime(f'{dated[1][:3]} {dated[2]} {dated[3]}', '%b %d %Y')
        except ValueError:
            day = None
        hour, minute, ampm = int(dated[4]), int(dated[5] or 0), dated[6].lower().replace('.', '')
        if day and hour <= 12 and minute <= 59:
            hour = hour % 12 + (12 if ampm == 'pm' else 0)
            return day.replace(hour=hour, minute=minute).timestamp()
    m = RESET_RE.search(text or '')
    if not m:
        return None
    hour, minute, ampm = int(m[1]), int(m[2] or 0), (m[3] or '').lower().replace('.', '')
    if ampm == 'pm' and hour < 12:
        hour += 12
    if ampm == 'am' and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if at <= now:
        at += timedelta(days=1)
    return at.timestamp()


class Providers:
    def __init__(self, path: Path | None, sandbox: Path | None = None):
        self.path = path  # None = local only (tests, or no settings location)
        self.sandbox = Path(sandbox or tempfile.gettempdir()) / 'histold-ai-sandbox'
        self.lock = threading.Lock()
        self._claude_flags: set[str] | None = None

    # ---------- settings + usage ----------
    def load(self) -> dict:
        state = json.loads(json.dumps(DEFAULTS))
        state.update({'usage': {'claude': [], 'codex': []}, 'cooldown': {}, 'last_error': {}})
        if self.path and self.path.exists():
            try:
                saved = json.loads(self.path.read_text(encoding='utf-8'))
                for k in state:
                    if k in saved:
                        state[k] = saved[k] if not isinstance(state[k], dict) else {**state[k], **saved[k]}
            except (OSError, ValueError):
                pass
        return state

    def save(self, state: dict) -> None:
        if not self.path:
            return
        now = time.time()
        for p in ('claude', 'codex'):
            state['usage'][p] = [t for t in state['usage'].get(p, []) if now - t < WINDOW_WEEK]
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(state, indent=1), encoding='utf-8')
        tmp.replace(self.path)

    def update_settings(self, body: dict) -> dict:
        with self.lock:
            state = self.load()
            if body.get('mode') in ('auto', 'local', 'claude', 'codex'):
                state['mode'] = body['mode']
            for p in ('claude', 'codex'):
                for k in ('per_5h', 'per_week'):
                    v = (body.get(p) or {}).get(k)
                    if isinstance(v, (int, float)) and 0 <= v <= 1000:
                        state[p][k] = int(v)
                if (body.get(p) or {}).get('clear_cooldown'):
                    state['cooldown'].pop(p, None)
            self.save(state)
        return self.status()

    def status(self) -> dict:
        state, now = self.load(), time.time()
        out = {'mode': state['mode'], 'enabled': self.path is not None, 'providers': {}}
        for p in ('claude', 'codex', 'local'):
            info = {'label': LABELS[p]}
            if p == 'local':
                info.update(installed=True, ready=True)
            else:
                exe = find_claude() if p == 'claude' else find_codex()
                used = state['usage'].get(p, [])
                cool = state['cooldown'].get(p, 0)
                info.update(installed=bool(exe), per_5h=state[p]['per_5h'], per_week=state[p]['per_week'],
                            used_5h=sum(now - t < WINDOW_5H for t in used), used_week=sum(now - t < WINDOW_WEEK for t in used),
                            resting_until=cool if cool > now else None, last_error=state['last_error'].get(p))
                info['ready'] = bool(exe) and not info['resting_until'] and info['used_5h'] < info['per_5h'] \
                    and info['used_week'] < info['per_week']
            out['providers'][p] = info
        return out

    def chain(self) -> list[str]:
        if not self.path:
            return ['local']
        state = self.load()
        mode = state['mode']
        order = state['order'] if mode == 'auto' else [mode]
        return [p for p in order if p in LABELS] + (['local'] if 'local' not in order else [])

    # ---------- answering ----------
    def answer(self, system: str, history: list[dict], local, *, order=None) -> tuple[str, str, list[str]]:
        """Returns (reply, provider used, notes about skipped providers)."""
        notes = []
        st = self.status()['providers']
        for p in (order if order is not None else self.chain()):
            if p == 'local':
                return local(), 'local', notes
            info = st[p]
            if not info['installed']:
                notes.append(f'{LABELS[p]} not installed')
                continue
            if not info['ready']:
                why = ('resting until ' + datetime.fromtimestamp(info['resting_until']).strftime('%H:%M')) if info['resting_until'] \
                    else 'dashboard cap reached for this window'
                notes.append(f'{LABELS[p]} skipped: {why}')
                continue
            try:
                reply = (self._claude if p == 'claude' else self._codex)(self._prompt(system, history))
                self._record(p)
                return reply, p, notes
            except LimitError as exc:
                self._rest(p, str(exc))
                notes.append(f'{LABELS[p]} hit its plan limit')
            except Exception as exc:  # noqa: BLE001 - fall through to the next provider
                with self.lock:
                    state = self.load()
                    state['last_error'][p] = f'{type(exc).__name__}: {exc}'[:200]
                    self.save(state)
                notes.append(f'{LABELS[p]} failed')
        return local(), 'local', notes

    @staticmethod
    def _prompt(system: str, history: list[dict]) -> str:
        convo = '\n\n'.join(f"{'OWNER' if m['role'] == 'user' else 'ASSISTANT'}: {m['content']}" for m in history[-10:])
        return (f'{system}\n\nYou are answering inside a dashboard chat. Do not run commands, read files or use tools; '
                f'answer only from DATA above. Reply with the assistant message only.\n\nCONVERSATION:\n{convo}\n\nASSISTANT:')

    def _run(self, cmd: list[str], prompt: str, timeout: int = 150) -> subprocess.CompletedProcess:
        self.sandbox.mkdir(parents=True, exist_ok=True)
        return subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding='utf-8', errors='replace',
                              cwd=self.sandbox, timeout=timeout, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    def _flags(self, exe: str) -> set[str]:
        if self._claude_flags is None:
            try:
                text = subprocess.run([exe, '--help'], capture_output=True, text=True, timeout=30,
                                      creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout
            except (OSError, subprocess.SubprocessError):
                text = ''
            self._claude_flags = set(re.findall(r'(--[a-z][a-z-]+)', text))
        return self._claude_flags

    def _claude(self, prompt: str) -> str:
        exe = find_claude()
        flags = self._flags(exe)
        cmd = [exe, '-p', '--output-format', 'json', '--max-turns', '1']
        if '--tools' in flags:
            cmd += ['--tools', '']
        else:
            cmd += ['--disallowedTools', 'Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,NotebookEdit,Task']
        if '--strict-mcp-config' in flags:
            cmd += ['--strict-mcp-config']
        if '--setting-sources' in flags:
            cmd += ['--setting-sources', 'user']
        if '--no-session-persistence' in flags:
            cmd += ['--no-session-persistence']
        if os.environ.get('HISTOLD_CLAUDE_MODEL'):
            cmd += ['--model', os.environ['HISTOLD_CLAUDE_MODEL']]
        r = self._run(cmd, prompt)
        out = (r.stdout or '').strip()
        try:
            data = json.loads(out.splitlines()[-1] if out else '{}')
        except ValueError:
            data = {}
        text = data.get('result') if isinstance(data, dict) else None
        blob = f'{out}\n{r.stderr}'
        if (r.returncode or (isinstance(data, dict) and data.get('is_error'))) and LIMIT_RE.search(blob):
            raise LimitError(blob[-300:])
        if r.returncode or not text:
            raise RuntimeError((r.stderr or out or f'exit {r.returncode}')[-200:])
        return text.strip()

    def _codex(self, prompt: str) -> str:
        exe = find_codex()
        self.sandbox.mkdir(parents=True, exist_ok=True)
        out = self.sandbox / f'codex-{os.getpid()}-{threading.get_ident()}.txt'
        out.unlink(missing_ok=True)
        cmd = [exe, 'exec', '--sandbox', 'read-only', '--skip-git-repo-check', '--ephemeral', '--ignore-rules',
               '--color', 'never', '-C', str(self.sandbox), '-o', str(out)]
        if os.environ.get('HISTOLD_CODEX_MODEL'):
            cmd += ['-m', os.environ['HISTOLD_CODEX_MODEL']]
        r = self._run(cmd, prompt)
        text = out.read_text(encoding='utf-8', errors='replace').strip() if out.exists() else ''
        out.unlink(missing_ok=True)
        blob = f'{r.stdout}\n{r.stderr}'
        if not text and LIMIT_RE.search(blob):
            raise LimitError(blob[-300:])
        if not text:
            raise RuntimeError((r.stderr or r.stdout or f'exit {r.returncode}').strip()[-200:])
        return text

    def _record(self, p: str) -> None:
        with self.lock:
            state = self.load()
            state['usage'].setdefault(p, []).append(time.time())
            state['last_error'].pop(p, None)
            self.save(state)

    def _rest(self, p: str, message: str) -> None:
        with self.lock:
            state = self.load()
            state['cooldown'][p] = reset_time(message) or time.time() + 3600
            state['last_error'][p] = 'Plan usage limit reported' + (
                f" · resets {datetime.fromtimestamp(state['cooldown'][p]):%H:%M}")
            self.save(state)


class LimitError(RuntimeError):
    pass
