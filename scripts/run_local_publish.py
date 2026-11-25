#!/usr/bin/env python3
"""Make and publish one Histold video on this laptop, using the owner's Claude/Codex plans (and Qwen).

Started by scripts/dispatch.py at a publish hour when ``local_production.enabled`` is on. It
does what .github/workflows/publish.yml does, in the dedicated clone (D:\\histold\\YT-Automation):
claim the slot, run the pipeline step loop (pre-upload steps retried twice), record the
publication state in the repo, and give the slot back if nothing reached a platform.

Paid credits stay available: when the plans cannot make the video (limits used up, a tool
missing, a step failing before upload), the claim is released and the paid cloud workflow is
dispatched for the same format, so the channel keeps its slot. ``--no-fallback`` disables that.

Usage:
  python scripts/run_local_publish.py --format short --slot 2026-11-22T11     (scheduled)
  python scripts/run_local_publish.py --format short --dry-run                 (research + script only)
  python scripts/run_local_publish.py --format short --no-upload               (render a preview)
  python scripts/run_local_publish.py --format short --slot 2026-11-22T11 --ahead
      (made ahead: render now, claim the slot when its hour starts, upload straight away)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HOME = Path(os.environ.get('HISTOLD_HOME', r'D:\histold'))
ENV_ROOT = Path(os.environ.get('HISTOLD_LOCAL_ENV', r'D:\YT-Automation-env'))
LOCK = HOME / 'control' / 'local-publish.lock'
RETRY = HOME / 'control' / 'local-publish-retry.json'   # a video waiting for the plans to reset
STATUS = HOME / 'control' / 'local-publish-status.json'  # the run in progress, for Studio HQ
LOG_DIR = HOME / 'control' / 'local-publish'
AHEAD_DIR = HOME / 'control' / 'made-ahead'           # one file per slot made ahead, read by scripts/dispatch.py
CLAIM_WORKTREE = ENV_ROOT / 'claim-worktree'           # separate checkout, so a claim never resets this one
PRE_UPLOAD_RETRIES = 2
BUDGET_SECONDS = 5 * 3600


def log(*parts):
    line = datetime.now().strftime('%Y-%m-%d %H:%M:%S ') + ' '.join(str(p) for p in parts)
    print(line, flush=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with (LOG_DIR / 'runner.log').open('a', encoding='utf-8') as handle:
        handle.write(line + '\n')


def run(cmd, env=None, check=False, timeout=None, capture=True, cwd=None):
    result = subprocess.run(cmd, cwd=cwd or ROOT, env=env, capture_output=capture, text=True, encoding='utf-8',
                            errors='replace', timeout=timeout, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if check and result.returncode:
        raise RuntimeError(f'{cmd[0]} failed: {(result.stderr or result.stdout)[-300:]}')
    return result


def gh() -> str:
    return os.environ.get('GH_EXE') or shutil.which('gh') or r'C:\Program Files\GitHub CLI\gh.exe'


def secret_text(*names) -> str | None:
    for name in names:
        path = HOME / 'secrets' / name
        if path.is_file():
            value = path.read_text(encoding='utf-8').strip()
            if value:
                return value
    return None


def _started(when) -> bool:
    """True once a 'start_after' time has passed (or none is set); an unreadable value means not yet."""
    if not when:
        return True
    try:
        start = datetime.fromisoformat(str(when))
    except ValueError:
        return False
    return datetime.now(start.tzinfo) >= start


def tool_env(cfg) -> tuple[dict, list[str]]:
    """Environment for the pipeline, and what is missing for a full publish."""
    env = dict(os.environ)
    missing = []
    local = cfg.get('local_production', {}) or {}
    ffmpeg_dir = Path(local.get('ffmpeg_dir') or r'D:\Clips-Kitty\vendor\ffmpeg')
    if (ffmpeg_dir / 'ffmpeg.exe').is_file():
        env['PATH'] = str(ffmpeg_dir) + os.pathsep + env.get('PATH', '')
    elif not shutil.which('ffmpeg'):
        missing.append('ffmpeg')
    env.update({
        'HISTOLD_AI_BACKEND': 'plan', 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8', 'PYTHONUNBUFFERED': '1',
        'PLAYWRIGHT_BROWSERS_PATH': str(ENV_ROOT / 'ms-playwright'), 'HF_HOME': str(ENV_ROOT / 'hf-cache'),
        'TORCH_HOME': str(ENV_ROOT / 'torch-cache'), 'TEMP': str(ENV_ROOT / 'tmp'), 'TMP': str(ENV_ROOT / 'tmp'),
        'PUBLISH_WORKFLOW': 'local', 'PRESERVE_STAGE_ARTIFACT': 'true',
        'GITHUB_REPOSITORY': cfg.get('local_production.repository', 'kumarvnsh/YT-Automation'),
        'YOUTUBE_CLIENT_SECRET_FILE': 'secrets/client_secret.json',
    })
    # Higher-quality encoding on the laptop only; cloud runs never read local_production.
    encode = local.get('encode', {}) or {}
    for var, key in (('FFMPEG_PRESET', 'preset'), ('FFMPEG_CRF', 'crf'), ('FRAME_JPEG_QUALITY', 'frame_quality')):
        if encode.get(key) not in (None, '') and not os.environ.get(var):
            env[var] = str(encode[key])
    workers = local.get('render_workers')
    if workers not in (None, '') and not os.environ.get('HISTOLD_RENDER_WORKERS'):
        env['HISTOLD_RENDER_WORKERS'] = str(workers)  # scenes captured at once (src/claude_motion.py)
    if local.get('textured_style', True) and not os.environ.get('HISTOLD_TEXTURE'):
        env['HISTOLD_TEXTURE'] = '1'  # textured-illustration kit and brief (src/cinematic_texture.css)
    style = str(local.get('scene_style', 'auto') or 'off').strip().lower()
    if style != 'off' and not os.environ.get('HISTOLD_STYLE'):
        env['HISTOLD_STYLE'] = style  # cut-paper / flat-lit scene kits (src/cinematic_motion.py)
    painted = local.get('painted_scenes', {}) or {}
    if painted.get('enabled') and not os.environ.get('HISTOLD_PAINTED') and _started(painted.get('start_after')):
        env['HISTOLD_PAINTED'] = '1'  # Gemini/Codex paint a picture per scene on the plan (src/painted_scenes.py)
        if not os.environ.get('HISTOLD_PAINTED_MODE'):
            env['HISTOLD_PAINTED_MODE'] = str(painted.get('mode') or 'inset')  # inset = framed picture, not a background
    (ENV_ROOT / 'tmp').mkdir(parents=True, exist_ok=True)
    kokoro = ENV_ROOT / 'histold-kokoro' / 'Scripts' / 'python.exe'
    if kokoro.is_file():
        env['KOKORO_PYTHON'] = str(kokoro)
    hyperframes = ROOT / 'node_modules' / '.bin' / 'hyperframes.cmd'
    if hyperframes.is_file():
        env['HYPERFRAMES_CLI'] = str(hyperframes)
    for key, files, required in (('PEXELS_API_KEY', ('pexels-api-key.txt',), False),
                                 # The analytics token is the same non-expiring system-user token, with publish rights.
                                 ('META_ACCESS_TOKEN', ('meta-publish-token.txt', 'meta-earnings-token.txt'), True)):
        value = os.environ.get(key) or secret_text(*files)
        if value:
            env[key] = value
        elif required:
            missing.append(key)  # without it Facebook and Instagram posts would silently stop
        else:
            log(f'{key} not set; stock footage fallback is unavailable')
    token = run([gh(), 'auth', 'token']).stdout.strip()
    if token:
        env['GITHUB_TOKEN'] = token  # hosts the mp4 as a release asset for Meta, as in the cloud
    else:
        missing.append('GitHub sign-in')
    for name, var in (('HISTOLD_3D', 'VIDEO_3D'), ('VIDEO_MODE', 'VIDEO_MODE')):
        value = run([gh(), 'variable', 'get', name]).stdout.strip()
        if value:
            env[var] = value
    env.setdefault('VIDEO_3D', 'off')
    if not (ROOT / 'secrets' / 'token.json').is_file():
        missing.append('YouTube upload sign-in')
    return env, missing


def git_sync():
    run(['git', 'fetch', '-q', 'origin'], check=True, timeout=120)
    run(['git', 'reset', '-q', '--hard', 'origin/master'], check=True, timeout=120)


def stream(cmd, env, timeout, path: Path) -> subprocess.CompletedProcess:
    """Run one pipeline step, writing its output to the run log as it happens so Studio HQ can
    show the step in progress (for example how many scenes are rendered)."""
    lines, started = [], time.time()
    with path.open('a', encoding='utf-8') as handle:
        process = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                   encoding='utf-8', errors='replace', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        for line in process.stdout:
            lines.append(line)
            handle.write(line)
            handle.flush()
            if time.time() - started > timeout:
                process.kill()
                break
        process.wait()
    return subprocess.CompletedProcess(cmd, process.returncode, stdout=''.join(lines), stderr='')


def step_loop(args, env, cmd=None) -> tuple[str, str]:
    if cmd is None:
        cmd = [sys.executable, '-m', 'src.main', '--format', args.format, '--step']
        if getattr(args, 'stage', None):  # publish a finished preview: only the upload step runs
            cmd = [sys.executable, '-m', 'src.main', '--stage', args.stage, '--force-upload', '--step']
        if args.dry_run:
            cmd.append('--dry-run')
        if args.no_upload:
            cmd.append('--no-upload')
    retries, end, last = 0, time.time() + BUDGET_SECONDS, ''
    while time.time() < end:
        result = stream(cmd, env, max(60, int(end - time.time())), LOG_DIR / f'{args.stamp}.log')
        out = (result.stdout or '') + (result.stderr or '')
        last = out
        if 'STATUS: DONE' in out:
            return 'DONE', out
        if result.returncode or 'STATUS: ERROR' in out:
            failed = re.findall(r"!! step '([a-z_]+)' failed", out)
            step = failed[-1] if failed else ''
            if retries < PRE_UPLOAD_RETRIES and step and step != 'upload' and 'No plan provider could' not in out:
                retries += 1
                log(f"step '{step}' failed; retrying ({retries} of {PRE_UPLOAD_RETRIES}) in 60 seconds")
                time.sleep(60)
                continue
            return 'ERROR', out
    return 'TIMEOUT', last


def prepare_preview(source: Path) -> str:
    """Bring a finished, quality-passed preview into this checkout's output folder for upload."""
    state = json.loads((source / 'state.json').read_text(encoding='utf-8'))
    quality = json.loads((source / 'quality.json').read_text(encoding='utf-8'))
    if not state.get('complete') or not state.get('no_upload') or state.get('youtube_id'):
        raise SystemExit('Only a finished preview that was never uploaded can be published this way.')
    if quality.get('passed') is not True:
        raise SystemExit('That preview did not pass its quality checks.')
    target = ROOT / 'output' / source.name
    if source.resolve() != target.resolve():
        shutil.copytree(source, target, dirs_exist_ok=True)
    log('publishing preview', source.name)
    return source.name


