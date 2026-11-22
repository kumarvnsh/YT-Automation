"""Histold live operations for Studio HQ: local production on the owner's plans.

Read-only status comes straight from the laptop's files (no network except a local Ollama ping):
- the runner lock and log (D:\\histold\\control\\local-publish*), and the newest stage in the
  dedicated clone's output folder, for the run in progress;
- plan usage (D:\\histold\\control\\plan-ai.json) against Histold's budgets in config.yaml;
- the waiting video (local-publish-retry.json) when the plans were used up.

Controls write the same files the dispatcher and runner use:
- ``histold_local_mode``: where videos are made. A switch file wins over config.yaml, so the change
  applies at the next publish hour without a git push.
- ``histold_local_run``: make one video now on the plans (the daily cap still applies).
- ``histold_local_retry``: start the waiting video now, or drop it.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .archive import atomic_json, read_json

ACTIONS = ('histold_local_mode', 'histold_local_run', 'histold_local_retry')
IST = timezone(timedelta(hours=5, minutes=30))
STEPS = (('research', 'Research'), ('script', 'Script'), ('voiceover', 'Voice'), ('assets', 'Visuals'),
         ('render', 'Render'), ('quality', 'Checks'), ('upload', 'Upload'))
DEFAULT_CAPS = {'per_5h': 60, 'per_week': 600}
LOCK_FRESH = 6 * 3600


def clone(config) -> Path:
    return Path(config.get('dashboard_histold_index_root') or config['roots']['histold'])


def home(config) -> Path:
    return clone(config).parent


def control(config) -> Path:
    return home(config) / 'control'


def _config_text(config) -> str:
    try:
        return (clone(config) / 'config.yaml').read_text(encoding='utf-8')
    except OSError:
        return ''


def _section(text: str, name: str) -> str:
    match = re.search(rf'^{name}:\s*\n((?:[ \t]+.*\n|\s*\n)+)', text + '\n', re.M)
    return match.group(1) if match else ''


def configured(config) -> dict:
    """The few config.yaml values Studio HQ needs, read without a YAML library."""
    text = _config_text(config)
    local, ai = _section(text, 'local_production'), _section(text, 'local_ai')
    enabled = re.search(r'^\s+enabled:\s*(true|false)', local, re.M)
    python = re.search(r"^\s+python:\s*['\"]?([^'\"\n#]+)", local, re.M)
    caps = {}
    for provider in ('claude', 'codex'):
        found = re.search(rf'^\s+{provider}:\s*\{{([^}}]*)\}}', ai, re.M)
        values = dict(DEFAULT_CAPS)
        if found:
            for key, value in re.findall(r'(per_5h|per_week)\s*:\s*(\d+)', found.group(1)):
                values[key] = int(value)
        caps[provider] = values
    return {'enabled': bool(enabled and enabled.group(1) == 'true'),
            'python': (python.group(1).strip() if python else r'D:\YT-Automation-env\histold-local\Scripts\python.exe'),
            'caps': caps}


def mode(config) -> dict:
    settings = configured(config)
    switch = read_json(control(config) / 'local-production.json') if (control(config) / 'local-production.json').is_file() else None
    if isinstance(switch, dict) and isinstance(switch.get('enabled'), bool):
        return {'local': switch['enabled'], 'source': 'studio_hq', 'changed_at': switch.get('at')}
    return {'local': settings['enabled'], 'source': 'config', 'changed_at': None}


def _iso(value: float | None) -> str | None:
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if value else None


def plans(config, now: float) -> dict:
    caps = configured(config)['caps']
    state = read_json(control(config) / 'plan-ai.json') if (control(config) / 'plan-ai.json').is_file() else {}
    state = state if isinstance(state, dict) else {}
    out = {}
    for provider in ('claude', 'codex'):
        used = [t for t in (state.get('usage') or {}).get(provider, []) if isinstance(t, (int, float))]
        resting = (state.get('cooldown') or {}).get(provider) or 0
        recent = sorted(t for t in used if now - t < 5 * 3600)
        week = sorted(t for t in used if now - t < 7 * 86400)
        out[provider] = {'used_5h': len(recent), 'used_week': len(week), **caps[provider],
                         'resting_until': _iso(resting) if resting > now else None,
                         'frees_at': _iso(recent[0] + 5 * 3600) if len(recent) >= caps[provider]['per_5h'] else None,
                         'last_error': (state.get('last_error') or {}).get(provider)}
        out[provider]['ready'] = not out[provider]['resting_until'] and len(recent) < caps[provider]['per_5h'] \
            and len(week) < caps[provider]['per_week']
    try:
        import urllib.request
        with urllib.request.urlopen('http://127.0.0.1:11434/api/tags', timeout=1) as response:
            models = [m.get('name') for m in json.loads(response.read().decode()).get('models', [])]
        out['qwen'] = {'ready': any(str(m).startswith('qwen3:8b') for m in models), 'running': True}
    except Exception:  # noqa: BLE001 - Ollama off is a normal state
        out['qwen'] = {'ready': False, 'running': False}
    return out


def _events(path: Path, limit: int = 12) -> list[dict]:
    try:
        lines = path.read_text(encoding='utf-8', errors='replace').splitlines()[-limit * 3:]
    except OSError:
        return []
    out = []
    for line in lines:
        match = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (.+)', line)
        if match:
            at = datetime.strptime(match.group(1), '%Y-%m-%d %H:%M:%S').replace(tzinfo=IST)
            text = re.sub(r'https?://\S+', '', match.group(2)).strip()[:160]
            if out and out[-1]['text'] == text:
                continue
            out.append({'at': at.isoformat(), 'text': text})
    return out[-limit:][::-1]


STEP_NAMES = {'research': 'Researching the story', 'script': 'Writing the script', 'voiceover': 'Recording the narration',
              'align': 'Timing every word', 'audio_check': 'Checking the audio', 'captions': 'Making captions',
              'assets': 'Gathering portraits and maps', 'render': 'Rendering scenes', 'compose': 'Assembling the video',
              'quality': 'Quality checks', 'thumbnail': 'Making the thumbnail', 'upload': 'Uploading'}
_NOISE = re.compile(r'^\s*(?:[{}\[\]"(]|=== |STATUS: |Resuming stage|Targeting|No incomplete|warnings?\.warn|\S+Warning)')


# Usual length of each step in seconds; weights the whole-video bar and estimates steps that report no counts.
TYPICAL = {'research': 120, 'script': 60, 'voiceover': 60, 'align': 20, 'audio_check': 5, 'captions': 5, 'assets': 30,
           'render': 330, 'compose': 40, 'quality': 20, 'thumbnail': 20, 'upload': 60}


def live_log(path: Path) -> tuple[str | None, str | None, str | None, dict]:
    """(step in progress, latest progress line, problem being retried, counts) from the end of the run's live log."""
    try:
        with path.open('rb') as handle:
            handle.seek(max(0, path.stat().st_size - 20000))
            lines = handle.read().decode('utf-8', errors='replace').splitlines()
    except OSError:
        return None, None, None, {}
    step, detail, problem, counts = None, None, None, {}
    for line in lines:
        found = re.match(r'\s*→ step: ([a-z_]+)', line)
        failed = re.match(r"\s*!! step '[a-z_]+' failed: (.+)", line)
        written = re.match(r'\s*plan scenes: (\d+)/\d+', line)
        if found:
            step, detail, problem, counts = found.group(1), None, None, {}
        if written:
            counts['written'] = counts.get('written', 0) + int(written.group(1))
        elif re.match(r'\s*✓ scene \d+ rendered', line):
            counts['rendered'] = counts.get('rendered', 0) + 1
        elif re.match(r'\s*(plan scene repair|claude_motion repair)', line):
            counts['repaired'] = True
        if found:
            continue
        elif failed:
            problem = re.sub(r'https?://\S+', '', failed.group(1)).replace('; refusing old-style fallback', '').strip()[:200]
        elif line.strip() and not _NOISE.match(line) and line.startswith('  ') and not line.lstrip().startswith('!!'):
            detail = re.sub(r'https?://\S+', '', line).strip()[:160]
    return step, detail, problem, counts


