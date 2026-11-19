from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from scripts.claim_histold_publish import reserve
from src.config import Config
from src.editorial import DESIGNS, design
from src.growth import scheduled_format
from src.pipeline import _effective_mode, _step_research


CFG = Config({'growth': {'default_hours_utc': [11, 18, 23], 'daily_publish_cap': 3,
                         'weekly_longform': {'weekday': 6, 'hour_utc': 5}},
              'video': {'long_mode': 'cinematic_2d'}})


def test_slot_stays_claimed_and_daily_cap_includes_manual_shorts():
    now = datetime(2026, 11, 22, 11, 10, tzinfo=timezone.utc)
    hour = '2026-11-22T11'
    claims = reserve(CFG, [], [], now, 'short', 'slot-' + hour, hour)
    with pytest.raises(ValueError, match='already claimed'):
        reserve(CFG, claims, [], now, 'short', 'slot-' + hour, hour)
    with pytest.raises(ValueError, match='missed its original'):
        reserve(CFG, [], [], now.replace(hour=18), 'short', 'slot-' + hour, hour)
    claims = reserve(CFG, claims, [], now, 'short', 'manual-1', None)
    claims = reserve(CFG, claims, [], now, 'short', 'manual-2', None)
    with pytest.raises(ValueError, match='cap reached'):
        reserve(CFG, claims, [], now, 'short', 'manual-3', None)


def test_sunday_longform_is_cinematic_and_uses_a_separate_slot(tmp_path):
    now = datetime(2026, 11, 22, 5, 0, tzinfo=timezone.utc)
    assert scheduled_format(tmp_path, CFG, now)[0] == 'long'
    assert scheduled_format(tmp_path, CFG, now.replace(day=23))[0] is None
    assert scheduled_format(tmp_path, CFG, now.replace(hour=11))[0] == 'short'
    assert _effective_mode(CFG, 'long') == 'cinematic_2d'
    assert _effective_mode(CFG, 'long', {'visual_mode': 'broll'}) == 'broll'
    assert len({design(i)['name'] for i in range(len(DESIGNS))}) == len(DESIGNS)


def test_weekly_research_records_partial_verification_without_blocking(tmp_path):
    unsupported = {'topic': 'A historical story', 'status': 'unsupported'}
    stage = {'fmt': 'long'}
    with patch('src.research.research_topic', return_value=unsupported) as research:
        _step_research(CFG, tmp_path, stage)
    assert stage['research'] == unsupported
    research.assert_called_once()
    assert (tmp_path / 'research.json').exists()


def test_failed_run_without_uploads_has_no_upload_evidence(tmp_path):
    from scripts.claim_histold_publish import release, upload_evidence
    import json
    (tmp_path / 'state.json').write_text(json.dumps({'done': ['research'], 'meta': {}}))
    assert upload_evidence('slot-2026-11-19T11', str(tmp_path), []) == []
    assert upload_evidence('run-1', '', []) == []  # failed before any stage existed
    claims = [{'key': 'slot-2026-11-19T11', 'day': '2026-11-19', 'week': '2026-W47', 'format': 'short'},
              {'key': 'run-2', 'day': '2026-11-19', 'week': '2026-W47', 'format': 'short'}]
    assert [c['key'] for c in release(claims, 'slot-2026-11-19T11')] == ['run-2']


@pytest.mark.parametrize('state,index', [
    ({'youtube_id': 'abc'}, []),
    ({'meta': {'facebook': '123'}}, []),
    ({'done': ['research', 'upload']}, []),
    ({}, [{'publish_claim': 'run-9', 'video_id': 'v1'}]),
])
def test_any_sign_of_publishing_keeps_the_claim(tmp_path, state, index):
    from scripts.claim_histold_publish import upload_evidence
    import json
    (tmp_path / 'state.json').write_text(json.dumps(state))
    assert upload_evidence('run-9', str(tmp_path), index)


def test_meta_errors_alone_do_not_count_as_published(tmp_path):
    from scripts.claim_histold_publish import upload_evidence
    import json
    (tmp_path / 'state.json').write_text(json.dumps({'meta': {'facebook_error': 'token expired'}}))
    assert upload_evidence('run-9', str(tmp_path), []) == []


def test_configured_cap_has_headroom_for_reruns():
    from src.config import load_config
    assert int(load_config().get('growth.daily_publish_cap')) == 6