def clear_stale_locks(max_age: int = 120) -> None:
    """A run killed mid-step (power cut, forced restart) leaves its media reservation behind, which
    blocks resuming that video. Only the runner writes these stages and it holds LOCK, so an old
    reservation on an unfinished stage is stale."""
    for reservation in (ROOT / 'output').glob('*/.studio-hq-media-lock'):
        try:
            state = json.loads((reservation.parent / 'state.json').read_text(encoding='utf-8'))
            if not state.get('complete') and time.time() - reservation.stat().st_mtime > max_age:
                reservation.rmdir()
                log('cleared a stale media reservation from', reservation.parent.name)
        except (OSError, ValueError):
            pass


def newest_stage() -> Path | None:
    stages = [p for p in (ROOT / 'output').glob('*_*') if (p / 'state.json').is_file()]
    return max(stages, key=lambda p: p.stat().st_mtime) if stages else None


def record_state():
    """Union-merge the publication state into master, like the workflow's 'Commit dedup state'."""
    files = ['data/used_topics.json', 'data/pending_approvals.json', 'data/published_index.json',
             'data/topic_reservations.json']
    for _ in range(3):
        run(['git', 'fetch', '-q', 'origin', 'master'], timeout=120)
        run([sys.executable, 'scripts/merge_json_state.py', '--ref', 'origin/master'], timeout=120)
        run(['git', 'reset', '--mixed', '-q', 'origin/master'])
        run(['git', 'add', '--'] + [f for f in files if (ROOT / f).exists()])
        if run(['git', 'diff', '--cached', '--quiet']).returncode == 0:
            return 'nothing to record'
        run(['git', '-c', 'user.name=github-actions[bot]', '-c', 'user.email=github-actions[bot]@users.noreply.github.com',
             'commit', '-q', '-m', 'chore: update used topics / approval queue (local run) [skip ci]'])
        if run(['git', 'push', '-q', 'origin', 'HEAD:master'], timeout=120).returncode == 0:
            return 'recorded'
    return 'could not push'


