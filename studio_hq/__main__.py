"""Read-only audit, explicit archive, restore and GitHub artifact collection."""
from __future__ import annotations

import argparse
import io
import re
from contextlib import closing
from collections import defaultdict
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
import zipfile

from .archive import (CHANNELS, MEDIA, SUPPORT, Rclone, archive_one, archive_tick, atomic_json,
                      discover, hosted_story_evidence, package, read_json, restore, stamp, timestamp, transfer_package,
                      within, worker_lock)
from src.studio_events import scrub


def load(path):
    config = read_json(path)
    if set(config['roots']) != CHANNELS:
        raise ValueError('Configure exactly Histold, Astrotold and Shotsnip roots')
    if config.get('local_cleanup_enabled') is not False:
        raise ValueError('Phase zero only supports local_cleanup_enabled=false')
    remote = config.get('remote', '')
    if not remote or ':' not in remote or remote.startswith(('/', '\\')) or '..' in remote.split('/'):
        raise ValueError('Configure a named rclone remote and private archive folder')
    return config


def inventory(config):
    stats = []
    # Do not inventory a parent repo twice through its nested Astrotold profile.
    paths = [(key, Path(root) / 'output') for key, root in config['roots'].items() if key != 'shotsnip']
    clip = Path(config['roots']['shotsnip'])
    paths.extend(('shotsnip/' + name, clip / 'work' / name) for name in ('live', 'scheduler', 'manual', 'previews'))
    paths.append(('clips-kitty', Path(config.get('kitty_root', r'D:\Clips-Kitty')) / 'data'))
    for label, root in paths:
        totals = defaultdict(lambda: {'files': 0, 'bytes': 0})
        errors = 0
        if root.is_dir():
            for directory, dirs, files in os.walk(root, followlinks=False):
                dirs[:] = [d for d in dirs if not Path(directory, d).is_symlink()
                           and not (hasattr(Path(directory, d), 'is_junction') and Path(directory, d).is_junction())]
                for name in files:
                    try:
                        path = within(Path(directory, name), root)
                        size = path.stat().st_size
                        key = 'raw_ts' if path.suffix.lower() == '.ts' else 'mp4' if path.suffix.lower() == '.mp4' else 'other'
                        totals[key]['files'] += 1; totals[key]['bytes'] += size
                    except (OSError, ValueError):
                        errors += 1
        stats.append(dict(label=label, path=str(root), exists=root.exists(), groups=dict(totals), read_errors=errors))
    return {'schema_version': 1, 'observed_at': stamp(), 'locations': stats,
            'disk': dict(zip(('total', 'used', 'free'), shutil.disk_usage(clip))),
            'cleanup_enabled': False}


def sync_events(config):
    """Idempotent observation index, deliberately separate from job ledgers."""
    folder = Path(config['state_dir']); folder.mkdir(parents=True, exist_ok=True)
    imported = 0
    with closing(sqlite3.connect(folder / 'observations.sqlite')) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS events(event_id TEXT PRIMARY KEY,channel TEXT,job_id TEXT,stage TEXT,kind TEXT,observed_at TEXT,payload TEXT)')
        known = {row[0] for row in db.execute('SELECT event_id FROM events')}
        for channel, root in config['roots'].items():
            for path in sorted((Path(root) / 'data/private/studio-hq/events').glob('*.json')):
                if path.stem in known:
                    continue
                try:
                    row = read_json(path)
                except (OSError, ValueError):
                    continue
                row = scrub(row)
                if not row or row['channel'] != channel or row['event_id'] != path.stem:
                    continue
                imported += insert_event(db, row)
    return {'events_imported': imported, 'database': str(folder / 'observations.sqlite')}


def insert_event(db, row):
    return db.execute('INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?,?)',
        (row['event_id'], row['channel'], row['job_id'], row['stage'], row['kind'], row['observed_at'], json.dumps(row))).rowcount


HOSTED_PREFIX = 'studio-hq-events-'
HOSTED_MAX_BYTES = 2 * 1024 ** 2
HOSTED_EVENT_NAME = re.compile(r'[0-9a-f]{32}\.json')


