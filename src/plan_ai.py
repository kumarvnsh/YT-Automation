"""Histold production AI on the owner's own plans: Claude Code, then Codex, then local Qwen.

Used only when the local runner sets HISTOLD_AI_BACKEND=plan (scripts/run_local_publish.py). The
GitHub workflow never sets it, so cloud runs keep the paid Anthropic API path unchanged; switching
back to paid credits is just running in the cloud again.

Each request goes down the provider order from config (``local_ai``):
- Claude Code (``claude -p``) and Codex (``codex exec``) run headless on the owner's subscriptions,
  in an empty sandbox folder, with no file or shell tools. Research may use only web search and
  web fetch. Usage is capped per rolling 5-hour and 7-day window, and a provider that reports a plan
  limit rests until the reset it reports (or one hour).
- Qwen (Ollama) is the last resort and only for jobs that need neither the web nor long code
  (``local_ai.qwen_for``), because an 8B model writes weaker scripts and cannot browse.

When every provider is unavailable, ``PlanUnavailable`` is raised. The step fails, and the local
runner gives the slot back to the paid cloud workflow, so the channel never misses a slot.
"""
from __future__ import annotations

import json
import re
import shutil
import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from .ai_providers import LIMIT_RE, LimitError, find_claude, find_codex, reset_time

HOME = Path(os.environ.get('HISTOLD_HOME', r'D:\histold'))
WINDOW_5H, WINDOW_WEEK = 5 * 3600, 7 * 86400
DEFAULT_ORDER = ('claude', 'codex', 'qwen')
DEFAULT_QWEN_FOR = ('assess', 'thumbnail', 'title')
DEFAULT_CAPS = {'per_5h': 60, 'per_week': 600}
TIMEOUTS = {'research': 1200, 'scenes': 1800, 'scene_repair': 900}
NAMES = {'claude': 'claude_plan', 'codex': 'codex_plan', 'qwen': 'qwen_local', 'antigravity': 'antigravity_plan'}
DEFAULT_AG_MODELS = ('claude-sonnet-5-5-high', 'gemini-4-neon-high')
DEFAULT_IMAGE_ORDER = ('antigravity', 'codex')
_LOCK = threading.Lock()


class PlanUnavailable(RuntimeError):
    """No plan provider could answer; the caller fails the step so the paid cloud run takes over."""


_LIVE = {'mtime': None, 'value': {}}


def _live_settings() -> dict:
    """``local_ai`` from the current config.yaml. These are settings for this PC (models, budgets),
    so they apply to a video already in progress, whose own settings are a snapshot from its start."""
    try:
        from .config import base_dir
        path = base_dir() / 'config.yaml'
        mtime = path.stat().st_mtime
        if _LIVE['mtime'] != mtime:
            import yaml
            _LIVE['value'] = (yaml.safe_load(path.read_text(encoding='utf-8')) or {}).get('local_ai') or {}
            _LIVE['mtime'] = mtime
    except Exception:  # noqa: BLE001 - fall back to the video's own settings
        return {}
    return _LIVE['value'] if isinstance(_LIVE['value'], dict) else {}


def _with_live(cfg):
    live = _live_settings()
    if not live:
        return cfg
    from .config import Config
    return Config({**(getattr(cfg, 'raw', None) or {}), 'local_ai': live})


def enabled(cfg=None) -> bool:
    return os.environ.get('HISTOLD_AI_BACKEND', '').strip().lower() == 'plan'


def _state_path() -> Path:
    return Path(os.environ.get('HISTOLD_PLAN_AI_STATE') or HOME / 'control' / 'plan-ai.json')


def _sandbox() -> Path:
    folder = Path(os.environ.get('HISTOLD_PLAN_AI_SANDBOX') or HOME / 'plan-ai-sandbox')
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _load() -> dict:
    try:
        state = json.loads(_state_path().read_text(encoding='utf-8'))
        if isinstance(state, dict):
            return {'usage': state.get('usage') or {}, 'cooldown': state.get('cooldown') or {},
                    'last_error': state.get('last_error') or {}}
    except (OSError, ValueError):
        pass
    return {'usage': {}, 'cooldown': {}, 'last_error': {}}