def claim(args, env, cwd=None) -> tuple[bool, str]:
    with tempfile.NamedTemporaryFile('w', delete=False, suffix='.env', dir=ENV_ROOT / 'tmp') as handle:
        github_env = handle.name
    claim_env = {**env, 'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_ENV': github_env,
                 'PUBLISH_SCHEDULED': 'true' if args.slot else 'false', 'DISPATCH_SLOT': args.slot or '',
                 'VIDEO_FORMAT': args.format}
    key = f'slot-{args.slot}' if args.slot else f'local-{args.stamp}'
    result = run([sys.executable, 'scripts/claim_histold_publish.py', args.format, key], env=claim_env, timeout=300, cwd=cwd)
    Path(github_env).unlink(missing_ok=True)
    log('claim', key, (result.stdout or result.stderr).strip()[-200:])
    return result.returncode == 0, key


def notify(text):
    try:
        from src import telegram_control
        telegram_control.notify(text)
    except Exception:  # noqa: BLE001 - notifications never block publishing
        pass


def defer(args, reason, key=None, env=None):
    """Plans used up: give the slot back and try again on the plans after the first reset.

    No paid credits are spent. scripts/dispatch.py starts the retry once ``retry_at`` passes;
    it resumes the unfinished stage, so finished research and script are not redone.
    """
    from src.config import load_config
    from src import plan_ai
    stage = newest_stage()
    if key:
        run([sys.executable, 'scripts/claim_histold_publish.py', '--release', key, str(stage or '')], env=env, timeout=300)
    if args.dry_run or args.no_upload:
        log('plans used up; preview not retried:', reason)
        return 1
    retry_at = plan_ai.next_ready_at(load_config())
    pending = read_retry() or {}
    RETRY.parent.mkdir(parents=True, exist_ok=True)
    RETRY.write_text(json.dumps({'format': args.format, 'retry_at': retry_at, 'reason': reason[:200],
                                 'attempts': int(pending.get('attempts') or 0) + 1,
                                 'first_slot': pending.get('first_slot') or args.slot or args.stamp}, indent=1), encoding='utf-8')
    when = datetime.fromtimestamp(retry_at).strftime('%d %b, %I:%M %p')
    log('deferred until', when, '-', reason)
    notify(f'⏳ Sir, your Claude and Codex limits are used up, so this Histold {args.format} is waiting. '
           f'I will make it on your plans after they reset, around {when} IST. No paid credits are used.')
    return 0