def step_fraction(step: str, counts: dict, scenes: int, elapsed: float) -> tuple[float, bool]:
    """(share of the step done, measured?) Scene work is counted; other steps are estimated from time, capped at 95%."""
    if step == 'render' and scenes:
        written = min(counts.get('written', 0), scenes) / scenes
        rendered = min(counts.get('rendered', 0), scenes) / scenes
        return min(0.99, 0.6 * written + (0.1 if counts.get('repaired') or rendered else 0) + 0.3 * rendered), True
    return min(0.95, max(0.0, elapsed) / TYPICAL.get(step, 60)), False


def video_usage(root: Path, job: str, since: float) -> dict | None:
    """Plan calls, tokens and time for one video, from the pipeline's usage events (only files written since it started)."""
    folder = root / 'data' / 'private' / 'studio-hq' / 'events'
    steps: dict[str, dict] = {}
    try:
        entries = [e for e in os.scandir(folder) if e.name.endswith('.json') and e.stat().st_mtime >= since - 120]
    except OSError:
        return None
    for entry in entries:
        row = read_json(Path(entry.path))
        if not isinstance(row, dict) or row.get('job_id') != job or row.get('kind') != 'api_usage':
            continue
        usage = row.get('usage') if isinstance(row.get('usage'), dict) else {}
        number = lambda key: usage.get(key) if isinstance(usage.get(key), (int, float)) else 0
        item = steps.setdefault(str(row.get('stage') or 'other'), {'calls': 0, 'failed': 0, 'input': 0, 'cached': 0,
                                                                     'output': 0, 'seconds': 0.0, 'providers': []})
        item['calls'] += 1
        item['failed'] += row.get('status') != 'ok'
        item['input'] += number('input_tokens') + number('cache_creation_input_tokens') + number('prompt_tokens')
        item['cached'] += number('cache_read_input_tokens')
        item['output'] += number('output_tokens') + number('completion_tokens')
        item['seconds'] += row.get('elapsed_seconds') if isinstance(row.get('elapsed_seconds'), (int, float)) else 0
        provider = str(row.get('provider') or '')
        if provider and provider not in item['providers']:
            item['providers'].append(provider)
    if not steps:
        return None
    order = [step for step in STEP_NAMES if step in steps] + sorted(set(steps) - set(STEP_NAMES))
    rows = [{'step': step, 'label': STEP_NAMES.get(step, step.replace('_', ' ').capitalize()), **steps[step],
             'seconds': round(steps[step]['seconds'], 1)} for step in order]
    total = {key: sum(row[key] for row in rows) for key in ('calls', 'failed', 'input', 'cached', 'output')}
    total['seconds'] = round(sum(row['seconds'] for row in rows), 1)
    return {'steps': rows, 'total': total}