def sync_hosted_events(config, index_root, *, limit=20, run=subprocess.run):
    """Import scrubbed hosted Histold observations from studio-hq-events-<job> artifacts.

    Each artifact maps to exactly one workflow run and one job: events whose job
    differs from the artifact name are rejected. event_id keeps re-imports idempotent.
    """
    from .dashboard_phase3 import job_reference
    folder = Path(index_root); folder.mkdir(parents=True, exist_ok=True)
    ledger_path = folder / 'hosted-events.json'
    ledger = read_json(ledger_path) if ledger_path.is_file() else {}
    repo = config.get('github_repo', 'kumarvnsh/YT-Automation')
    gh = config.get('github_cli', 'gh')
    listing = run([gh, 'api', '--paginate', f'repos/{repo}/actions/artifacts?per_page=100', '--jq',
                   '.artifacts[] | select(.name | startswith("' + HOSTED_PREFIX + '")) | select(.expired | not)'
                   ' | {id, name, size_in_bytes, run_id: .workflow_run.id}'],
                  capture_output=True, text=True, encoding='utf-8', timeout=120)
    if listing.returncode:
        raise RuntimeError('Cannot list GitHub artifacts')
    pending = [json.loads(line) for line in listing.stdout.splitlines() if line.strip()]
    pending = [item for item in pending if str(item['id']) not in ledger]
    report = {'artifacts': 0, 'events_imported': 0, 'rejected': 0, 'skipped': 0}
    with closing(sqlite3.connect(folder / 'observations.sqlite')) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS events(event_id TEXT PRIMARY KEY,channel TEXT,job_id TEXT,stage TEXT,kind TEXT,observed_at TEXT,payload TEXT)')
        for item in sorted(pending, key=lambda a: a['id'])[:max(0, limit)]:
            job = item['name'][len(HOSTED_PREFIX):]
            entry = dict(run_id=item['run_id'], job_id=job, imported=0, rejected=0, at=stamp())
            if not job_reference('histold', job) or not isinstance(item['run_id'], int) or item['size_in_bytes'] > HOSTED_MAX_BYTES:
                entry['status'] = 'skipped'
                report['skipped'] += 1
            else:
                fetched = run([gh, 'api', f"repos/{repo}/actions/artifacts/{item['id']}/zip"],
                              capture_output=True, timeout=120)
                if fetched.returncode or len(fetched.stdout) > HOSTED_MAX_BYTES:
                    continue  # retried on the next pass
                with zipfile.ZipFile(io.BytesIO(fetched.stdout)) as bundle:
                    for member in bundle.infolist():
                        if (member.is_dir() or not HOSTED_EVENT_NAME.fullmatch(member.filename)
                                or member.file_size > 64 * 1024):
                            entry['rejected'] += 1; continue
                        try:
                            row = scrub(json.loads(bundle.read(member).decode('utf-8')))
                        except ValueError:
                            row = None
                        if (not row or row['event_id'] + '.json' != member.filename
                                or row['channel'] != 'histold' or row['job_id'] != job):
                            entry['rejected'] += 1; continue
                        row.update(hosted_run_id=item['run_id'], hosted_artifact_id=item['id'])
                        entry['imported'] += insert_event(db, row)
                entry['status'] = 'imported'
                report['events_imported'] += entry['imported']
                report['rejected'] += entry['rejected']
            report['artifacts'] += 1
            ledger[str(item['id'])] = entry
    atomic_json(ledger_path, ledger)
    return report