def read_retry() -> dict | None:
    try:
        data = json.loads(RETRY.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) and data.get('format') in ('short', 'long') else None
    except (OSError, ValueError):
        return None


def fall_back(args, reason, key=None, env=None):
    stage = newest_stage()
    if key:
        run([sys.executable, 'scripts/claim_histold_publish.py', '--release', key, str(stage or '')], env=env, timeout=300)
    if args.no_fallback or args.dry_run or args.no_upload:
        log('fallback skipped:', reason)
        notify(f'⚠️ Sir, the local Histold run could not finish ({reason}). No cloud fallback was started.')
        return 1
    dispatched = run([gh(), 'workflow', 'run', 'publish.yml', '--ref', 'master', '-f', f'format={args.format}'],
                     env={**os.environ, 'GH_PROMPT_DISABLED': '1'}, timeout=120)
    ok = dispatched.returncode == 0
    log('cloud fallback', 'dispatched' if ok else 'FAILED', reason)
    notify(('☁️ Sir, your plans could not make this Histold video (' + reason + '), so the paid cloud run is making it instead.')
           if ok else ('❌ Sir, the local Histold run failed (' + reason + ') and the cloud fallback could not be started.'))
    return 0 if ok else 1


def finish(args, status: str, out: str, key, env) -> int:
    """Record a finished run, or hand a failed one to the retry or the paid cloud run."""
    if status == 'DONE':
        if not (args.dry_run or args.no_upload):
            log('state', record_state())
        return 0
    failed = re.findall(r"!! step '([a-z_]+)' failed: (.*)", out)
    step, error = failed[-1] if failed else ('', status)
    if step == 'upload':
        notify('❌ Sir, the local Histold upload failed (' + error[:160] + '). I did not retry it, to avoid a double post.')
        return 1
    if 'No plan provider could' in out:
        return defer(args, f"step '{step}': plans used up", key, env)
    return fall_back(args, (f"step '{step}' failed: " if step else '') + error[:160], key, env)


