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
