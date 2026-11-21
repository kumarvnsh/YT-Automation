"""Explicit archive packages with content verification and isolated restores.

This phase intentionally has NO local deletion operation. Plan output is not
authorization to erase files. Existing production settings are left untouched.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import subprocess
import time
import uuid
import zipfile

from src.studio_outputs import published_story

CHANNELS = {'histold', 'astrotold', 'shotsnip'}
MEDIA = {'video.mp4', 'finished-short.mp4', 'narrated-short.mp4', 'commentary-short.mp4'}
SUPPORT = {'script.json', 'research.json', 'storyboard.json', 'metadata.json',
           'transcript.json', 'timings.json', 'quality.json', 'quality-report.json',
           'audio-check.json', 'captions.ass', 'captions.srt', 'overlays.ass',
           'thumbnail.png', 'thumbnail.jpg', 'thumbnail.webp'}


def stamp():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def hosted_story_evidence(artifact, video):
    """Check published workflow state and bind extracted bytes to the retained ZIP."""
    member = video.relative_to(artifact/'files')
    with zipfile.ZipFile(within(artifact/'artifact.zip', artifact)) as bundle:
        stage = bundle.getinfo(member.with_name('state.json').as_posix())
        if stage.file_size > 16 * 1024**2 or not published_story(json.loads(bundle.read(stage))):
            return False
        media = bundle.getinfo(member.as_posix())
        if media.file_size != video.stat().st_size:
            return False
        with bundle.open(media) as stream:
            sha256 = hashlib.sha256()
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                sha256.update(block)
            return sha256.hexdigest() == digest(video)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def within(path, root):
    """Reject symlinks/junctions at any component, including configured roots."""
    path, root = Path(path).absolute(), Path(root).absolute()
    if not path.is_relative_to(root):
        raise ValueError('Artifact outside configured root')
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise ValueError('Reparse paths are not archive inputs')
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Resolved artifact outside configured root')
    return path


def digest(path, name='sha256'):
    result = hashlib.new(name)
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def fingerprint(path):
    before = Path(path).stat()
    value = {'size': before.st_size, 'mtime_ns': before.st_mtime_ns,
             'md5': digest(path, 'md5'), 'sha256': digest(path)}
    after = Path(path).stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('Artifact changed while hashing')
    return value


def timestamp(value):
    if isinstance(value, (int, float)):
        return float(value)
    parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Completion time needs an explicit timezone')
    return parsed.timestamp()


def manual_protections(root):
    """Read existing queue without importing modules that initialize/write it."""
    database = root / 'work/manual/jobs.sqlite'
    if not database.exists():
        return set()
    protected = set()
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
        for identity, status, result in db.execute('SELECT id,status,result FROM jobs'):
            if status != 'published':
                protected.add(str(root / 'work/manual' / identity))
                row = json.loads(result or '{}')
                if row.get('file'):
                    protected.add(str(Path(row['file']).absolute()))
    return protected


def published_manual_jobs(db):
    return [(job_id, float(updated), json.loads(options or '{}'), json.loads(result or '{}'))
            for job_id, updated, options, result in db.execute(
                "SELECT id,updated,options,result FROM jobs WHERE status='published'")]


def manual_publication_age(video, sha256, ledger, jobs):
    """Bind a published manual job and its selected destinations to these exact bytes."""
    matches = [(job_id, updated, options, result) for job_id, updated, options, result in jobs
               if result.get('file') and Path(result['file']) == video]
    if len(matches) != 1:
        return None
    job_id, updated, options, result = matches[0]
    targets = set(options.get('platforms', []))
    publication = result.get('publication') or {}
    if not targets or targets - {'youtube', 'facebook'}:
        return None
    uploads = [row for row in ledger['uploads'] if row.get('file') and Path(row['file']) == video]
    if not uploads or any(row.get('status') not in ('uploaded', 'facebook_published')
                          or (row.get('facebook') or {}).get('status') in ('pending', 'error', 'failed', 'uncertain')
                          for row in uploads):
        return None
    for row in uploads:
        if row.get('sha256') != sha256:
            continue
        if 'youtube' in targets and (not row.get('video_id') or row['video_id'] != publication.get('youtube_id')):
            continue
        if 'facebook' in targets and ((row.get('facebook') or {}).get('status') != 'published'
                                     or (publication.get('facebook') or {}).get('status') != 'published'):
            continue
        return job_id, max(updated, timestamp(row.get('finished_at') or row['started_at']))
    return None


def discover(config, now=None):
    """List finals with reasons; never select files from age/mtime alone."""
    now = time.time() if now is None else now
    output = []
    for channel, location in config['roots'].items():
        if channel not in CHANNELS:
            raise ValueError('Unknown channel')
        root = Path(location).absolute()
        bases = [root / 'work/scheduler', root / 'work/manual', root / 'work/live'] if channel == 'shotsnip' else [root / 'output']
        state_error = False
        protections = set()
        scheduler = {}
        if channel == 'shotsnip':
            try:
                protections = manual_protections(root)
                scheduler = read_json(root / 'work/scheduler/state.json')
                if not isinstance(scheduler.get('uploads'), list):
                    raise ValueError('No complete publishing ledger')
            except (OSError, ValueError, sqlite3.Error, TypeError):
                state_error = True
        for base in bases:
            if not base.is_dir():
                continue
            for video in sorted(base.rglob('*.mp4')):
                if video.name not in MEDIA:
                    continue
                row = dict(channel=channel, path=str(video), eligible=False, reasons=[], completed_at=None)
                try:
                    within(video, root)
                    run = video.parent
                    if state_error:
                        row['reasons'].append('publishing_state_unavailable')
                    for ancestor in (run, *run.parents):
                        if ancestor == root.parent:
                            break
                        if (ancestor / '.studio-hq-keep').exists():
                            row['reasons'].append('keep_flag')
                            break
                    if any(video == Path(p) or video.is_relative_to(Path(p)) for p in protections):
                        row['reasons'].append('manual_job_needs_files')
                    if channel == 'shotsnip':
                        if any(str(video) == str(r.get('file')) for r in scheduler.get('ready_clips', [])):
                            row['reasons'].append('pending_publication')
                        matches = [r for r in scheduler.get('uploads', []) if str(r.get('file')) == str(video)]
                        if not matches or any(r.get('status') not in ('uploaded', 'facebook_published') for r in matches):
                            row['reasons'].append('publication_not_confirmed')
                        if any((r.get('facebook') or {}).get('status') in ('pending', 'uncertain', 'failed', 'error') for r in matches):
                            row['reasons'].append('facebook_not_confirmed')
                    else:
                        state = read_json(run / 'state.json')
                        if not state.get('complete') or state.get('error') or state.get('no_upload'):
                            row['reasons'].append('job_incomplete_or_review_pending')
                        if not (state.get('youtube_id') or (state.get('meta') or {}).get('facebook')):
                            row['reasons'].append('publication_not_confirmed')
                    marker = read_json(video.with_name(video.name + '.studio-hq.json'))
                    stat = video.stat()
                    if marker.get('channel') != channel or marker.get('filename') != video.name or not marker.get('job_id'):
                        raise ValueError('Invalid completion binding')
                    if (marker.get('size'), marker.get('mtime_ns')) != (stat.st_size, stat.st_mtime_ns):
                        row['reasons'].append('finished_media_changed')
                    completed = timestamp(marker['completed_at'])
                    row.update(job_id=str(marker['job_id']), completed_at=marker['completed_at'])
                    if completed > now or now - completed < 48 * 3600:
                        row['reasons'].append('younger_than_48_hours')
                except FileNotFoundError:
                    row['reasons'].append('completion_or_state_record_missing')
                except (OSError, ValueError, KeyError, TypeError):
                    row['reasons'].append('invalid_or_unsafe_record')
                row['eligible'] = not row['reasons']
                output.append(row)
    return output


def package(candidate, root):
    if not candidate.get('eligible') or candidate['channel'] not in CHANNELS:
        raise ValueError('Candidate is not eligible')
    video = within(candidate['path'], root)
    names = [video]
    for p in sorted(video.parent.iterdir()):
        if p.is_file() and (p.name in SUPPORT or re.fullmatch(r'preview-\d+(?:\.\d+)?\.jpg', p.name)):
            within(p, root)
            names.append(p)
    files = [dict(name=p.name, **fingerprint(p)) for p in names]
    identity = hashlib.sha256((candidate['channel'] + ':' + str(video.relative_to(root))
                              + ':' + json.dumps([(r['name'], r['sha256']) for r in files])).encode()).hexdigest()[:24]
    date = datetime.fromtimestamp(timestamp(candidate['completed_at']), timezone.utc)
    return dict(schema_version=1, archive_id=identity, channel=candidate['channel'],
                job_id=candidate['job_id'], completed_at=candidate['completed_at'],
                local_dir=str(video.parent), files=files,
                remote_subdir=f"{candidate['channel']}/{date:%Y/%m}/{identity}")


@contextmanager
def worker_lock(directory, name='archive.lock'):
    path = Path(directory) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        if stream.tell() == 0:
            stream.write(b'0'); stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class Rclone:
    def __init__(self, config):
        self.config = config

    def run(self, *args):
        cooldown = Path(self.config['state_dir']) / 'transfer-cooldown.json'
        if cooldown.exists():
            if read_json(cooldown).get('retry_after_epoch', 0) > time.time():
                raise RuntimeError('Drive transfer deferred by persisted rate-limit backoff')
        options = []
        if self.config.get('drive_root_folder_id'):
            options = ['--drive-root-folder-id', self.config['drive_root_folder_id']]
        result = subprocess.run([self.config['rclone'], '--config', self.config['rclone_config'],
                                 '--retries', '2', '--low-level-retries', '3',
                                 '--contimeout', '20s', '--timeout', '120s',
                                 *options, *args], capture_output=True, text=True, encoding='utf-8', timeout=1800)
        if result.returncode:
            # stderr can include signed URLs and account information; do not publish it.
            private = Path(self.config['rclone_config']).parent
            private.mkdir(parents=True, exist_ok=True)
            (private / 'last-transfer-error.log').write_text(result.stderr, encoding='utf-8')
            if any(word in result.stderr.lower() for word in ('ratelimitexceeded', 'rate_limit_exceeded', 'userratelimitexceeded', 'error 429')):
                atomic_json(cooldown, dict(reason='provider_rate_limit', observed_at=stamp(),
                                          retry_after_epoch=time.time() + 180,
                                          local_cleanup_enabled=False))
            raise RuntimeError('rclone command failed; exit code ' + str(result.returncode))
        return result.stdout

    def stat(self, remote):
        return json.loads(self.run('lsjson', remote, '--stat', '--hash'))

    def copy(self, source, target):
        self.run('copyto', str(source), target, '--immutable', '--checksum',
                 '--bwlimit', str(self.config.get('bandwidth_limit', '4M')))

    def restore_files(self, source, target, names):
        # One exact member list avoids repeatedly listing the same Drive parents.
        members = Path(target) / '.restore-members.txt'
        members.write_text('\n'.join(names) + '\n', encoding='utf-8')
        self.run('copy', source, str(target), '--files-from-raw', str(members),
                 '--immutable', '--checksum', '--transfers', '1', '--checkers', '1',
                 '--bwlimit', str(self.config.get('bandwidth_limit', '4M')))


def checked_manifest(value):
    if value.get('schema_version') != 1 or value.get('channel') not in CHANNELS:
        raise ValueError('Unsupported archive manifest')
    if not re.fullmatch('[a-f0-9]{24}', value.get('archive_id', '')):
        raise ValueError('Invalid archive identity')
    subdir = str(value.get('remote_subdir', ''))
    if not re.fullmatch(r'(histold|astrotold|shotsnip)/\d{4}/\d{2}/[a-f0-9]{24}', subdir):
        raise ValueError('Invalid archive destination')
    if subdir.split('/')[0] != value['channel'] or subdir.split('/')[-1] != value['archive_id']:
        raise ValueError('Archive identity does not match destination')
    names = set()
    for row in value['files']:
        name = row['name']
        if name in names or '/' in name or '\\' in name or ':' in name or name in ('.', '..'):
            raise ValueError('Unsafe archive member')
        if name not in MEDIA | SUPPORT and not re.fullmatch(r'preview-\d+(?:\.\d+)?\.jpg', name):
            raise ValueError('File is not an approved archive artifact')
        if not re.fullmatch('[a-f0-9]{32}', row['md5']) or not re.fullmatch('[a-f0-9]{64}', row['sha256']):
            raise ValueError('Invalid content checksum')
        names.add(name)
    if not names.intersection(MEDIA):
        raise ValueError('No final video in archive')
    return value


def verify_remote(transfer, remote, row):
    metadata = transfer.stat(remote)
    hashes = {k.lower(): v for k, v in (metadata.get('Hashes') or {}).items()}
    if metadata.get('Size') != row['size'] or hashes.get('md5', '').lower() != row['md5']:
        raise ValueError('Remote size or checksum mismatch')
    if not metadata.get('ID'):
        raise ValueError('Remote Drive file identity is missing')
    return {'drive_file_id': metadata['ID'], 'verified_at': stamp()}


def archive_one(candidate, config, transfer=None):
    """Re-discover eligibility; candidate input alone is never trusted."""
    root = Path(config['roots'][candidate['channel']]).absolute()
    current = next((r for r in discover(config) if r['path'] == candidate['path']), None)
    if current is None or not current['eligible']:
        raise ValueError('Job is no longer archive eligible')
    value = checked_manifest(package(current, root))
    return transfer_package(value, root, config, transfer)


def transfer_package(value, root, config, transfer=None):
    """Copy/verify only; shared by eligible local jobs and artifact preservation."""
    value = checked_manifest(value)
    transfer = transfer or Rclone(config)
    destination = config['remote'].rstrip('/') + '/' + value['remote_subdir']
    for row in value['files']:
        source = within(Path(value['local_dir']) / row['name'], root)
        if fingerprint(source) != {k: row[k] for k in ('size', 'mtime_ns', 'md5', 'sha256')}:
            raise ValueError('Source changed since package creation')
        target = destination + '/' + row['name']
        transfer.copy(source, target)
        row.update(verify_remote(transfer, target, row))
        if fingerprint(source)['sha256'] != row['sha256']:
            raise ValueError('Source changed during upload')
    manifest_path = Path(config['state_dir']) / 'manifests' / (value['archive_id'] + '.json')
    # Reuse verification timestamps on retry so immutable remote manifests match.
    if manifest_path.exists():
        previous = checked_manifest(read_json(manifest_path))
        before = [(r['name'], r['sha256'], r['size']) for r in previous['files']]
        after = [(r['name'], r['sha256'], r['size']) for r in value['files']]
        if before != after:
            raise ValueError('Persisted manifest differs from verified package')
        if [r['drive_file_id'] for r in previous['files']] != [r['drive_file_id'] for r in value['files']]:
            raise ValueError('Remote identities changed; reconcile manifest before reuse')
        value = previous
    else:
        value.update(status='verified_local_retained', verified_at=stamp(), local_cleanup_enabled=False)
        atomic_json(manifest_path, value)
    # Upload the manifest after its contents have verified. It contains no tokens.
    transfer.copy(manifest_path, destination + '/archive-manifest.json')
    atomic_json(Path(config['state_dir']) / 'archive-receipts' / (value['archive_id'] + '.json'),
                {'destination': destination, 'manifest_sha256': digest(manifest_path)})
    return value


def archive_tick(config, limit=1, transfer=None):
    """Copy a bounded batch. A failed package is retried on the next tick."""
    state_dir = Path(config['state_dir'])
    root = Path(config['roots']['shotsnip']).absolute()
    archived, failed = [], []
    with worker_lock(state_dir):
        rows = discover(config)
        # For copy-only legacy migration, confirmed publication time is a
        # conservative upper bound on when these exact bytes were finished.
        ledger = root / 'work/scheduler/state.json'
        uploads = read_json(ledger)['uploads'] if ledger.is_file() else []
        database = root/'work/manual/jobs.sqlite'
        manual = []
        if database.is_file():
            with closing(sqlite3.connect(database.as_uri()+'?mode=ro', uri=True, timeout=1)) as db:
                manual = published_manual_jobs(db)
        now = time.time()
        for row in rows:
            if row['channel'] != 'shotsnip' or row['reasons'] != ['completion_or_state_record_missing']:
                continue
            video = Path(row['path'])
            if video.is_relative_to(root/'work/manual'):
                sha256 = digest(video)
                evidence = manual_publication_age(video, sha256, {'uploads': uploads}, manual)
                if evidence and now-evidence[1] >= 48*3600:
                    row.update(eligible=True, job_id=evidence[0],
                               completed_at=datetime.fromtimestamp(evidence[1], timezone.utc).isoformat(),
                               publication_hash=sha256)
                continue
            for upload in uploads:
                if (upload.get('file') != row['path'] or upload.get('status') != 'uploaded'
                        or not upload.get('video_id') or (upload.get('facebook') or {}).get('status') != 'published'):
                    continue
                try:
                    finished = upload['finished_at']
                    if now - timestamp(finished) >= 48 * 3600 and upload.get('sha256'):
                        video = Path(row['path'])
                        row.update(eligible=True, completed_at=finished, job_id=video.parent.parent.name,
                                   publication_hash=upload['sha256'])
                        break
                except (KeyError, ValueError, TypeError, OverflowError):
                    continue

        for row in sorted((r for r in rows if r['eligible']), key=lambda r: timestamp(r['completed_at'])):
            if len(archived) + len(failed) >= limit:
                break
            try:
                channel_root = Path(config['roots'][row['channel']]).absolute()
                value = package(row, channel_root)
                if 'publication_hash' in row:
                    if value['files'][0]['sha256'] != row['publication_hash']:
                        continue
                    value.update(completion_source='confirmed_publication_finished_at_upper_bound',
                                 source_age_confirmed=False)
                identity = value['archive_id']
                manifest = state_dir / 'manifests' / (identity + '.json')
                receipt = state_dir / 'archive-receipts' / (identity + '.json')
                destination = config['remote'].rstrip('/') + '/' + value['remote_subdir']
                if receipt.is_file() and manifest.is_file() and read_json(receipt) == {
                    'destination': destination, 'manifest_sha256': digest(manifest)
                }:
                    continue
                result = transfer_package(value, channel_root, config, transfer)
                archived.append(result['archive_id'])
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
                failed.append({'job_id': row['job_id'], 'channel': row['channel'], 'error': type(exc).__name__})
        report = {'observed_at': stamp(), 'archived': archived, 'failed': failed, 'cleanup_enabled': False}
        atomic_json(state_dir / 'last-archive-tick.json', report)
        return report


def restore(value, config, transfer=None):
    value = checked_manifest(value)
    transfer = transfer or Rclone(config)
    folder = Path(config['state_dir']) / 'restores' / value['archive_id'] / uuid.uuid4().hex
    folder.mkdir(parents=True, exist_ok=False)
    destination = config['remote'].rstrip('/') + '/' + value['remote_subdir']
    batched = hasattr(transfer, 'restore_files')
    if batched:
        transfer.restore_files(destination, folder, [r['name'] for r in value['files']])
    for row in value['files']:
        target = folder / row['name']
        if not batched:
            transfer.copy(destination + '/' + row['name'], str(target))
        if target.stat().st_size != row['size'] or digest(target) != row['sha256']:
            raise ValueError('Restored file failed content verification')
    record = dict(archive_id=value['archive_id'], verified_at=stamp(),
                  original_completed_at=value['completed_at'], path=str(folder),
                  local_cleanup_enabled=False)
    atomic_json(Path(config['state_dir']) / 'restore-checks' / (value['archive_id'] + '.json'), record)
    return record