def github_artifacts(config, *, collect=False, limit=3, new_only=False):
    """Only explicitly requested downloads; never executes collected artifacts."""
    repo = config.get('github_repo', 'kumarvnsh/YT-Automation')
    gh = config.get('github_cli', 'gh')
    result = subprocess.run([gh, 'api', f'repos/{repo}/actions/artifacts?per_page=100'],
                            capture_output=True, text=True, encoding='utf-8', timeout=60)
    if result.returncode:
        raise RuntimeError('Cannot list GitHub artifacts')
    artifacts = [a for a in json.loads(result.stdout)['artifacts']
                 if not a['expired'] and (a['name'].startswith('run-') or a['name'].startswith('astrotold-run-'))]
    report = [dict(id=a['id'], name=a['name'], size_bytes=a['size_in_bytes'],
                   created_at=a['created_at'], expires_at=a['expires_at'], run_id=a['workflow_run']['id']) for a in artifacts]
    if not collect:
        return {'artifacts': report, 'downloaded': False, 'pagination': 'first_100_only'}
    collected = []
    remaining = int(config.get('artifact_download_budget_bytes', 1024 ** 3))
    items = sorted(report, key=lambda a: a['expires_at'])
    if new_only:
        items = [item for item in items
                 if time.time()-timestamp(item['created_at']) >= 48*3600
                 and not (Path(config['state_dir'])/'github-artifacts'/str(item['id'])/'source.json').is_file()]
    for item in items[:max(0, limit)]:
        if item['size_bytes'] > remaining:
            continue
        folder = Path(config['state_dir']) / 'github-artifacts' / str(item['id'])
        if (folder / 'source.json').exists():
            collected.append(dict(id=item['id'], status='already_collected')); continue
        folder.mkdir(parents=True, exist_ok=True)
        archive = folder / 'artifact.zip'
        with archive.open('wb') as stream:
            result = subprocess.run([gh, 'api', f"repos/{repo}/actions/artifacts/{item['id']}/zip"],
                                    stdout=stream, stderr=subprocess.PIPE, timeout=600)
        if result.returncode:
            raise RuntimeError('Artifact download failed')
        with zipfile.ZipFile(archive) as bundle:
            expanded = sum(i.file_size for i in bundle.infolist())
            if expanded > remaining or shutil.disk_usage(folder).free < expanded + 5 * 1024 ** 3:
                raise ValueError('Artifact extraction exceeds local storage budget')
            for member in bundle.infolist():
                path = Path(member.filename)
                if path.is_absolute() or '..' in path.parts or '\\' in member.filename or ':' in member.filename:
                    raise ValueError('Unsafe artifact member path')
                if member.is_dir() or path.name not in MEDIA | SUPPORT:
                    continue
                target = folder / 'files' / path
                within(target, folder)
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(member) as source, target.open('wb') as dest:
                    shutil.copyfileobj(source, dest)
        atomic_json(folder / 'source.json', dict(item, channel='astrotold' if item['name'].startswith('astrotold-') else 'histold',
                    status='collected_local_only', completion_source='artifact_created_at_upper_bound',
                    archive_eligible=False, reason='requires_source_state_and_completion_binding_review'))
        remaining -= max(item['size_bytes'], expanded)
        collected.append(dict(id=item['id'], status='collected_local_only'))
    return {'collected': collected, 'drive_archived': False}


