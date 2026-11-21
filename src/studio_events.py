"""Best-effort Studio HQ observations; never owns publishing or blocks a job.

One atomic JSON file per event avoids cross-process append races. Never record
prompts, raw responses, exception messages, URLs, credentials or SDK headers.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
import uuid

_context = ContextVar('studio_hq_context', default=None)


def utcnow():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def scope(root, channel, job_id, stage):
    token = _context.set((Path(root), str(channel), str(job_id), str(stage)))
    try:
        yield
    finally:
        _context.reset(token)


def emit(kind, **fields):
    """Emit under the active channel root; all telemetry failures are nonfatal."""
    try:
        ctx = _context.get()
        if ctx is None:
            return
        root, channel, job_id, stage = ctx
        folder = root / 'data/private/studio-hq/events'
        folder.mkdir(parents=True, exist_ok=True)
        identity = uuid.uuid4().hex
        row = dict(schema_version=1, event_id=identity, observed_at=utcnow(),
                   channel=channel, job_id=job_id, stage=stage, kind=kind, **fields)
        temporary = folder / (identity + '.tmp')
        temporary.write_text(json.dumps(row, ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, folder / (identity + '.json'))
    except Exception:
        pass


TEXT_FIELDS = frozenset({'event_id', 'observed_at', 'channel', 'job_id', 'stage', 'kind', 'provider',
                         'operation', 'model', 'status', 'error_type', 'cost_status', 'pricing_basis',
                         'retry_visibility'})
USAGE_FIELDS = frozenset({'input_tokens', 'output_tokens', 'prompt_tokens', 'completion_tokens', 'total_tokens',
                          'cache_creation_input_tokens', 'cache_read_input_tokens', 'web_search_requests',
                          'characters', 'images', 'seconds'})


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def scrub(row):
    """Allowlisted copy for indexes and hosted export; drops free text, URLs and messages.

    Unknown keys survive only as booleans or numbers. Returns None when the row
    lacks the identity fields an index needs.
    """
    if not isinstance(row, dict) or row.get('schema_version') != 1:
        return None
    clean = {'schema_version': 1}
    for key, value in row.items():
        if not isinstance(key, str) or len(key) > 40:
            continue
        if key in TEXT_FIELDS:
            if isinstance(value, str):
                clean[key] = value[:120]
        elif key == 'cost_usd':
            if _number(value) or (isinstance(value, str) and len(value) <= 20):
                clean[key] = value
        elif key == 'usage':
            if isinstance(value, dict):
                usage = {name: number for name, number in value.items() if name in USAGE_FIELDS and _number(number)}
                clean[key] = usage or None
        elif isinstance(value, bool) or _number(value):
            clean[key] = value
    if any(not isinstance(clean.get(key), str) for key in ('event_id', 'channel', 'job_id', 'stage', 'kind', 'observed_at')):
        return None
    return clean


def record_response(provider, operation, model, response, *, elapsed_seconds=None):
    """Usage is observed, money is unknown until a versioned price basis exists."""
    try:
        usage = getattr(response, 'usage', None)
        names = ('input_tokens', 'output_tokens', 'prompt_tokens', 'completion_tokens',
                 'total_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens')
        observed = {}
        for name in names:
            value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                observed[name] = value
        server = getattr(usage, 'server_tool_use', None)
        searches = getattr(server, 'web_search_requests', None)
        if isinstance(searches, int):
            observed['web_search_requests'] = searches
        # Plain HTTP clients (requests) return error statuses instead of raising.
        http_status = getattr(response, 'status_code', None)
        failed = isinstance(http_status, int) and not isinstance(http_status, bool) and http_status >= 400
        extra = {'http_status': http_status} if failed else {}
        emit('api_usage', provider=provider, operation=operation, model=str(model),
             status='failed' if failed else 'response_received', usage=observed or None, **extra,
             elapsed_seconds=elapsed_seconds, cost_usd=None, pricing_basis=None,
             cost_status='unknown_price_not_configured',
             retry_visibility='application_calls_only_sdk_retries_not_observed')
    except Exception:
        pass


def call(provider, operation, model_name, function, **kwargs):
    """Preserve call semantics; record usage even when later validation fails."""
    started = time.monotonic()
    model = model_name or kwargs.get('model', 'unknown')
    try:
        response = function(**kwargs)
    except Exception as exc:
        emit('api_usage', provider=provider, operation=operation, model=str(model),
             status='failed', error_type=type(exc).__name__, usage=None,
             cost_usd=None, pricing_basis=None, cost_status='unknown_failed_request',
             elapsed_seconds=round(time.monotonic() - started, 3),
             retry_visibility='application_calls_only_sdk_retries_not_observed')
        raise
    record_response(provider, operation, model, response,
                    elapsed_seconds=round(time.monotonic() - started, 3))
    return response


@contextmanager
def stream_call(provider, operation, model_name, function, **kwargs):
    """Record failed streams; caller records the final usage on success."""
    started = time.monotonic()
    try:
        with function(**kwargs) as response:
            yield response
    except Exception as exc:
        emit('api_usage', provider=provider, operation=operation,
             model=str(model_name or kwargs.get('model', 'unknown')), status='failed',
             error_type=type(exc).__name__, usage=None, cost_usd=None,
             pricing_basis=None, cost_status='unknown_failed_stream',
             elapsed_seconds=round(time.monotonic() - started, 3),
             retry_visibility='application_calls_only_sdk_retries_not_observed')
        raise


def record_finished(video, channel, job_id):
    """Bind completion time to actual final bytes' stat; never infer from mtime.

    Completion marker is additive. Archive discovery rechecks this binding and
    publication state independently. Returning False cannot fail production.
    """
    try:
        video = Path(video)
        stat = video.stat()
        path = video.parent / (video.name + '.studio-hq.json')
        if path.exists():
            previous = json.loads(path.read_text(encoding='utf-8'))
            if (previous.get('channel'), previous.get('filename'), previous.get('size'), previous.get('mtime_ns')) == (channel, video.name, stat.st_size, stat.st_mtime_ns):
                return True
        value = dict(schema_version=1, channel=channel, job_id=str(job_id),
                     filename=video.name, completed_at=utcnow(),
                     size=stat.st_size, mtime_ns=stat.st_mtime_ns)
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
        os.replace(temporary, path)
        return True
    except Exception:
        return False
