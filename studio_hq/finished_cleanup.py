"""Bounded, opt-in removal of verified Shotsnip scheduler exports."""
from contextlib import closing, contextmanager, nullcontext
import json
from pathlib import Path
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
import time

from .archive import (Rclone, atomic_json, checked_manifest, digest, hosted_story_evidence, read_json,
                      manual_publication_age, published_manual_jobs, stamp, timestamp, within, worker_lock)
from src.studio_outputs import published_story, stage_media_lock


def references_run(value, root, run):
    if isinstance(value, str):
        path = Path(value)
        if not path.is_absolute():
            if not value.replace('\\', '/').lower().startswith('work/'):
                return False
            path = root / path
        return path.resolve().is_relative_to(run)
    if isinstance(value, dict):
        return any(references_run(item, root, run) for item in value.values())
    if isinstance(value, list):
        return any(references_run(item, root, run) for item in value)
    return False


@contextmanager
def shotsnip_snapshot(root):
    from filelock import FileLock

    scheduler = within(root/'work/scheduler/worker.lock', root)
    live_worker = within(root/'work/dashboard/worker.lock', root)
    queue = within(root/'work/manual/jobs.sqlite', root)
    with worker_lock(scheduler.parent, scheduler.name), FileLock(str(live_worker), timeout=0):
        with closing(sqlite3.connect(queue.as_uri()+'?mode=rw', uri=True, timeout=0)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            ledger = read_json(within(scheduler.parent/'state.json', root))
            if not isinstance(ledger.get('uploads'), list) or not isinstance(ledger.get('ready_clips', []), list):
                raise ValueError('Invalid Shotsnip publishing ledger')
            jobs = [json.loads(value or '{}') for row in db.execute(
                "SELECT options,result FROM jobs WHERE status IS NULL OR status NOT IN ('published','cancelled')"
            ) for value in row]
            ledger['studio_hq_published_manual_jobs'] = published_manual_jobs(db)
            yield ledger, jobs


def eligible_video(manifest, root, ledger, jobs, now):
    if manifest['channel'] != 'shotsnip' or manifest['status'] != 'verified_local_retained':
        return None
    row = manifest['files'][0]
    if row['name'] not in ('finished-short.mp4', 'commentary-short.mp4', 'narrated-short.mp4'):
        return None
    source = Path(manifest['local_dir'])/row['name']
    manual_root = root/'work/manual'
    manual = source.is_relative_to(manual_root)
    base = manual_root if manual else root/'work/scheduler'
    if not source.is_relative_to(base):
        return None
    video = within(source, base)
    parts = video.relative_to(base).parts
    if not video.is_file() or len(parts) != 3:
        return None
    if any((p/'.studio-hq-keep').exists() for p in video.parents if p.is_relative_to(root)):
        return None
    run = video.parent.parent
    if references_run([jobs, ledger.get('ready_clips', [])], root, run):
        return None
    if manual:
        evidence = manual_publication_age(video, row['sha256'], ledger,
                                          ledger.get('studio_hq_published_manual_jobs', []))
        if not evidence or evidence[0] != parts[0] or now-evidence[1] < 48*3600:
            return None
        return video if package_matches(manifest, video, root) else None
    uploads = [item for item in ledger['uploads'] if references_run(item, root, run)]
    if any(item.get('status') != 'uploaded' or not item.get('video_id')
           or (item.get('facebook') or {}).get('status') != 'published' for item in uploads):
        return None
    matches = [item for item in uploads if Path(item.get('file', '')) == video
               and item.get('sha256') == row['sha256'] and item.get('finished_at')]
    if len(matches) != 1 or now-timestamp(matches[0]['finished_at']) < 48*3600:
        return None
    return video if package_matches(manifest, video, root) else None


def package_matches(manifest, video, root):
    for entry in manifest['files']:
        member = within(video.parent/entry['name'], root)
        if not member.is_file() or member.stat().st_size != entry['size'] or digest(member) != entry['sha256']:
            return False
    return True


@contextmanager
def cleanup_candidate(manifest, config):
    channel = manifest['channel']
    root = Path(config['roots'][channel]).absolute()
    if channel == 'shotsnip':
        with shotsnip_snapshot(root) as (ledger, jobs):
            yield eligible_video(manifest, root, ledger, jobs, time.time())
        return
    if config.get('story_finished_cleanup_enabled') is not True:
        yield None
        return
    source = Path(manifest['local_dir'])/'video.mp4'
    state = Path(config['state_dir']).absolute()
    hosted = manifest.get('github_artifact_id')
    if hosted:
        artifact = state/'github-artifacts'/str(int(hosted))
        stage_root = artifact/'files'
    else:
        stage_root = root/'output'
    if (manifest['status'] != 'verified_local_retained' or manifest['files'][0]['name'] != source.name
            or not source.is_relative_to(stage_root) or not source.is_file()):
        yield None
        return
    video = within(source, stage_root)
    boundary = artifact if hosted else root
    with nullcontext() if hosted else stage_media_lock(video.parent):
        if hosted:
            origin = read_json(artifact/'source.json')
            eligible = (int(hosted) == origin['id'] and manifest['github_run_id'] == origin['run_id']
                        and channel == origin['channel'] and manifest['completed_at'] == origin['created_at']
                        and hosted_story_evidence(artifact, video))
        else:
            stage = read_json(within(video.parent/'state.json', root))
            marker = read_json(within(video.with_name(video.name+'.studio-hq.json'), root))
            approval = root/'data/pending_approvals.json'
            eligible = (stage.get('studio_hq_managed_output') is True and published_story(stage)
                        and marker.get('channel') == channel and marker.get('filename') == video.name
                        and marker.get('job_id') == manifest['job_id']
                        and marker.get('completed_at') == manifest['completed_at']
                        and marker.get('size') == video.stat().st_size
                        and marker.get('mtime_ns') == video.stat().st_mtime_ns
                        and (not approval.exists() or video.parent.name not in approval.read_text(encoding='utf-8-sig')))
        keep = any((p/'.studio-hq-keep').exists() for p in video.parents if p.is_relative_to(boundary))
        can_remove = (eligible and not keep and time.time()-timestamp(manifest['completed_at']) >= 48*3600
                      and package_matches(manifest, video, boundary))
        yield video if can_remove else None


def cleanup_tick(config):
    """Restore into a temporary directory before deleting at most one local MP4."""
    from filelock import Timeout

    state = Path(config['state_dir']).absolute()
    report = dict(observed_at=stamp(), status='no_eligible_clip', removed=0, bytes_removed=0)
    try:
        with worker_lock(state):
            candidates = []
            for path in sorted((state/'archive-receipts').glob('*.json')):
                manifest_path = state/'manifests'/path.name
                manifest = checked_manifest(read_json(manifest_path))
                destination = config['remote'].rstrip('/')+'/'+manifest['remote_subdir']
                if read_json(path) != {'destination': destination, 'manifest_sha256': digest(manifest_path)}:
                    continue
                if (state/'finished-cleanup-receipts'/path.name).exists():
                    continue
                with cleanup_candidate(manifest, config) as video:
                    if video is not None:
                        candidates.append((timestamp(manifest['completed_at']), manifest['archive_id'], manifest, destination, video))
            if candidates:
                _, identity, manifest, destination, video = min(candidates, key=lambda item: item[:2])
                with TemporaryDirectory(prefix='finished-cleanup-', dir=within(state, state)) as scratch:
                    recovered = Path(scratch)
                    Rclone(config).restore_files(destination, recovered, [entry['name'] for entry in manifest['files']])
                    for entry in manifest['files']:
                        copy = recovered/entry['name']
                        if copy.stat().st_size != entry['size'] or digest(copy) != entry['sha256']:
                            raise ValueError('Drive restore does not match the archive manifest')
                    ffprobe = config['cleanup_ffprobe']
                    probe = subprocess.run([ffprobe, '-v', 'error', '-select_streams', 'v:0',
                                            '-show_entries', 'stream=codec_name', '-of', 'default=nw=1',
                                            str(recovered/video.name)], capture_output=True, text=True, timeout=30)
                    if probe.returncode or 'codec_name=' not in probe.stdout:
                        raise ValueError('Restored clip failed video readability check')
                    with cleanup_candidate(manifest, config) as rechecked:
                        if rechecked != video:
                            report['status'] = 'protection_changed'
                        else:
                            report.update(status='verified_pending_removal', archive_id=identity, channel=manifest['channel'],
                                          original=str(video), verified_files=len(manifest['files']))
                            removal_record = state/'finished-cleanup-receipts'/f'{identity}.json'
                            atomic_json(removal_record, report)
                            video.unlink()
                            report.update(status='removed', removed=1, bytes_removed=manifest['files'][0]['size'],
                                          observed_at=stamp())
                            atomic_json(removal_record, report)
    except (Timeout, OSError, ValueError, KeyError, RuntimeError, sqlite3.Error, subprocess.TimeoutExpired) as exc:
        report['status'] = 'deferred' if isinstance(exc, (Timeout, sqlite3.OperationalError)) else 'failed'
        report['error_type'] = type(exc).__name__
    atomic_json(state/'last-finished-cleanup-tick.json', report)
    return report
