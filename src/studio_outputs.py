"""Shared reservation for Studio HQ managed story outputs."""
from contextlib import contextmanager


@contextmanager
def stage_media_lock(stage):
    # Atomic mkdir works for both native Windows and POSIX writers. A stale
    # reservation after a crash holds the media for review.
    reservation = stage / '.studio-hq-media-lock'
    reservation.mkdir()
    try:
        yield
    finally:
        reservation.rmdir()


def published_story(state):
    meta = state.get('meta') or {}
    return (state.get('complete') is True and state.get('no_upload') is False
            and not state.get('error') and not state.get('meta_error')
            and 'upload' in state.get('done', [])
            and bool(state.get('youtube_id') or meta.get('facebook'))
            and not any(value for key, value in meta.items() if key.endswith('_error')))
