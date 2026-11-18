import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from src.operations import health_alerts, snapshot, observed_change, audience_summary, weekly_review, daily_briefing, alert_message
from src.whatsapp_updates import send_update


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

    @patch('requests.post')
    def test_message_timeout_is_not_retried_automatically(self, post):
        import requests
        post.side_effect = requests.Timeout()
        config = {'enabled': True, 'base_url': 'http://127.0.0.1:2785', 'session_id': 'jarvis',
                  'recipient': '15551234567', 'api_key': 'private'}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'state.json'
            self.assertEqual(send_update(config, 'briefing', 'daily:2026-11-17', path)['status'], 'outcome_unknown')
            send_update(config, 'changed briefing', 'daily:2026-11-17', path)
            self.assertEqual(post.call_count, 1)

    @patch('requests.post')
    def test_disabled_delivery_never_sends(self, post):
        self.assertEqual(send_update({}, 'test', 'daily', 'unused')['status'], 'disabled')
        post.assert_not_called()


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
        self.assertIn('*NEXT ACTION*', text)
        self.assertNotIn('Local AI hypothesis', text)

    def test_error_takes_priority_over_a_suggested_experiment(self):
        now = datetime(2026, 11, 18, tzinfo=timezone.utc)
        alert = {'severity': 'error', 'message': 'Upload failed'}
        text = daily_briefing({'ai_insights': {'interpretation': {'next_test': 'Try objects'}}}, [alert], now)
        self.assertIn('Check the issues above', text)
        self.assertNotIn('Try objects', text)
        self.assertIn('*Issue needing attention*', alert_message(alert, now))


if __name__ == '__main__':
    unittest.main()
