"""Google Antigravity as a plan provider on this laptop: Gemini draws images, and its models write text.

Antigravity must be open (it runs a local language server). Nothing here costs money: it uses the
owner's Google AI Pro quota, which the app reports per model with its reset time.

Text uses the server's plain GetModelResponse call: one prompt in, one answer out, no agent and no
tools, so nothing in a prompt can make it run commands. Images need Antigravity's agent (only it has
the image tool); it is told to draw and do nothing else, and the image is taken from its
conversation folder rather than letting it write files anywhere.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

AG = Path(os.environ.get('ANTIGRAVITY_HOME', Path.home() / '.gemini' / 'antigravity'))
SERVICE = 'exa.language_server_pb.LanguageServerService'
IMAGE_EXT = ('.png', '.jpg', '.jpeg', '.webp')
_CACHE: dict = {'at': 0.0, 'models': None}


class Unavailable(RuntimeError):
    """Antigravity is closed, the model is out of quota, or it is disabled on this plan."""

    def __init__(self, message: str, reset: float | None = None):
        super().__init__(message)
        self.reset = reset  # epoch seconds when the quota comes back, when Antigravity says


def _reset_at(text: str) -> float | None:
    """'Quota resets at `2026-11-23T09:39:26Z`' -> epoch seconds."""
    import re
    from datetime import datetime
    match = re.search(r'resets? at\W*(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)', text, flags=re.I)
    if not match:
        return None
    return datetime.fromisoformat(match.group(1).replace('Z', '+00:00')).timestamp()


def forget(conversation: str) -> None:
    """Remove an automated conversation from the Antigravity app, so the owner's list stays clean."""
    try:
        _call('DeleteCascadeTrajectory', {'cascadeId': conversation}, timeout=10)
    except Exception:  # noqa: BLE001 - tidying only
        pass
    shutil.rmtree(AG / 'brain' / conversation, ignore_errors=True)