# --------------------------------------------------------------------------- #
# Made ahead: the video is rendered before its slot and uploaded the moment the slot opens
# --------------------------------------------------------------------------- #
def slot_start(slot: str) -> datetime:
    return datetime.strptime(slot, '%Y-%m-%dT%H').replace(tzinfo=timezone.utc)


def ahead_file(slot: str) -> Path:
    return AHEAD_DIR / f'{slot}.json'


def write_ahead(slot: str, **fields) -> None:
    """Progress of a made-ahead video. scripts/dispatch.py reads it at the slot hour to know whether this
    run still owns the slot or the usual run at the hour has to make the video."""
    path = ahead_file(slot)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        data = {}
    data.update(fields, slot=slot, updated=datetime.now(timezone.utc).isoformat())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1), encoding='utf-8')


def hand_back(stage: str | None) -> None:
    """Turn an unfinished made-ahead preview into a normal stage, so the run at the slot hour (or the retry
    after a plan reset) resumes it with its finished research and script and uploads it."""
    if not stage:
        return
    path = ROOT / 'output' / stage / 'state.json'
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
        if state.get('no_upload') and not state.get('complete'):
            state['no_upload'] = False
            path.write_text(json.dumps(state, indent=2), encoding='utf-8')
            log('handed back the unfinished made-ahead video', stage)
    except (OSError, ValueError):
        pass


def claim_worktree() -> Path | None:
    """A second checkout for the slot claim. The claim resets its checkout to origin/master, which must
    not happen in this one while a video is being made in it."""
    if (CLAIM_WORKTREE / '.git').exists():
        return CLAIM_WORKTREE
    run(['git', 'worktree', 'prune'], timeout=120)
    result = run(['git', 'worktree', 'add', '--detach', str(CLAIM_WORKTREE), 'origin/master'], timeout=300)
    if result.returncode:
        log('claim checkout unavailable:', (result.stderr or result.stdout).strip()[-200:])
        return None
    return CLAIM_WORKTREE


class SlotClaim(threading.Thread):
    """Claims the slot as soon as its hour starts, while the video may still be rendering."""

    def __init__(self, args, env, cwd: Path, margin: float = 20):
        super().__init__(daemon=True)
        self.args, self.env, self.cwd, self.margin = args, env, cwd, margin
        self.cancelled = threading.Event()
        self.ok, self.key = None, None

    def run(self):
        wait = (slot_start(self.args.slot) - datetime.now(timezone.utc)).total_seconds() + self.margin
        if self.cancelled.wait(max(0.0, wait)):
            return
        try:
            self.ok, self.key = claim(self.args, self.env, cwd=self.cwd)
        except Exception as exc:  # noqa: BLE001 - an unclaimed slot keeps the video as a preview
            log('slot claim failed:', exc)
            self.ok = False

    def stop(self) -> None:
        """Cancel a claim that has not started; one already running is waited for."""
        self.cancelled.set()
        self.join()


_STAGE_RE = re.compile(r'Created stage: (\S+)|"stage":\s*"([^"]+)"')


