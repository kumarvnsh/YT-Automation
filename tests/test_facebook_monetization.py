import io
import unittest

from src.facebook_monetization import parse_export, merge_records, build_report


HEADER = 'media_id,period_start,period_end,currency,qualified_views,content_earnings,stars_count,stars_earnings\n'


class FacebookMonetizationTests(unittest.TestCase):
    def rows(self, body):
        return parse_export(io.StringIO(HEADER + body))

    def test_earnings_and_stars_stay_separate(self):
        rows = self.rows('123,2026-10-01,2026-10-07,USD,2000,1.23,10,0.10\n')
        report = build_report(rows, [{'facebook_id':'123','category':'strange_geography'}])
        reel = report['reels'][0]
        self.assertEqual(reel['content_earnings'], '1.23')
        self.assertEqual(reel['stars_earnings'], '0.10')
        self.assertEqual(reel['content_earnings_per_1000_qualified_views'], '0.6150')
        self.assertEqual(reel['category'], 'strange_geography')

    def test_missing_values_are_not_zero_or_guessed_stars_revenue(self):
        row = self.rows('123,2026-10-01,2026-10-07,USD,,,10,\n')[0]
        self.assertIsNone(row['qualified_views'])
        self.assertIsNone(row['stars_earnings'])
        self.assertIsNone(build_report([row], [])['reels'][0]['content_earnings_per_1000_qualified_views'])

    def test_zero_views_has_no_rate(self):
        row = self.rows('123,2026-10-01,2026-10-07,USD,0,0,0,0\n')[0]
        self.assertEqual(row['qualified_views'], 0)
        self.assertIsNone(build_report([row], [])['reels'][0]['content_earnings_per_1000_qualified_views'])

    def test_mapping_uses_explicit_export_headers(self):
        data = io.StringIO('Post ID,From,To,Currency,Revenue\n123,2026-10-01,2026-10-07,USD,2.50\n')
        rows = parse_export(data, {'media_id':'Post ID','period_start':'From','period_end':'To','currency':'Currency','content_earnings':'Revenue'})
        self.assertEqual(rows[0]['content_earnings'], '2.50')

    def test_reimport_replaces_same_period(self):
        first = self.rows('123,2026-10-01,2026-10-07,USD,100,1,0,0\n')
        revised = self.rows('123,2026-10-01,2026-10-07,USD,200,2,0,0\n')
        self.assertEqual(merge_records(first, revised), revised)

    def test_overlapping_periods_are_rejected(self):
        first = self.rows('123,2026-10-01,2026-10-07,USD,100,1,0,0\n')
        second = self.rows('123,2026-10-07,2026-10-10,USD,100,1,0,0\n')
        with self.assertRaisesRegex(ValueError, 'overlap'):
            merge_records(first, second)

    def test_bad_dates_fractional_counts_and_nonfinite_money_rejected(self):
        for body in ('123,2026-10-08,2026-10-07,USD,1,1,0,0\n',
                     '123,2026-10-01,2026-10-07,USD,1.5,1,0,0\n',
                     '123,2026-10-01,2026-10-07,USD,1,NaN,0,0\n'):
            with self.assertRaises(ValueError):
                self.rows(body)

    def test_duplicate_rows_do_not_silently_double_earnings(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            self.rows('123,2026-10-01,2026-10-07,USD,1,1,0,0\n' * 2)
