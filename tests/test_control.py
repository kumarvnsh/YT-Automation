"""Dashboard boundaries: exact schedules, explicit intent, and controlled writes."""
import copy
import unittest
from datetime import datetime, timezone

from src import control
from src.config import Config
from src.growth import PLAN
from src.strategy import OVERRIDES


def config():
    return Config({'growth': {'posts_per_day': 5, 'allowed_hours_utc': list(range(24)), 'min_gap_hours': 2},
                   'editorial': {'series': [{'id': 'unsolved'}, {'id': 'almost'}],
                                 'regions': [{'id': 'europe'}, {'id': 'americas'}],
                                 'duration_variants': [{'id': 'concise'}, {'id': 'story'}]}})


class MemoryGitHub:
    def __init__(self):
        self.files = {}
        self.dispatched = []

    def update_json(self, path, change, message):
        self.files[path] = change(copy.deepcopy(self.files.get(path, {})))

    def run_workflow(self, workflow):
        self.dispatched.append(workflow)


class ControlTests(unittest.TestCase):
    def test_schedule_cannot_change_volume_or_truncate_times(self):
        for hours in ([0, 2, 12, 16, 18, 22], [0.5, 2, 12, 16, 18], [False, 2, 12, 16, 18],
                      [0, 0, 12, 16, 18], [0, 1, 12, 16, 18]):
            with self.subTest(hours=hours), self.assertRaises(ValueError):
                control.validate('pin_hours', {'hours': hours}, config())

    def test_unsupported_actions_and_nonfinite_boosts_fail(self):
        for name, args in [('publish', {}), ('unpin_hours', {'posts_per_day': 10}),
                           ('boost_option', {'dimension': 'region', 'option': 'europe', 'factor': float('nan')}),
                           ('boost_option', {'dimension': 'region', 'option': 'europe', 'factor': 8})]:
            with self.subTest(name=name, args=args), self.assertRaises(ValueError):
                control.validate(name, args, config())

    def test_pausing_every_option_does_not_silently_reset(self):
        gh = MemoryGitHub()
        control.execute('pause_option', {'dimension': 'series', 'option': 'unsolved'}, config(), gh)
        with self.assertRaisesRegex(ValueError, 'at least one'):
            control.execute('pause_option', {'dimension': 'series', 'option': 'almost'}, config(), gh)
        self.assertEqual(gh.files[OVERRIDES]['series']['paused'], ['unsolved'])

    def test_boost_reset_pin_and_unpin_write_only_expected_files(self):
        gh = MemoryGitHub()
        args = {'dimension': 'region', 'option': 'europe', 'factor': 1.5}
        control.execute('boost_option', args, config(), gh)
        self.assertEqual(gh.files[OVERRIDES]['region']['boost'], {'europe': 1.5})
        control.execute('reset_option', {'dimension': 'region', 'option': 'europe'}, config(), gh)
        self.assertEqual(gh.files[OVERRIDES]['region']['boost'], {})
        now = datetime(2026, 11, 16, tzinfo=timezone.utc)
        control.execute('pin_hours', {'hours': [0, 12, 16, 18, 22], 'days': 7}, config(), gh, now)
        self.assertEqual(gh.files[PLAN]['pinned_until'], '2026-11-23T00:00:00+00:00')
        control.execute('unpin_hours', {}, config(), gh, now)
        self.assertIsNone(gh.files[PLAN]['pinned_until'])
        control.execute('refresh_analytics', {}, config(), gh)
        self.assertEqual(gh.dispatched, ['analytics.yml'])
        self.assertEqual(set(gh.files), {PLAN, OVERRIDES})

    def test_proposals_are_grounded_in_explicit_request(self):
        self.assertTrue(control.requested('pause_option', {'option': 'unsolved'}, 'Pause unsolved please'))
        self.assertFalse(control.requested('boost_option', {'dimension': 'region', 'option': 'americas'},
                                           'Which region is best and pause unsolved please'))
        self.assertFalse(control.requested('pause_option', {'option': 'unsolved'}, "Don't pause unsolved"))
        self.assertFalse(control.requested('pin_hours', {}, 'Make ten videos a day'))

    def test_ist_conversion_is_exact_including_midnight(self):
        self.assertEqual(control.explicit_ist_hours('Pin IST hours 05:30, 17:30, 21:30, 23:30, 03:30'),
                         [0, 12, 16, 18, 22])
        with self.assertRaises(ValueError):
            control.explicit_ist_hours('Pin 05:00 IST')
        self.assertIsNone(control.explicit_ist_hours('Pin 0,12,16,18,22 UTC'))

    def test_natural_commands_and_clause_scoped_negation(self):
        self.assertTrue(control.requested('pause_option', {'dimension':'series','option':'unsolved'}, 'Avoid unsolved stories'))
        self.assertTrue(control.requested('boost_option', {'dimension':'region','option':'europe'}, 'Focus on European history'))
        self.assertTrue(control.requested('boost_option', {'dimension':'region','option':'europe'},
                                          "Don't change the schedule; boost Europe instead"))
        self.assertFalse(control.timing_requested("Don't change the schedule; boost Europe instead"))
        self.assertTrue(control.analysis_requested('Analyse again and change publish timings'))
        self.assertTrue(control.timing_requested('Analyse again and change publish timings'))


if __name__ == '__main__':
    unittest.main()