def current_run(config, now: float) -> dict | None:
    lock = control(config) / 'local-publish.lock'
    running = lock.is_file() and now - lock.stat().st_mtime < LOCK_FRESH
    status = read_json(control(config) / 'local-publish-status.json') if (control(config) / 'local-publish-status.json').is_file() else None
    status = status if isinstance(status, dict) else {}
    root = Path(status['root']) if running and isinstance(status.get('root'), str) else clone(config)
    stages = [p for p in (root / 'output').glob('2*_*') if (p / 'state.json').is_file()]
    if not stages:
        return {'running': running} if running else None
    stage = max(stages, key=lambda p: p.stat().st_mtime)
    step, detail, problem, counts = live_log(Path(status['log'])) if running and isinstance(status.get('log'), str) else (None, None, None, {})
    state = read_json(stage / 'state.json')
    if not isinstance(state, dict):
        return {'running': running} if running else None
    done = [s for s in state.get('done') or [] if isinstance(s, str)]
    target = [s for s in state.get('target_steps') or [] if isinstance(s, str)]
    pending = [s for s in target if s not in done]
    title = ((state.get('script') or {}).get('title') if isinstance(state.get('script'), dict) else None) or state.get('title')
    created = stage.name[:15]
    try:
        started = datetime.strptime(created, '%Y%m%d_%H%M%S').replace(tzinfo=IST).isoformat()
    except ValueError:
        started = None
    current = None
    if running:  # the live log names the step being worked on; the saved state only knows finished steps
        current = step if step in STEP_NAMES and step not in done else (pending[0] if pending else None)
    progress = None
    if running and current:
        scenes = len((state.get('script') or {}).get('segments') or []) if isinstance(state.get('script'), dict) else 0
        fraction, measured = step_fraction(current, counts, scenes, now - (stage / 'state.json').stat().st_mtime)
        weights = [TYPICAL.get(s, 60) for s in target]
        finished = sum(TYPICAL.get(s, 60) for s in target if s in done)
        overall = (finished + TYPICAL.get(current, 60) * fraction) / sum(weights) if weights else None
        progress = {'step': round(fraction, 3), 'measured': measured, 'overall': round(overall, 3) if overall is not None else None,
                    'counts': {'scenes': scenes, 'written': min(counts.get('written', 0), scenes),
                               'rendered': min(counts.get('rendered', 0), scenes)} if current == 'render' and scenes else None}
    return {'running': running, 'stage': stage.name, 'format': state.get('fmt'), 'title': str(title or '')[:160] or None,
            'started': started, 'updated': _iso((stage / 'state.json').stat().st_mtime),
            'done': done, 'current': current, 'current_label': STEP_NAMES.get(current) if current else None,
            'detail': detail if running else None, 'problem': problem if running else None, 'progress': progress, 'kind': status.get('kind') if running else None,
            'usage': video_usage(root, stage.name, datetime.fromisoformat(started).timestamp() if started else now - 6 * 3600),
            'complete': bool(state.get('complete')),
            'dry_run': target == ['research', 'script'], 'preview': bool(state.get('no_upload')),
            'error': str(state.get('error'))[:240] if state.get('error') else None,
            'youtube_url': ('https://www.youtube.com/watch?v=' + state['youtube_id']) if isinstance(state.get('youtube_id'), str) else None}