def _save(state: dict) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    state['usage'] = {p: [t for t in times if now - t < WINDOW_WEEK] for p, times in state['usage'].items()}
    temporary = path.with_name(path.name + f'.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(state, indent=1), encoding='utf-8')
    os.replace(temporary, path)


def _caps(cfg, provider: str) -> dict:
    caps = dict(DEFAULT_CAPS)
    configured = cfg.get(f'local_ai.{provider}') if cfg is not None else None
    if isinstance(configured, dict):
        caps.update({k: int(v) for k, v in configured.items() if k in caps and isinstance(v, (int, float))})
    return caps


def status(cfg=None) -> dict:
    """Readiness per plan provider, for the runner's pre-flight check and the dashboard."""
    state, now = _load(), time.time()
    out = {}
    for provider in ('claude', 'codex'):
        used = state['usage'].get(provider, [])
        caps = _caps(cfg, provider)
        resting = state['cooldown'].get(provider, 0)
        installed = bool(find_claude() if provider == 'claude' else find_codex())
        out[provider] = {'installed': installed, 'used_5h': sum(now - t < WINDOW_5H for t in used),
                         'used_week': len(used), **caps, 'resting_until': resting if resting > now else None,
                         'last_error': state['last_error'].get(provider)}
        info = out[provider]
        info['ready'] = installed and not info['resting_until'] and info['used_5h'] < caps['per_5h'] \
            and info['used_week'] < caps['per_week']
    out['antigravity'] = _antigravity_status(cfg)
    return out


def _ag_models(cfg) -> list[str]:
    models = cfg.get('local_ai.antigravity_models', DEFAULT_AG_MODELS) if cfg is not None else DEFAULT_AG_MODELS
    return [str(m) for m in models or ()]


def _antigravity_status(cfg) -> dict:
    """Antigravity reports its own quota per model; it is ready when the app is open and one text model has quota."""
    from . import antigravity_ai as ag
    try:
        offered = ag.models()
    except Exception as exc:  # noqa: BLE001 - closed app or unexpected reply: simply not ready
        return {'installed': (ag.AG / 'bin' / 'agentapi.bat').is_file(), 'ready': False, 'resting_until': None,
                'last_error': str(exc)[:160], 'models': {}}
    mine = {m: offered[m] for m in _ag_models(cfg) if m in offered}
    return {'installed': True, 'resting_until': None, 'last_error': None,
            'models': {m: {'left': round(i['left'], 3), 'reset': i['reset']} for m, i in mine.items()},
            'ready': any(ag.ready(m) for m in mine)}


def next_ready_at(cfg=None, now: float | None = None) -> float:
    """Earliest time Claude or Codex can answer again: its reported reset, or when its oldest
    counted call leaves the capped window. One hour from now when nothing says otherwise."""
    now = now or time.time()
    state, times = _load(), []
    for provider in ('claude', 'codex'):
        caps, used = _caps(cfg, provider), sorted(state['usage'].get(provider, []))
        candidates = [now]
        resting = state['cooldown'].get(provider, 0)
        if resting > now:
            candidates.append(resting)
        recent = [t for t in used if now - t < WINDOW_5H]
        if len(recent) >= caps['per_5h']:
            candidates.append(recent[len(recent) - caps['per_5h']] + WINDOW_5H)
        if len(used) >= caps['per_week']:
            candidates.append(used[len(used) - caps['per_week']] + WINDOW_WEEK)
        times.append(max(candidates))
    soonest = min(times)
    return soonest if soonest > now else now + 3600


def order_for(cfg, purpose: str, web: bool) -> list[str]:
    order = list(cfg.get('local_ai.order', DEFAULT_ORDER) if cfg is not None else DEFAULT_ORDER)
    qwen_for = set(cfg.get('local_ai.qwen_for', DEFAULT_QWEN_FOR) if cfg is not None else DEFAULT_QWEN_FOR)
    # Antigravity's plain call has no web search, so it never researches.
    return [p for p in order if p in ('claude', 'codex') or (p == 'antigravity' and not web)
            or (p == 'qwen' and not web and purpose in qwen_for)]


def _emit(provider: str, purpose: str, model: str | None, started: float, status_text: str, usage=None, error=None):
    try:
        from .studio_events import emit
        emit('api_usage', provider=NAMES[provider], operation=purpose, model=model or 'plan default',
             status=status_text, error_type=error, usage=usage, cost_usd=0 if status_text == 'ok' else None,
             cost_status='owner_plan' if provider != 'qwen' else 'local_model', pricing_basis='subscription',
             elapsed_seconds=round(time.monotonic() - started, 3))
    except Exception:  # noqa: BLE001 - telemetry never blocks production
        pass


def _run(cmd: list[str], prompt: str, timeout: int, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding='utf-8', errors='replace',
                          cwd=_sandbox(), timeout=timeout, env={**os.environ, **(env or {})},
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def _claude(cfg, prompt: str, web: bool, timeout: int, system: str = '', purpose: str = '') -> tuple[str, dict, str | None]:
    exe = find_claude()
    cmd = [exe, '-p', '--output-format', 'json', '--no-session-persistence', '--strict-mcp-config',
           '--setting-sources', 'user']
    # No file, shell or agent tools ever; research may only search and fetch web pages.
    cmd += ['--tools', 'WebSearch,WebFetch', '--allowedTools', 'WebSearch,WebFetch'] if web else ['--tools', '']
    if system:
        # Replace Claude Code's own coding-assistant prompt, so the job's rules (frame size, caption band,
        # logo area) carry the same weight they do through the API.
        prompt_file = _sandbox() / f'system-{os.getpid()}-{threading.get_ident()}.txt'
        prompt_file.write_text(system, encoding='utf-8')
        cmd += ['--system-prompt-file', str(prompt_file)]
    per_job = cfg.get('local_ai.claude_model_for', {}) if cfg is not None else {}
    model = (per_job.get(purpose) if isinstance(per_job, dict) else None) or (cfg.get('local_ai.claude_model') if cfg is not None else None)
    if model:
        cmd += ['--model', str(model)]
    out_tokens = str(cfg.get('local_ai.claude_max_output_tokens', 32000) if cfg is not None else 32000)
    try:
        r = _run(cmd, prompt, timeout, {'CLAUDE_CODE_MAX_OUTPUT_TOKENS': out_tokens})
    finally:
        if system:
            prompt_file.unlink(missing_ok=True)
    raw = (r.stdout or '').strip()
    try:
        data = json.loads(raw.splitlines()[-1]) if raw else {}
    except ValueError:
        data = {}
    text = data.get('result') if isinstance(data, dict) else None
    blob = f'{raw}\n{r.stderr}'
    if (r.returncode or (isinstance(data, dict) and data.get('is_error'))) and LIMIT_RE.search(blob):
        raise LimitError(blob[-300:])
    if r.returncode or not text or (isinstance(data, dict) and data.get('is_error')):
        raise RuntimeError((r.stderr or raw or f'exit {r.returncode}')[-300:])
    usage = data.get('usage') if isinstance(data.get('usage'), dict) else None
    return text.strip(), usage or {}, model


def _codex(cfg, prompt: str, web: bool, timeout: int) -> tuple[str, dict, str | None]:
    exe = find_codex()
    sandbox = _sandbox()
    out = sandbox / f'codex-{os.getpid()}-{threading.get_ident()}.txt'
    out.unlink(missing_ok=True)
    cmd = [exe] + (['--search'] if web else []) + [
        'exec', '--sandbox', 'read-only', '--skip-git-repo-check', '--ephemeral', '--color', 'never',
        '-C', str(sandbox), '-o', str(out)]
    model = cfg.get('local_ai.codex_model') if cfg is not None else None
    if model:
        cmd += ['-m', str(model)]
    r = _run(cmd, prompt, timeout)
    text = out.read_text(encoding='utf-8', errors='replace').strip() if out.exists() else ''
    out.unlink(missing_ok=True)
    blob = f'{r.stdout}\n{r.stderr}'
    if not text and LIMIT_RE.search(blob):
        raise LimitError(blob[-300:])
    if not text:
        raise RuntimeError((r.stderr or r.stdout or f'exit {r.returncode}').strip()[-300:])
    return text, {}, model


def _antigravity(cfg, prompt: str, timeout: int) -> tuple[str, dict, str | None]:
    """First configured Antigravity model with quota left (Claude Sonnet, then Gemini); no tools."""
    from . import antigravity_ai as ag
    tried = []
    for model in _ag_models(cfg):
        if not ag.ready(model):
            tried.append(f'{model} unavailable')
            continue
        try:
            return ag.text(prompt, model, timeout=timeout), {}, model
        except ag.Unavailable as exc:
            tried.append(str(exc)[:80])
    raise LimitError('Antigravity: ' + '; '.join(tried or ['no models configured']))


def image_ready(cfg=None) -> bool:
    """Whether any image provider (Antigravity, then Codex) can draw right now."""
    cfg = _with_live(cfg)
    order = list(cfg.get('local_ai.image_order', DEFAULT_IMAGE_ORDER) if cfg is not None else DEFAULT_IMAGE_ORDER)
    if 'antigravity' in order and _load()['cooldown'].get('antigravity_image', 0) <= time.time():
        from . import antigravity_ai as ag
        if ag.ready(str(cfg.get('local_ai.antigravity_image_model', 'gemini-3.8-flash-high') if cfg is not None
                        else 'gemini-3.8-flash-high')):
            return True
    return 'codex' in order and status(cfg)['codex']['ready']


def image(cfg, prompt: str, out_path: Path, references: list[Path] = (), timeout: int = 420) -> Path:
    """Draw one image: Antigravity (Gemini) first, Codex as backup (local_ai.image_order).

    Raises PlanUnavailable only when every provider is closed or out of quota, so callers can wait
    for a reset; any other failure of the last provider is raised as it is.
    """
    cfg = _with_live(cfg)
    order = list(cfg.get('local_ai.image_order', DEFAULT_IMAGE_ORDER) if cfg is not None else DEFAULT_IMAGE_ORDER)
    notes, error = [], None
    for provider in order:
        started = time.monotonic()
        try:
            if provider == 'antigravity':
                from . import antigravity_ai as ag
                resting = _load()['cooldown'].get('antigravity_image', 0)
                if resting > time.time():  # its image quota is used up: do not open another conversation
                    raise PlanUnavailable('Antigravity image quota used up until ' + datetime.fromtimestamp(resting).strftime('%H:%M'))
                model = str(cfg.get('local_ai.antigravity_image_model', 'gemini-3.8-flash-high'))
                try:
                    ag.image(prompt, out_path, references, model_id=model, timeout=min(timeout, 300))
                except ag.Unavailable as exc:
                    if 'quota' in str(exc).lower():
                        with _LOCK:
                            state = _load()
                            state['cooldown']['antigravity_image'] = exc.reset or time.time() + 3600
                            _save(state)
                    raise PlanUnavailable(str(exc)) from exc
                _emit('antigravity', 'image', model, started, 'ok', usage={'images': 1})
                print(f'  image: drawn by {NAMES["antigravity"]} ({model})')
                return out_path
            if provider == 'codex':
                return codex_image(cfg, prompt, out_path, references, timeout=timeout)
        except PlanUnavailable as exc:
            notes.append(f'{provider}: {str(exc)[:100]}')
        except Exception as exc:  # noqa: BLE001 - the next provider gets a turn
            if provider == 'antigravity':
                _emit('antigravity', 'image', None, started, 'failed', error=type(exc).__name__)
            notes.append(f'{provider}: {type(exc).__name__}: {str(exc)[:100]}')
            error = exc
        print(f'  ! image: {notes[-1]}')
    if error is not None:
        raise RuntimeError('No image provider could draw: ' + '; '.join(notes)) from error
    raise PlanUnavailable('No image provider could draw: ' + '; '.join(notes or ['none configured']))


def _qwen(cfg, prompt: str, json_reply: bool, timeout: int, system: str = '') -> tuple[str, dict, str | None]:
    import requests
    model = cfg.get('local_ai.qwen_model', 'qwen3:8b') if cfg is not None else 'qwen3:8b'
    body = {'model': model, 'stream': False, 'think': False, 'messages': ([{'role': 'system', 'content': system}] if system else []) + [{'role': 'user', 'content': prompt}],
            'options': {'temperature': 0.4, 'num_ctx': int(cfg.get('local_ai.qwen_context', 16384) if cfg is not None else 16384)}}
    if json_reply:
        body['format'] = 'json'
    host = os.environ.get('OLLAMA_HOST_URL', 'http://127.0.0.1:11434')
    response = requests.post(host + '/api/chat', json=body, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    text = (data.get('message') or {}).get('content', '').strip()
    if not text:
        raise RuntimeError('Qwen returned an empty reply')
    return text, {'input_tokens': data.get('prompt_eval_count'), 'output_tokens': data.get('eval_count')}, model


def complete(cfg, purpose: str, prompt: str, *, system: str = '', web: bool = False, json_reply: bool = True,
             timeout: int | None = None) -> tuple[str, str]:
    """Answer with the first available plan provider. Returns (text, provider name)."""
    cfg = _with_live(cfg)
    full = (system.strip() + '\n\n' + prompt) if system else prompt
    timeout = timeout or TIMEOUTS.get(purpose, 600)
    ready = status(cfg)
    notes = []
    for provider in order_for(cfg, purpose, web):
        if provider != 'qwen' and not ready[provider]['ready']:
            notes.append(f"{provider}: {'not installed' if not ready[provider]['installed'] else 'resting or capped'}")
            continue
        started = time.monotonic()
        try:
            if provider == 'claude':
                text, usage, model = _claude(cfg, prompt, web, timeout, system=system.strip(), purpose=purpose)
            elif provider == 'codex':
                text, usage, model = _codex(cfg, full, web, timeout)
            elif provider == 'antigravity':
                text, usage, model = _antigravity(cfg, full, timeout)
            else:
                text, usage, model = _qwen(cfg, prompt, json_reply, timeout, system=system.strip())
        except LimitError as exc:
            with _LOCK:
                state = _load()
                state['cooldown'][provider] = reset_time(str(exc)) or time.time() + 3600
                state['last_error'][provider] = 'Plan usage limit reported · resets ' + \
                    datetime.fromtimestamp(state['cooldown'][provider]).strftime('%H:%M')
                _save(state)
            _emit(provider, purpose, None, started, 'failed', error='PlanLimit')
            notes.append(f'{provider}: plan limit reached')
            continue
        except Exception as exc:  # noqa: BLE001 - try the next provider
            with _LOCK:
                state = _load()
                state['last_error'][provider] = f'{type(exc).__name__}: {exc}'[:200]
                _save(state)
            _emit(provider, purpose, None, started, 'failed', error=type(exc).__name__)
            notes.append(f'{provider}: {type(exc).__name__}: {str(exc)[:120]}')
            continue
        if provider in ('claude', 'codex'):  # Antigravity counts its own quota
            with _LOCK:
                state = _load()
                state['usage'].setdefault(provider, []).append(time.time())
                state['last_error'].pop(provider, None)
                _save(state)
        clean = {k: v for k, v in (usage or {}).items() if isinstance(v, (int, float))}
        _emit(provider, purpose, model, started, 'ok', usage=clean or None)
        print(f'  {purpose}: answered by {NAMES[provider]}' + (f' ({model})' if model else ''))
        return text, provider
    raise PlanUnavailable(f'No plan provider could do {purpose}: ' + '; '.join(notes or ['none configured']))


def codex_image(cfg, prompt: str, out_path: Path, references: list[Path] = (), timeout: int = 420) -> Path:
    """Draw one image with Codex's built-in image generation on the owner's plan.

    Codex runs in its own empty folder that it may write to (and nothing else), with the
    reference images attached. Codex also keeps a copy under ~/.codex/generated_images (C:);
    that copy is removed once the image is taken, to keep generated media on D:.
    """
    cfg = _with_live(cfg)
    ready = status(cfg)['codex']
    if not ready['ready']:
        raise PlanUnavailable('Codex is ' + ('not installed' if not ready['installed'] else 'resting or capped') + '; it draws the images')
    work = _sandbox() / f'image-{os.getpid()}-{threading.get_ident()}-{int(time.time())}'
    work.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    try:
        attached = []
        for index, ref in enumerate(references):
            target = work / f'reference-{index}{Path(ref).suffix or ".jpg"}'
            target.write_bytes(Path(ref).read_bytes())
            attached += ['-i', target.name]
        cmd = [find_codex(), 'exec', '--sandbox', 'workspace-write', '--skip-git-repo-check', '--ephemeral', '--color', 'never',
               '-C', str(work), *attached, '-o', 'reply.txt', '-']
        full = prompt + '\n\nUse your built-in image_gen tool. Save the finished image in the current folder as image.png. Reply only with the file name.'
        result = subprocess.run(cmd, input=full, capture_output=True, text=True, encoding='utf-8', errors='replace',
                                cwd=work, timeout=timeout, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        blob = f'{result.stdout}\n{result.stderr}'
        image = work / 'image.png'
        if not image.is_file():
            # Codex can draw the image yet fail to copy it in (its Windows sandbox shell timed out on
            # 2026-11-23 "connecting runner pipe-in"); its own copy under generated_images is the image.
            drawn = [Path.home() / '.codex' / 'generated_images' / folder / name for folder, name in
                     re.findall(r'generated_images[\\/]+([0-9a-f-]{36})[\\/]+([\w.-]+\.(?:png|jpe?g|webp))', blob)]
            drawn = [p for p in drawn if p.is_file() and p.stat().st_mtime >= time.time() - (time.monotonic() - started) - 5]
            if drawn:
                image = max(drawn, key=lambda p: p.stat().st_mtime)
        if not image.is_file():
            if LIMIT_RE.search(blob):
                with _LOCK:
                    state = _load()
                    state['cooldown']['codex'] = reset_time(blob) or time.time() + 3600
                    state['last_error']['codex'] = 'Plan usage limit reported'
                    _save(state)
                _emit('codex', 'image', None, started, 'failed', error='PlanLimit')
                raise PlanUnavailable('Codex reached its plan limit while drawing')
            _emit('codex', 'image', None, started, 'failed', error='NoImage')
            raise RuntimeError('Codex did not produce an image: ' + (blob.strip()[-200:] or f'exit {result.returncode}'))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(image.read_bytes())
        for folder in set(re.findall(r'generated_images[\\/]+([0-9a-f-]{36})', blob)):
            shutil.rmtree(Path.home() / '.codex' / 'generated_images' / folder, ignore_errors=True)
        with _LOCK:
            state = _load()
            state['usage'].setdefault('codex', []).append(time.time())
            _save(state)
        _emit('codex', 'image', 'image_gen', started, 'ok', usage={'images': 1})
        return out_path
    finally:
        shutil.rmtree(work, ignore_errors=True)
