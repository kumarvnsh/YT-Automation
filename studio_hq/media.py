"""Shotsnip clip library and Histold draft previews for Studio HQ.

Media is only ever served through an index of absolute paths that the Rust hub re-checks
against two roots (the Shotsnip work folder and the private drafts folder) and a short list
of file types. Draft previews are extracted from the run artifact the existing workflow made.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import zipfile

from .archive import atomic_json, read_json

STAGE = re.compile(r'\d{8}_\d{6}(?:_\d{6})?_(?:short|long)')
RUN = re.compile(r'\d{6,20}')
DRAFT_FILES = {'video.mp4', 'metadata.json', 'script.json', 'quality.json',
               'thumbnail.jpg', 'thumbnail.png', 'thumbnail.webp'}
ARTIFACT_LIMIT = 300 * 1024 ** 2


def media_id(path):
    return hashlib.sha256(str(Path(path).resolve()).lower().encode()).hexdigest()[:32]


def drafts_folder(config):
    return Path(config['dashboard_private_snapshot']).parent/'drafts'


def short(value, length=240):
    return value.strip()[:length] if isinstance(value, str) else ''


def clips(shotsnip_root, media):
    """The original dashboard's clip library and capture runs, read-only, with playable media ids."""
    import os
    os.environ.setdefault('SHOTSNIP_ROOT', str(shotsnip_root))
    from scripts import clipping_dashboard as owner
    from .dashboard_phase3 import capture_error
    runs = owner.clip_runs()
    state = owner.read(owner.CLIP_ROOT/'work/scheduler/state.json', {})
    try:
        manual = owner.manual_snapshot()
    except Exception:  # noqa: BLE001 - the library still shows automatic and live clips
        manual = []
    work = (owner.CLIP_ROOT/'work').resolve()
    library = []
    for row in owner.clip_library(runs, state, manual, limit=60):
        path = (work/row['file']).resolve()
        if not path.is_file() or not path.is_relative_to(work):
            continue
        identity = media_id(path)
        media[identity] = str(path)
        published = row.get('published') or {}
        library.append({
            'media': identity, 'title': short(row.get('title'), 160) or 'Finished clip', 'creator': short(row.get('creator'), 80),
            'origin': row.get('origin'), 'run': short(row.get('run'), 80), 'at': row.get('at'), 'intro': short(row.get('intro'), 200),
            'source_url': row.get('source_url') if isinstance(row.get('source_url'), str) and row['source_url'].startswith('https://') else None,
            'status': published.get('status'), 'youtube': published.get('video_id'), 'facebook': published.get('reel_id'),
            'error': capture_error([row['error']]) if row.get('error') else None,
        })
    captures = []
    for run in runs[:40]:
        captures.append({
            'run': short(run.get('run'), 80), 'creator': short(run.get('creator'), 80), 'worker': run.get('worker'),
            'clips': len(run.get('clips') or []), 'chat_messages': run.get('chat_messages') if isinstance(run.get('chat_messages'), int) else None,
            'elapsed_seconds': run.get('elapsed_seconds') if isinstance(run.get('elapsed_seconds'), (int, float)) else None,
            'errors': [capture_error([item]) for item in (run.get('errors') or [])][:3],
            'source_url': run.get('source_url') if isinstance(run.get('source_url'), str) and run['source_url'].startswith('https://') else None,
        })
    return {'library': library, 'captures': captures}


def draft_preview(config, stage, media):
    """Details of a prepared draft preview, or None when it has not been prepared."""
    folder = drafts_folder(config)/stage
    video = folder/'video.mp4'
    if not video.is_file():
        return None
    media[media_id(video)] = str(video.resolve())
    thumbnail = next((folder/name for name in ('thumbnail.jpg', 'thumbnail.png', 'thumbnail.webp') if (folder/name).is_file()), None)
    if thumbnail:
        media[media_id(thumbnail)] = str(thumbnail.resolve())
    def load(name):
        try:
            value = read_json(folder/name)
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}
    metadata, script, quality = load('metadata.json'), load('script.json'), load('quality.json')
    tags = metadata.get('tags') if isinstance(metadata.get('tags'), list) else []
    return {
        'video': media_id(video), 'thumbnail': media_id(thumbnail) if thumbnail else None,
        'size': video.stat().st_size,
        'title': short(metadata.get('title') or script.get('title'), 200),
        'description': short(metadata.get('description') or script.get('description'), 2000),
        'tags': [short(tag, 40) for tag in tags if isinstance(tag, str)][:20],
        'privacy': short(metadata.get('privacy_status'), 20),
        'narration': [short(segment.get('narration'), 600) for segment in script.get('segments', [])
                      if isinstance(segment, dict) and segment.get('narration')][:30],
        'quality': {'passed': quality.get('passed') if isinstance(quality.get('passed'), bool) else None,
                    'score': quality.get('score') if isinstance(quality.get('score'), (int, float)) else None,
                    'errors': [short(item, 300) for item in quality.get('errors', []) if isinstance(item, str)][:8],
                    'warnings': [short(item, 300) for item in quality.get('warnings', []) if isinstance(item, str)][:8]},
    }


def prepare_draft(config, body, *, run=subprocess.run):
    """Download one pending draft's run artifact and keep only what the preview needs."""
    stage, run_id = body['stage'], body['run_id']
    repo, gh = config['github_repo'], config['github_cli']
    listing = run([gh, 'api', f'repos/{repo}/actions/runs/{run_id}/artifacts'], capture_output=True, timeout=30)
    if listing.returncode:
        raise RuntimeError('GitHub artifact list could not be read')
    artifact = next((item for item in json.loads(listing.stdout).get('artifacts', [])
                     if item.get('name') == 'run-' + stage and item.get('expired') is False), None)
    if not artifact:
        raise ValueError('This draft’s files have expired on GitHub, so a preview is no longer available.')
    if artifact['size_in_bytes'] > ARTIFACT_LIMIT:
        raise ValueError('This draft’s files are too large to preview here.')
    fetched = run([gh, 'api', f"repos/{repo}/actions/artifacts/{artifact['id']}/zip"], capture_output=True, timeout=300)
    if fetched.returncode or len(fetched.stdout) > ARTIFACT_LIMIT:
        raise RuntimeError('The draft could not be downloaded')
    root = drafts_folder(config)
    target = root/stage
    staging = root/(stage + '.partial')
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(fetched.stdout)) as bundle:
        for member in bundle.infolist():
            name = Path(member.filename).name
            if member.is_dir() or name not in DRAFT_FILES or '..' in Path(member.filename).parts or member.file_size > ARTIFACT_LIMIT:
                continue
            with bundle.open(member) as source, (staging/name).open('wb') as sink:
                shutil.copyfileobj(source, sink)
    if not (staging/'video.mp4').is_file():
        shutil.rmtree(staging, ignore_errors=True)
        raise ValueError('This draft has no rendered video to preview.')
    shutil.rmtree(target, ignore_errors=True)
    staging.replace(target)
    # Keep the cache small: previews for drafts no longer waiting are removed.
    waiting = {row.get('stage') for row in body.get('_pending', [])} | {stage}
    for old in root.iterdir():
        if old.is_dir() and old.name not in waiting:
            shutil.rmtree(old, ignore_errors=True)
    return 'Preview ready. Open the draft to watch it.'