def _hub() -> dict:
    hubs = sorted(AG.glob('daemon/hub_*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
    if not hubs:
        raise Unavailable('Antigravity is not open')
    info = json.loads(hubs[0].read_text(encoding='utf-8'))
    return {'address': f"127.0.0.1:{info['httpPort']}", 'token': info['csrfToken']}


def _call(method: str, body: dict, timeout: float = 20) -> dict:
    hub = _hub()
    request = urllib.request.Request(f"http://{hub['address']}/{SERVICE}/{method}", data=json.dumps(body).encode(),
                                     method='POST', headers={'Content-Type': 'application/json',
                                                             'x-codeium-csrf-token': hub['token']})
    try:
        return json.loads(urllib.request.urlopen(request, timeout=timeout).read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', 'replace')[:300]
        if exc.code == 429 or 'quota' in detail.lower() or 'exhausted' in detail.lower():
            raise Unavailable(f'Antigravity quota: {detail}') from exc
        raise RuntimeError(f'Antigravity {method}: HTTP {exc.code} {detail}') from exc
    except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
        if isinstance(exc, TimeoutError) or 'timed out' in str(exc):
            raise RuntimeError(f'Antigravity {method} timed out') from exc
        raise Unavailable('Antigravity is not open') from exc


def models(fresh: bool = False) -> dict[str, dict]:
    """modelId -> {'enum', 'left' (0-1), 'reset', 'disabled'}; cached for a minute."""
    if fresh or not _CACHE['models'] or time.time() - _CACHE['at'] > 60:
        data = _call('GetCascadeModelConfigData', {})
        _CACHE['models'] = {c['modelId']: {'enum': (c.get('modelOrAlias') or {}).get('model'),
                                           'left': float((c.get('quotaInfo') or {}).get('remainingFraction', 0)),
                                           'reset': (c.get('quotaInfo') or {}).get('resetTime'),
                                           'disabled': bool(c.get('disabled'))}
                            for c in data.get('clientModelConfigs', []) if c.get('modelId')}
        _CACHE['at'] = time.time()
    return _CACHE['models']


def ready(model_id: str, floor: float = .05) -> bool:
    try:
        info = models().get(model_id)
    except Exception:  # noqa: BLE001
        return False
    return bool(info and info['enum'] and not info['disabled'] and info['left'] > floor)


def text(prompt: str, model_id: str, timeout: int = 900) -> str:
    """One prompt, one answer: no agent, no tools."""
    info = models().get(model_id)
    if not info or not info['enum'] or info['disabled']:
        raise Unavailable(f'{model_id} is not offered on this Antigravity plan')
    if info['left'] <= .05:
        raise Unavailable(f'{model_id} quota used up until {info["reset"]}')
    reply = _call('GetModelResponse', {'prompt': prompt, 'model': info['enum']}, timeout=timeout)
    answer = (reply.get('response') or '').strip()
    if not answer:
        raise RuntimeError(f'{model_id} returned an empty reply')
    _CACHE['at'] = 0  # quota changed; read it fresh next time
    return answer


DRAW_RULES = ('\n\nGenerate exactly one image with your image generation tool, then stop. Do not run any terminal '
              'commands, do not create, copy or edit files, and do not browse the web. When the image exists, reply '
              'only with the word DONE.')


def _agentapi(*args: str, timeout: int = 60) -> dict:
    hub = _hub()
    exe = AG / 'bin' / 'agentapi.bat'
    if not exe.is_file():
        raise Unavailable('Antigravity agentapi is not installed')
    env = {**os.environ, 'ANTIGRAVITY_LS_ADDRESS': hub['address'], 'ANTIGRAVITY_CSRF_TOKEN': hub['token']}
    result = subprocess.run([str(exe), *args], capture_output=True, text=True, encoding='utf-8', errors='replace',
                            env=env, timeout=timeout, stdin=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        data = json.loads(result.stdout or '{}')
    except ValueError:
        data = {}
    if data.get('error'):
        raise RuntimeError(f"Antigravity: {data['error'][:200]}")
    return data


def image(prompt: str, out_path: Path, references: list[Path] = (), model_id: str = 'gemini-3.8-flash-high',
          timeout: int = 300) -> Path:
    """Have Antigravity's agent draw one image and copy it to out_path (PNG/JPEG by out_path's suffix)."""
    if not ready(model_id):
        raise Unavailable(f'Antigravity {model_id} is closed or out of quota')
    refs = ''
    if references:
        refs = ('\n\nReference images (look at them with your file viewer; keep the real subject\'s likeness and period '
                'details, repaint as a new composition):\n' + '\n'.join(str(Path(r).resolve()) for r in references))
    started = time.time()
    reply = _agentapi('new-conversation', f'--model={model_id}', '--title=Histold image', prompt + refs + DRAW_RULES)
    conversation = ((reply.get('response') or {}).get('newConversation') or {}).get('conversationId')
    if not conversation:
        raise RuntimeError('Antigravity did not start the drawing')
    folder = AG / 'brain' / conversation
    log = folder / '.system_generated' / 'logs' / 'transcript_full.jsonl'
    found, helpers = None, set()
    try:
        while time.time() - started < timeout:
            time.sleep(3)
            drawn = [p for p in folder.glob('*') if p.suffix.lower() in IMAGE_EXT and p.stat().st_mtime >= started - 5] \
                if folder.is_dir() else []
            if drawn:
                found = max(drawn, key=lambda p: p.stat().st_mtime)
            rows = []
            if log.is_file():
                rows = [json.loads(line) for line in log.read_text(encoding='utf-8', errors='replace').splitlines() if line.strip()]
            import re
            text = json.dumps(rows)
            said = '\n'.join(str(row.get('content') or '') for row in rows)
            helpers.update(re.findall(r'"conversationId":\s*"([0-9a-f-]{36})"', said))  # its drawing helper
            lowered = text.lower()
            if 'resource_exhausted' in lowered or ('quota' in lowered and ('exhausted' in lowered or 'exceeded' in lowered)):
                reset = _reset_at(text)
                raise Unavailable('Antigravity image quota used up' + (f' until {time.strftime("%H:%M", time.localtime(reset))}'
                                                                      if reset else ''), reset)
            # The agent hands the drawing to a helper and often replies before the file lands, so only
            # the file counts; it is taken once it has stopped changing.
            if found and time.time() - found.stat().st_mtime > 3:
                break
        if not found:
            raise RuntimeError(f'Antigravity did not draw within {timeout}s')
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        from PIL import Image
        with Image.open(found) as picture:
            picture = picture.convert('RGB')
            if out_path.suffix.lower() == '.png':
                picture.save(out_path, 'PNG')
            else:
                picture.save(out_path, 'JPEG', quality=95)
        return out_path
    finally:
        # Generated media stays on D: (owner rule), and the app's conversation list stays clean:
        # the drawing conversation and its helper are removed once the image is taken.
        for one in {conversation, *helpers}:
            forget(one)
