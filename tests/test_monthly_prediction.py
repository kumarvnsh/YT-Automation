from __future__ import annotations

import unittest
from datetime import date

from src import topics


class _Cfg:
    """Minimal config stub: only answers the monthly_prediction flag."""

    def __init__(self, flag):
        self._flag = flag

    def get(self, path, default=None):
        if path == "channel.monthly_prediction":
            return self._flag
        return default


class MonthlyPredictionTests(unittest.TestCase):
    def test_first_window_day_is_bn1(self):
        # July has 31 days; window is Jul 23-31, so Jul 23 -> BN1.
        d = topics.monthly_prediction_direction(_Cfg(True), today=date(2026, 7, 23), slot="morning")
        self.assertIsNotNone(d)
        self.assertIn("birth number 1", d)
        self.assertIn("August", d)

    def test_last_day_is_bn9(self):
        d = topics.monthly_prediction_direction(_Cfg(True), today=date(2026, 7, 31), slot="morning")
        self.assertIn("birth number 9", d)
        self.assertIn("August", d)

    def test_thirty_day_month_last_day_is_bn9(self):
        # June has 30 days; window is Jun 22-30, so Jun 30 -> BN9.
        d = topics.monthly_prediction_direction(_Cfg(True), today=date(2026, 6, 30), slot="morning")
        self.assertIn("birth number 9", d)
        self.assertIn("July", d)

    def test_december_rolls_to_january_next_year(self):
        d = topics.monthly_prediction_direction(_Cfg(True), today=date(2026, 12, 31), slot="morning")
        self.assertIn("birth number 9", d)
        self.assertIn("January 2027", d)

    def test_mid_month_returns_none(self):
        d = topics.monthly_prediction_direction(_Cfg(True), today=date(2026, 7, 15), slot="morning")
        self.assertIsNone(d)

    def test_evening_slot_returns_none(self):
        d = topics.monthly_prediction_direction(_Cfg(True), today=date(2026, 7, 31), slot="evening")
        self.assertIsNone(d)

    def test_flag_off_returns_none(self):
        d = topics.monthly_prediction_direction(_Cfg(False), today=date(2026, 7, 31), slot="morning")
        self.assertIsNone(d)


if __name__ == "__main__":
    unittest.main()
