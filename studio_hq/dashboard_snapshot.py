"""Export the read-only dashboard's public schema from local source records."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import sqlite3

from .archive import atomic_json, digest, read_json, stamp, within
from .video_insights import summary as video_summary
from .histold_live import snapshot as histold_live_snapshot
from .dashboard_analytics import analytics, pacing_experiment
from .dashboard_controls import overview
from .dashboard_phase3 import phase3, shotsnip_workspace

LABELS = {'histold': 'Histold', 'shotsnip': 'Shotsnip'}
GOOD = {'uploaded', 'published', 'facebook_published'}


def record(path, fallback):
    try:
        return read_json(path)
    except (OSError, ValueError):
        return fallback


def date(value):
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return datetime.fromtimestamp(value, timezone.utc).isoformat()
        if isinstance(value, str):
            result = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if result.tzinfo:
                return result.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        pass
    return None


def safe_text(value, fallback=''):
    if not isinstance(value, str):
        return fallback
    cleaned = ' '.join(re.sub(r'https?://\S+', '', value).split())
    return cleaned[:160] or fallback


def youtube(value):
    if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{11}', value):
        return 'https://www.youtube.com/watch?v=' + value
    return None


def drive(value):
    if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{15,120}', value):
        return 'https://drive.google.com/file/d/' + value + '/view'
    return None


def activity(channel, kind, title, status, at, url=None, job=None):
    identity = job if isinstance(job, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,100}', job) else None
    return dict(channel=channel, kind=kind, title=title, status=status, at=date(at), url=url, job=identity)


def story_activity(root, channel, index_root=None):
    entries = {}
    sources = [root] + ([index_root] if index_root and index_root != root else [])
    for source in sources:
        index = record(source/'data/published_index.json', [])
        if not isinstance(index, list):
            continue
        for row in index:
            if not isinstance(row, dict) or row.get('channel', channel) != channel:
                continue
            identity = row.get('video_id') or row.get('facebook_id') or row.get('stage_dir_name')
            if isinstance(identity, str) and identity:
                entries[identity] = activity(channel, 'Publication', safe_text(row.get('title'), 'Published video'),
                                             'Published', row.get('published_at'), youtube(row.get('video_id')),
                                             job=row.get('stage_dir_name'))
    for stage in (root/'output').glob('*/state.json'):
        try:
            within(stage, root)
            observed = stage.stat().st_mtime
        except (OSError, ValueError):
            continue
        row = record(stage, {})
        if not isinstance(row, dict) or not row.get('job_id') or row.get('complete') and not row.get('no_upload'):
            continue
        status = 'Needs attention' if row.get('error') else 'Ready for review' if row.get('complete') else 'In progress'
        entries[stage.parent.name] = activity(channel, 'Local production', safe_text(row.get('title'), 'Production run'),
                                             status, observed, job=stage.parent.name)
    return list(entries.values())


def shotsnip_activity(root, previews):
    result = []
    queue = root/'work/manual/jobs.sqlite'
    ledger = record(root/'work/scheduler/state.json', {})
    if not isinstance(ledger, dict):
        ledger = {}
    manual_ids = set()
    queue_available = False
    manual_counts = Counter()
    controls = []
    try:
        with closing(sqlite3.connect(queue.as_uri()+'?mode=ro', uri=True, timeout=1)) as db:
            manual_counts.update(dict(db.execute('SELECT status,count(*) FROM jobs GROUP BY status')))
            state_names = {
                'published': 'Published', 'ready': 'Ready for review', 'queued': 'Queued',
                'queued_publish': 'Queued to publish', 'running': 'In progress', 'publishing': 'Publishing',
                'partial': 'Needs attention', 'failed': 'Failed', 'attention': 'Needs attention',
            }
            waiting_rows = []
            for identity, status, updated, payload, options, created in db.execute(
                'SELECT id,status,updated,result,options,created FROM jobs ORDER BY updated DESC LIMIT 100'
            ):
                job = read_json_text(payload)
                publication = job.get('publication') or {}
                if not isinstance(publication, dict):
                    publication = {}
                video_id = publication.get('youtube_id')
                if isinstance(video_id, str) and video_id:
                    manual_ids.add(video_id)
                if status in state_names:
                    settings = read_json_text(options)
                    targets = settings.get('platforms')
                    if not isinstance(targets, list):
                        targets = []
                    result.append(activity('shotsnip', 'Manual clip', safe_text(job.get('title'), 'Manual clip'),
                                           state_names[status], updated, youtube(video_id), job=identity))
                    if status != 'published':
                        preview = False
                        if status == 'ready' and isinstance(identity, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,100}', identity):
                            try:
                                path = within(job.get('file', ''), root/'work/manual'/identity)
                                if path.is_file() and path.suffix.lower() == '.mp4':
                                    previews[identity] = {'path': str(path), 'revision': updated}
                                    preview = True
                            except (OSError, ValueError, TypeError):
                                pass
                        if status == 'queued_publish':
                            waiting_rows.append({'id': identity, 'status': status, 'created': created, 'options': settings})
                        controls.append(dict(id=identity, revision=updated, status=status, at=date(updated), created=created,
                                             not_before=date(settings.get('not_before')) if isinstance(settings.get('not_before'), (int, float)) else None,
                                             title=safe_text(job.get('title'), 'Manual clip'),
                                             destinations=[p for p in targets if p in ('youtube', 'facebook')],
                                             privacy=settings.get('privacy') if settings.get('privacy') in ('private', 'unlisted') else 'public',
                                             preview=preview))
            queue_available = True
    except (OSError, sqlite3.Error):
        waiting_rows = []
    schedule_config = record(root/'scheduler.json', {})
    manual_schedule = None
    try:
        from .dashboard_controls import manual_module
        module = manual_module(root)
        times = module.estimate(waiting_rows, ledger.get('last_upload_at', 0), schedule_config)
        order = sorted(times, key=times.get)
        for row in controls:
            if row['id'] in times:
                row['eta'] = date(times[row['id']])
                row['position'] = order.index(row['id']) + 1
        sample = {'options': {'local_library_batch': True}} if any(r['options'].get('local_library_batch') for r in waiting_rows) else {'options': {}}
        manual_schedule = {'gap_minutes': module.publish_gap_minutes(sample, schedule_config),
                           'custom': isinstance(schedule_config.get('manual_post_minutes'), int),
                           'choices': list(getattr(module, 'MANUAL_GAP_CHOICES', ())),
                           'last_upload': date(ledger.get('last_upload_at')), 'next_at': date(min(times.values())) if times else None}
    except Exception:  # noqa: BLE001 - the queue still works without estimates
        pass
    for row in ledger.get('uploads', []) if isinstance(ledger.get('uploads'), list) else []:
        if not isinstance(row, dict) or row.get('video_id') in manual_ids:
            continue
        status = row.get('status')
        if status not in GOOD | {'uncertain', 'pending', 'facebook_uncertain', 'facebook_pending', 'quarantined'}:
            continue
        title = safe_text(row.get('title'), 'Scheduled clip')
        path = row.get('file')
        run = None
        if isinstance(path, str) and Path(path).is_absolute():
            try:
                source = within(Path(path).parent/'metadata.json', root/'work')
                metadata = record(source, {})
                if isinstance(metadata, dict):
                    title = safe_text(metadata.get('title'), title)
                # Live-capture clips publish from work/live/<run>/ with the same run naming.
                base = next((root/'work'/area for area in ('scheduler', 'live') if Path(path).is_relative_to(root/'work'/area)), None)
                parts = Path(path).relative_to(base).parts if base else ()
                if len(parts) >= 2 and re.fullmatch(r'\d{8}-\d{6}-[A-Za-z0-9_-]+', parts[0]):
                    run = parts[0]
            except (OSError, ValueError):
                pass
        result.append(activity('shotsnip', 'Scheduled clip', title, 'Published' if status in GOOD else 'Needs attention',
                               row.get('finished_at') or row.get('started_at'), youtube(row.get('video_id')), job=run))
    phase = ledger.get('phase')
    phase_labels = {
        'queued':'Queued', 'waiting':'Waiting', 'paused':'Paused', 'ready':'Ready',
        'scanning':'Scanning', 'capturing':'Capturing', 'analyzing':'Analyzing', 'finishing':'Finishing',
        'uploading':'Publishing', 'published':'Published', 'error':'Needs attention',
    }
    schedule = record(root/'scheduler.json', {})
    return result, {
        'queue_available': queue_available,
        'waiting': sum(manual_counts[k] for k in ('queued', 'queued_publish', 'ready')),
        'attention': sum(manual_counts[k] for k in ('failed', 'attention', 'partial')),
        'scheduler_status': phase_labels.get(phase, 'Unknown') if isinstance(phase, str) else 'Unknown',
        'scheduler_at': date(ledger.get('heartbeat_at')),
        'scheduler_enabled': schedule.get('enabled') if isinstance(schedule, dict) else None,
        'scheduler_hold': bool(schedule.get('live_operations_batch_hold')) if isinstance(schedule, dict) else False,
        'manual_jobs': controls,
        'manual_schedule': manual_schedule,
    }


def read_json_text(value):
    try:
        result = json.loads(value or '{}')
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError):
        return {}


def archives(state, roots):
    rows = []
    for path in (state/'manifests').glob('*.json'):
        if not re.fullmatch('[a-f0-9]{24}', path.stem):
            continue
        manifest = record(path, {})
        if not isinstance(manifest, dict) or manifest.get('status') != 'verified_local_retained':
            continue
        channel = manifest.get('channel')
        if not isinstance(channel, str) or channel not in roots:
            continue
        member = next((item for item in manifest.get('files', [])
                       if isinstance(item, dict) and str(item.get('name', '')).endswith('.mp4')), None)
        folder = manifest.get('local_dir')
        if not member or not isinstance(folder, str):
            continue
        directory = Path(folder)
        boundary = state/'github-artifacts' if manifest.get('github_artifact_id') else roots[channel]
        try:
            media = within(directory/str(member['name']), boundary)
        except (OSError, ValueError, KeyError):
            continue
        metadata = record(media.parent/'metadata.json', {})
        title = safe_text(metadata.get('title'), 'Finished video') if isinstance(metadata, dict) else 'Finished video'
        url = drive(member.get('drive_file_id'))
        receipt = state/'archive-receipts'/path.name
        restore = state/'restore-checks'/path.name
        receipt_row = record(receipt, {})
        restore_row = record(restore, {})
        try:
            manifest_hash = digest(path) if isinstance(receipt_row, dict) and 'manifest_sha256' in receipt_row else None
        except OSError:
            continue
        verified = ((manifest_hash is not None and receipt_row.get('manifest_sha256') == manifest_hash)
                    or (isinstance(restore_row, dict) and restore_row.get('archive_id') == path.stem))
        rows.append(dict(id=path.stem, channel=channel, title=title,
                         at=date(manifest.get('completed_at')), archived_at=date(manifest.get('verified_at')),
                         state='Drive + local' if verified and media.is_file() else 'Drive only' if verified else 'Verification pending',
                         size=member['size'] if type(member.get('size')) is int else None,
                         url=url if verified else None))
    return sorted(rows, key=lambda row: row['archived_at'] or '', reverse=True)


def snapshot(config, previews=None, *, include_finance=False, media=None):
    roots = {name: Path(config['roots'][name]).absolute() for name in LABELS}
    state = Path(config['state_dir'])
    queue, health = shotsnip_activity(roots['shotsnip'], previews if previews is not None else {})
    alternate = config.get('dashboard_histold_index_root')
    rows = queue + story_activity(roots['histold'], 'histold', Path(alternate) if alternate else None)
    rows.sort(key=lambda item: item['at'] or '', reverse=True)
    library = archives(state, roots)
    tick = record(state/'last-archive-tick.json', {})
    cleanup = record(state/'last-finished-cleanup-tick.json', {})
    if cleanup.get('channel') and cleanup['channel'] not in LABELS:
        cleanup = {}
    raw = record(roots['shotsnip']/'work/dashboard/cleanup.json', {})
    raw = raw.get('raw_segments') if isinstance(raw, dict) else None
    if not isinstance(raw, dict):
        raw = record(state/'raw-retention-first-run.json', {})
    footprint = shutil.disk_usage(state)
    channel_summary = []
    visible_rows = []
    for channel, label in LABELS.items():
        channel_rows = [row for row in rows if row['channel'] == channel]
        publications = [row for row in channel_rows if row['status'] == 'Published']
        visible_rows.extend(channel_rows[:50])
        channel_summary.append(dict(id=channel, name=label, archived=sum(
            row['channel'] == channel and row['state'] != 'Verification pending' for row in library),
            last_published=publications[0]['at'] if publications else None,
            activity_count=len(channel_rows)))
    cleanup_statuses = {'no_eligible_clip': 'No eligible clip', 'removed': 'Verified and removed',
                        'deferred':'Deferred', 'failed':'Failed', 'protection_changed':'Protected on recheck',
                        'verified_pending_removal':'Pending confirmation'}
    try:
        commands = overview(config)
    except (OSError, sqlite3.Error):
        commands = []
    backup = record(state/'backups/status.json', {})
    approvals = []
    source = Path(alternate) if alternate else roots['histold']
    now = datetime.now(timezone.utc)
    for draft in record(source/'data/pending_approvals.json', []):
        if not isinstance(draft, dict):
            continue
        stage, run_id, created = draft.get('stage_dir_name'), str(draft.get('run_id')), date(draft.get('created'))
        if (not isinstance(stage, str) or not re.fullmatch(r'\d{8}_\d{6}(?:_\d{6})?_(?:short|long)', stage)
                or not re.fullmatch(r'\d{1,20}', run_id) or not created
                or not 0 <= (now-datetime.fromisoformat(created)).total_seconds() < 14*86400):
            continue
        approvals.append(dict(title=safe_text(draft.get('title'), 'Histold draft'), stage=stage, run_id=run_id, at=created,
                              url='https://github.com/' + config.get('github_repo', 'kumarvnsh/YT-Automation') + '/actions/runs/' + run_id))
        if include_finance and media is not None and config.get('dashboard_private_snapshot'):
            from .media import draft_preview
            approvals[-1]['preview'] = draft_preview(config, stage, media)
    return dict(
        version=1, generated_at=stamp(), channels=channel_summary,
        analytics=analytics(roots, source, rows),
        pacing_experiment=pacing_experiment(roots['shotsnip']),
        video_insights=video_summary(config) if include_finance else None,
        histold_live=histold_live_snapshot(config) if include_finance else None,
        phase3=phase3(config, source, roots['shotsnip'], rows, approvals, now, include_finance=include_finance, media=media),
        shotsnip_workspace=shotsnip_workspace(roots['shotsnip']),
        activity=sorted(visible_rows, key=lambda item: item['at'] or '', reverse=True), archives=library[:250],
        health=health,
        commands=commands, controls_enabled=config.get('dashboard_controls_enabled') is True,
        approvals=approvals,
        backup_at=date(backup.get('at')) if isinstance(backup, dict) else None,
        storage=dict(free=footprint.free, total=footprint.total,
                     drive_only=sum(row['state'] == 'Drive only' for row in library),
                     archive_count=sum(row['state'] != 'Verification pending' for row in library)),
        operations=dict(
            archive=dict(at=date(tick.get('observed_at')), copied=len(tick.get('archived', []))+len(tick.get('hosted_stories_archived', [])),
                         errors=len(tick.get('failed', []))+int(bool(tick.get('hosted_story_error')))),
            cleanup=dict(at=date(cleanup.get('observed_at')), status=cleanup_statuses.get(cleanup.get('status'), 'Awaiting report'),
                         removed=cleanup.get('removed', 0), bytes_removed=cleanup.get('bytes_removed', 0),
                         channel=cleanup.get('channel') if cleanup.get('channel') in LABELS else None),
            raw=dict(removed=raw.get('removed'), bytes_removed=raw.get('bytes_removed'))),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    previews = {}
    config = read_json(args.config)
    private_path = config.get('dashboard_private_snapshot')
    include_finance = isinstance(private_path, str) and Path(private_path).resolve() == args.output.resolve()
    media = {}
    data = snapshot(config, previews, include_finance=include_finance, media=media)
    atomic_json(args.output.with_name('dashboard-previews.json'), previews)
    if include_finance:
        atomic_json(args.output.with_name('dashboard-media.json'), media)
    atomic_json(args.output, data)


if __name__ == '__main__':
    main()