def produce_ahead(args, env) -> tuple[str, str, str | None]:
    """Make a new preview stage (never resuming another video) step by step, without its upload."""
    path = LOG_DIR / f'{args.stamp}.log'
    first = stream([sys.executable, '-m', 'src.main', '--format', args.format, '--no-upload', '--fresh', '--step'],
                   env, BUDGET_SECONDS, path)
    out = (first.stdout or '') + (first.stderr or '')
    found = [created or named for created, named in _STAGE_RE.findall(out)]
    stage = found[-1] if found else None
    if not stage:
        return 'ERROR', out, None
    write_ahead(args.slot, stage=stage)
    if 'STATUS: DONE' in out:
        return 'DONE', out, stage
    status, rest = step_loop(args, env, [sys.executable, '-m', 'src.main', '--stage', stage, '--step'])
    return status, out + rest, stage


def make_ahead(args, env) -> int:
    """Render the slot's video before its hour, then claim and upload it when the hour starts.

    Nothing is claimed early, so the claim's slot-hour and daily-cap rules apply unchanged. A video that
    fails before its slot is handed back to the usual run at the slot hour; one that fails after the claim
    is handled like any local run (plan reset retry or paid cloud run).
    """
    worktree = claim_worktree()
    if not worktree:
        write_ahead(args.slot, state='gave_up', reason='no claim checkout')
        return 0
    write_ahead(args.slot, state='making', format=args.format, started=datetime.now(timezone.utc).isoformat())
    watcher = SlotClaim(args, env, worktree)
    watcher.start()
    status, out, stage = produce_ahead(args, env)
    log('made ahead', args.format, 'for', args.slot, status, stage or '')
    if status != 'DONE':
        hand_back(stage)
        if watcher.is_alive():
            if (slot_start(args.slot) - datetime.now(timezone.utc)).total_seconds() > 15 * 60:
                watcher.stop()  # early enough that the usual run at the hour is still started
            else:
                watcher.join()  # the dispatcher may already have left this slot to us: claim it and fall back
        if not watcher.ok:  # no claim: the usual run at the slot hour takes it from here
            write_ahead(args.slot, state='gave_up', reason=status)
            return 0
        write_ahead(args.slot, state='failed')
        return finish(args, status, out, watcher.key, env)
    write_ahead(args.slot, state='waiting')
    if watcher.is_alive():
        log('video ready; waiting for the slot at', slot_start(args.slot).strftime('%H:%M UTC'))
    watcher.join()
    if not watcher.ok:
        write_ahead(args.slot, state='kept_as_preview')
        log('state', record_state())
        notify(f'ℹ️ Sir, the Histold {args.format} made ahead for {args.slot} UTC could not take its slot '
               "(for example, today's posting cap is reached). It is saved as a preview in Studio HQ.")
        return 0
    write_ahead(args.slot, state='publishing')
    args.stage = stage
    status, out = step_loop(args, env)
    log('made-ahead upload', status)
    write_ahead(args.slot, state='published' if status == 'DONE' else 'failed')
    return finish(args, status, out, watcher.key, env)


