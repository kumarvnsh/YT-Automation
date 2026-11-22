import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from src.operations import health_alerts, snapshot, observed_change, audience_summary, weekly_review, daily_briefing, alert_message
from src import telegram_control
from src.operations import shotsnip_lines


class OperationsTests(unittest.TestCase):
    def test_country_basis_remains_separate_from_editorial_region(self):
        result = audience_summary({'platforms': {'YouTube': {'basis': 'views', 'countries': {'US': 10, 'GB': 5, 'IN': 15}},
                                                 'Facebook': {'basis': 'Page likes', 'countries': {'IN': 100}}}})
        self.assertEqual(result['YouTube']['usa_europe_share_of_reported'], 0.5)
        self.assertEqual(result['Facebook']['usa_europe_share_of_reported'], 0)

    def test_cancelled_publish_is_not_a_failure_alert(self):
        runs = [{'workflowName': 'Publish', 'status': 'completed', 'conclusion': 'cancelled', 'databaseId': 1}]
        self.assertFalse(any(a['id'].startswith('run:') for a in health_alerts({}, runs)))
        runs[0]['conclusion'] = 'failure'
        self.assertTrue(any(a['id'] == 'run:1' for a in health_alerts({}, runs)))

    def test_comparison_excludes_new_and_missing_videos(self):
        now = datetime.now(timezone.utc)
        old = snapshot({'videos': [{'yt_id': 'a', 'yt': 10}]}, now)
        new = snapshot({'videos': [{'yt_id': 'a', 'yt': 14}, {'yt_id': 'b', 'yt': 999}]}, now)
        self.assertEqual(observed_change(new, old)['yt'], {'videos_compared': 1, 'observed_view_change': 4})

    def test_telegram_update_is_sent_once_even_after_a_failure(self):
        sent = []
        with tempfile.TemporaryDirectory() as tmp, patch.object(telegram_control, 'notify',
                                                                side_effect=lambda text, *a: sent.append(text) or 'failed: Timeout'):
            path = Path(tmp)/'state.json'
            self.assertEqual(telegram_control.notify_once('briefing', 'daily:2026-11-17', path), 'failed: Timeout')
            self.assertEqual(telegram_control.notify_once('changed briefing', 'daily:2026-11-17', path), 'failed: Timeout')
            self.assertEqual(sent, ['briefing'])

    def test_unpaired_bot_retries_later(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(telegram_control, 'notify', return_value='not_configured') as notify:
            path = Path(tmp)/'state.json'
            telegram_control.notify_once('briefing', 'daily', path)
            telegram_control.notify_once('briefing', 'daily', path)
            self.assertEqual(notify.call_count, 2)

    def test_shotsnip_section_reads_live_state_and_today(self):
        now = datetime(2026, 11, 22, 4, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'work/dashboard').mkdir(parents=True)
            (root/'scheduler.json').write_text(json.dumps({'enabled': False, 'live_operations_batch_hold': {'until': 'x'}}))
            (root/'work/dashboard/analytics.json').write_text(json.dumps({'subscribers': 16, 'today_ist': '2026-11-22', 'today_upload_views': 42,
                'videos': [{'published_at': '2026-11-21T20:00:00+00:00'}, {'published_at': '2026-11-20T20:00:00+00:00'}]}))
            text = '\n'.join(shotsnip_lines(root, now))
        self.assertIn('Live clipping: paused (batch hold active)', text)
        self.assertIn('Uploaded today: 1 Short(s)', text)
        self.assertIn("Views on today's uploads so far: 42", text)
        self.assertIn('Subscribers: 16', text)


    def test_briefing_separates_accumulated_views_and_local_publish_slots(self):
        now = datetime(2026, 11, 18, 6, 45, tzinfo=timezone.utc)
        text = daily_briefing({'hours': [11, 18, 23], 'videos': [
            {'at': '2026-11-17T23:00:00Z', 'yt': 12, 'fb': 100},
            {'at': '2026-11-17T12:00:00Z', 'yt': 8, 'fb': None}]}, [], now)
        self.assertIn('18 Nov, 04:30 PM IST', text)
        self.assertIn('Published today: 1 tracked video(s)', text)
        self.assertIn('YouTube: 20 views (2 measured videos)', text)
        self.assertIn('Instagram: unavailable', text)
        self.assertIn('not views earned today', text)
        self.assertIn('NEXT ACTION', text)
        self.assertNotIn('*', text)  # Telegram messages are plain text
        self.assertNotIn('Local AI hypothesis', text)

    def test_error_takes_priority_over_a_suggested_experiment(self):
        now = datetime(2026, 11, 18, tzinfo=timezone.utc)
        alert = {'severity': 'error', 'message': 'Upload failed'}
        text = daily_briefing({'ai_insights': {'interpretation': {'next_test': 'Try objects'}}}, [alert], now)
        self.assertIn('Check the issues above', text)
        self.assertNotIn('Try objects', text)
        self.assertIn('Issue needing attention', alert_message(alert, now))


if __name__ == '__main__':
    unittest.main()