def setup(config) -> list[dict]:
    root, secrets = clone(config), home(config) / 'secrets'
    env_root = Path(configured(config)['python']).parents[2]
    return [
        {'name': 'Production tools', 'ok': Path(configured(config)['python']).is_file()},
        {'name': 'Narration voice (Kokoro)', 'ok': (env_root / 'histold-kokoro' / 'Scripts' / 'python.exe').is_file()},
        {'name': 'YouTube upload sign-in', 'ok': (root / 'secrets' / 'token.json').is_file()},
        {'name': 'Facebook and Instagram token', 'ok': any((secrets / name).is_file() for name in ('meta-publish-token.txt', 'meta-earnings-token.txt'))},
    ]


def snapshot(config) -> dict | None:
    if not config.get('dashboard_histold_index_root') and not config.get('roots', {}).get('histold'):
        return None
    now = time.time()
    retry = read_json(control(config) / 'local-publish-retry.json') if (control(config) / 'local-publish-retry.json').is_file() else None
    waiting = None
    if isinstance(retry, dict) and retry.get('format') in ('short', 'long'):
        waiting = {'format': retry['format'], 'retry_at': _iso(float(retry.get('retry_at') or 0)),
                   'reason': str(retry.get('reason') or '')[:200], 'attempts': int(retry.get('attempts') or 1)}
    runner_log = control(config) / 'local-publish' / 'runner.log'
    return {'mode': mode(config), 'run': current_run(config, now), 'plans': plans(config, now), 'waiting': waiting,
            'setup': setup(config), 'events': _events(runner_log),
            'heartbeat': _iso(runner_log.stat().st_mtime) if runner_log.is_file() else None}


# ---------------------------------------------------------------- controls

def check(body) -> str:
    action = body.get('action')
    if action == 'histold_local_mode':
        if type(body.get('local')) is not bool:
            raise ValueError('Choose where Histold videos are made.')
        return 'local' if body['local'] else 'cloud'
    if action == 'histold_local_run':
        if body.get('format') not in ('short', 'long'):
            raise ValueError('Choose a Short or a long video.')
        return body['format']
    if action == 'histold_local_retry':
        if body.get('choice') not in ('now', 'cancel'):
            raise ValueError('Choose whether to start or drop the waiting video.')
        return body['choice']
    raise ValueError('Unknown Histold live action.')


def _start_runner(config, args: list[str]) -> None:
    python = Path(configured(config)['python'])
    if not python.is_file():
        raise ValueError('The local production tools are not installed on this PC.')
    lock = control(config) / 'local-publish.lock'
    if lock.is_file() and time.time() - lock.stat().st_mtime < LOCK_FRESH:
        raise ValueError('A Histold video is already being made on this PC.')
    flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == 'nt' else 0
    subprocess.Popen([str(python), str(clone(config) / 'scripts' / 'run_local_publish.py')] + args, cwd=clone(config),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)


def perform(config, body) -> str:
    action = body['action']
    folder = control(config)
    if action == 'histold_local_mode':
        folder.mkdir(parents=True, exist_ok=True)
        atomic_json(folder / 'local-production.json', {'enabled': body['local'], 'by': 'studio_hq',
                                                       'at': datetime.now(timezone.utc).isoformat()})
        if read_json(folder / 'local-production.json').get('enabled') is not body['local']:
            raise RuntimeError('Could not verify the production switch')
        return ('Histold videos are now made on this PC with your Claude and Codex plans, from the next publish hour.'
                if body['local'] else 'Histold videos are now made in the paid cloud workflow, from the next publish hour.')
    if action == 'histold_local_run':
        if not mode(config)['local']:
            raise ValueError('Turn on local production first.')
        _start_runner(config, ['--format', body['format']])
        return f"Started making one Histold {'Short' if body['format'] == 'short' else 'long video'} on this PC. Progress appears here."
    retry = folder / 'local-publish-retry.json'
    waiting = read_json(retry) if retry.is_file() else None
    if not isinstance(waiting, dict) or waiting.get('format') not in ('short', 'long'):
        raise ValueError('No Histold video is waiting.')
    if body['choice'] == 'cancel':
        retry.unlink(missing_ok=True)
        return 'The waiting Histold video was dropped. The next scheduled slot runs as usual.'
    _start_runner(config, ['--format', waiting['format'], '--retry'])
    return 'Started the waiting Histold video now. If the plans are still used up, it keeps waiting.'