def own_ahead_stage(slot: str) -> None:
    """The usual run at a slot hour: an unfinished video made ahead for this slot (its run stopped,
    for example the laptop restarted) is handed back so this run resumes and uploads it."""
    try:
        data = json.loads(ahead_file(slot).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return
    if data.get('state') != 'published':
        hand_back(data.get('stage'))


AHEAD_LOCK_WAIT_MARGIN = 75 * 60   # a made-ahead video needs at least this long before its slot


def take_lock(wait_until: float = 0):
    """The single local-publish lock. A made-ahead run starts while the previous slot's video may still be
    uploading, so it waits for that run (until ``wait_until``) instead of giving up at once."""
    while True:
        try:
            return os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if time.time() - LOCK.stat().st_mtime >= BUDGET_SECONDS + 1800:
                    LOCK.unlink(missing_ok=True)  # left by a run that died
                    continue
            except FileNotFoundError:
                continue
            if time.time() >= wait_until:
                return None
            time.sleep(30)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--format', choices=('short', 'long'), required=True)
    ap.add_argument('--slot', default='', help='scheduled UTC hour (YYYY-MM-DDTHH) from the dispatcher')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--no-upload', action='store_true')
    ap.add_argument('--no-fallback', action='store_true')
    ap.add_argument('--no-sync', action='store_true', help='testing only: do not reset the clone to origin/master')
    ap.add_argument('--retry', action='store_true', help='make the video that waited for the plans to reset')
    ap.add_argument('--publish-stage', type=Path, help='upload a finished preview that passed its quality checks')
    ap.add_argument('--ahead', action='store_true',
                    help='with --slot: make the video before its hour, claim and upload it when the hour starts')
    args = ap.parse_args()
    args.stage = None
    if args.ahead and (not args.slot or args.dry_run or args.no_upload or args.retry or args.publish_stage):
        ap.error('--ahead needs a future --slot and no other mode')
    if args.ahead and slot_start(args.slot) <= datetime.now(timezone.utc):
        args.ahead = False  # started late (for example after sleep): this is now the usual slot run
    if args.retry:
        args.slot = ''  # the original hour has passed; the daily cap still applies to the claim
    args.stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    fd = take_lock(slot_start(args.slot).timestamp() - AHEAD_LOCK_WAIT_MARGIN if args.ahead else 0)
    if fd is None:
        log('another local publish is running')
        if args.ahead:
            write_ahead(args.slot, state='gave_up', reason='another local publish was still running')
        return 0
    try:
        if not args.no_sync:
            git_sync()
        clear_stale_locks()
        if args.publish_stage:
            args.stage = prepare_preview(args.publish_stage)
            args.no_fallback = True  # a specific video: never replace it with a different cloud-made one
            args.slot = ''
        from src.config import load_config
        from src import plan_ai
        cfg = load_config()
        env, missing = tool_env(cfg)
        ready = plan_ai.status(cfg)
        if not args.stage and not any(ready[p]['ready'] for p in ('claude', 'codex')):  # research needs one; a preview upload needs none
            if args.ahead:  # the usual run at the slot hour decides whether to wait for the reset
                write_ahead(args.slot, state='gave_up', reason='plans resting or capped')
                return 0
            return defer(args, 'Claude and Codex plans are both resting or capped')
        if args.retry:
            RETRY.unlink(missing_ok=True)
        if missing and not (args.dry_run or args.no_upload):
            if args.ahead:
                write_ahead(args.slot, state='gave_up', reason='local setup incomplete: ' + ', '.join(missing))
                return 0
            return fall_back(args, 'local setup incomplete: ' + ', '.join(missing))
        if args.ahead:
            STATUS.write_text(json.dumps({'root': str(ROOT), 'log': str(LOG_DIR / f'{args.stamp}.log'), 'format': args.format,
                                          'slot': args.slot, 'started': datetime.now(timezone.utc).isoformat(),
                                          'kind': 'publish (made ahead)'}), encoding='utf-8')
            log('local run', args.format, 'made ahead for slot', args.slot, 'start')
            return make_ahead(args, env)
        if args.slot and not args.stage:
            own_ahead_stage(args.slot)
        key = None
        if not (args.dry_run or args.no_upload):
            ok, key = claim(args, env)
            if not ok:
                if args.retry:
                    notify(f'ℹ️ Sir, the Histold {args.format} that waited for your plans was not made: '
                           "its slot can no longer be used (for example, today's posting cap is reached).")
                return 0  # slot already taken, cap reached, or window missed: nothing to fall back to
        STATUS.write_text(json.dumps({'root': str(ROOT), 'log': str(LOG_DIR / f'{args.stamp}.log'), 'format': args.format,
                                      'slot': args.slot or None, 'started': datetime.now(timezone.utc).isoformat(),
                                      'kind': 'script test' if args.dry_run else 'preview' if args.no_upload else 'publish'}),
                          encoding='utf-8')
        log('local run', args.format, 'slot', args.slot or '-', 'start')
        status, out = step_loop(args, env)
        log('local run', args.format, status)
        return finish(args, status, out, key, env)
    finally:
        os.close(fd)
        LOCK.unlink(missing_ok=True)


if __name__ == '__main__':
    raise SystemExit(main())