def archive_collected(config, limit=3, published_only=False):
    """Preserve collected final videos; never make unknown-age sources deletable."""
    rows = []
    root = Path(config['state_dir']) / 'github-artifacts'
    for source in sorted(root.glob('*/source.json')):
        if len(rows) >= limit:
            break
        origin = read_json(source)
        if origin.get('channel') not in CHANNELS:
            raise ValueError('Unknown artifact channel')
        for video in (source.parent / 'files').rglob('video.mp4'):
            within(video, root)
            if published_only:
                if time.time()-timestamp(origin['created_at']) < 48*3600:
                    continue
                try:
                    if not hosted_story_evidence(source.parent, video):
                        continue
                except (KeyError, ValueError, zipfile.BadZipFile):
                    continue
            # This is a conservative upper bound, explicitly distinguished from
            # render completion. It permits preservation only, never deletion.
            candidate = dict(eligible=True, channel=origin['channel'], path=str(video),
                             job_id='github-artifact-' + str(origin['id']), completed_at=origin['created_at'])
            value = package(candidate, root)
            if value['archive_id'] in config.get('legacy_drive_archive_ids', []):
                continue  # Already verified under the previous Drive connection.
            if (Path(config['state_dir'])/'archive-receipts'/(value['archive_id']+'.json')).is_file():
                continue
            value.update(completion_source='artifact_created_at_upper_bound',
                         github_artifact_id=origin['id'], github_run_id=origin['run_id'],
                         source_age_confirmed=False)
            rows.append(transfer_package(value, root, config))
            if len(rows) >= limit:
                break
    return {'archived': rows, 'cleanup_enabled': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default=r'D:\StudioHQ\config.json')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('inventory', 'plan', 'sync-events', 'sync-hosted-events', 'drive-status'):
        sub.add_parser(name)
    archive = sub.add_parser('archive'); archive.add_argument('--limit', type=int, default=1)
    tick = sub.add_parser('archive-tick'); tick.add_argument('--limit', type=int, choices=range(1, 11), default=1)
    tick.add_argument('--cleanup-shotsnip', action='store_true')
    preserved = sub.add_parser('archive-collected'); preserved.add_argument('--limit', type=int, default=3)
    restored = sub.add_parser('restore'); restored.add_argument('manifest')
    gh = sub.add_parser('github-artifacts'); gh.add_argument('--collect', action='store_true'); gh.add_argument('--limit', type=int, default=3)
    args = parser.parse_args()
    config = load(args.config)
    try:
        if args.command == 'inventory':
            report = inventory(config)
            atomic_json(Path(config['state_dir']) / 'inventory.json', report)
        elif args.command == 'plan':
            rows = discover(config)
            report = dict(observed_at=stamp(), candidates=rows, cleanup_enabled=False)
            atomic_json(Path(config['state_dir']) / 'archive-plan.json', report)
        elif args.command == 'sync-events':
            report = sync_events(config)
        elif args.command == 'sync-hosted-events':
            report = sync_hosted_events(config, Path(config['dashboard_private_snapshot']).parent)
        elif args.command == 'drive-status':
            transfer = Rclone(config)
            report = dict(quota=json.loads(transfer.run('about', config['remote'].split(':')[0] + ':', '--json')), cleanup_enabled=False)
        elif args.command == 'github-artifacts':
            with worker_lock(config['state_dir']):
                report = github_artifacts(config, collect=args.collect, limit=args.limit)
            atomic_json(Path(config['state_dir']) / 'github-artifacts.json', report)
        elif args.command == 'archive-collected':
            with worker_lock(config['state_dir']):
                report = archive_collected(config, max(0, args.limit))
        elif args.command == 'archive-tick':
            report = archive_tick(config, args.limit)
            if config.get('archive_hosted_stories') is True and not report['failed']:
                try:
                    with worker_lock(config['state_dir']):
                        hosted = archive_collected(config, 1, published_only=True)
                        if not hosted['archived']:
                            github_artifacts(config, collect=True, limit=1, new_only=True)
                            hosted = archive_collected(config, 1, published_only=True)
                    report['hosted_stories_archived'] = [item['archive_id'] for item in hosted['archived']]
                except (OSError, ValueError, KeyError, RuntimeError, zipfile.BadZipFile, subprocess.TimeoutExpired) as exc:
                    report['hosted_story_error'] = type(exc).__name__
            if args.cleanup_shotsnip and config.get('shotsnip_finished_cleanup_enabled') is True and not report['failed']:
                from .finished_cleanup import cleanup_tick
                report['shotsnip_finished_cleanup'] = cleanup_tick(config)
            if private := config.get('dashboard_private_snapshot'):
                try:  # Usage telemetry is advisory; it never fails the archive tick.
                    report['hosted_telemetry'] = sync_hosted_events(config, Path(private).parent, limit=10)
                except (OSError, ValueError, KeyError, RuntimeError, sqlite3.Error, zipfile.BadZipFile,
                        subprocess.TimeoutExpired) as exc:
                    report['hosted_telemetry'] = {'error': type(exc).__name__}
            atomic_json(Path(config['state_dir'])/'last-archive-tick.json', report)
            print(json.dumps(report, indent=2))
            return int(bool(report['failed']) or bool(report.get('hosted_story_error'))
                       or report.get('shotsnip_finished_cleanup', {}).get('status') == 'failed')
        elif args.command == 'archive':
            with worker_lock(config['state_dir']):
                eligible = [r for r in discover(config) if r['eligible']]
                report = {'archived': [archive_one(r, config) for r in eligible[:max(0, args.limit)]], 'cleanup_enabled': False}
        else:
            with worker_lock(config['state_dir']):
                report = restore(read_json(args.manifest), config)
        print(json.dumps(report, indent=2))
    except (OSError, ValueError, RuntimeError, sqlite3.Error, subprocess.TimeoutExpired) as exc:
        # Keep provider command output/private exception contents out of public logs.
        parser.exit(1, 'Studio HQ operation failed (' + type(exc).__name__ + '). No local media was deleted.\n')


if __name__ == '__main__':
    raise SystemExit(main())
