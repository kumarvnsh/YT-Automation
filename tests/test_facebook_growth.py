import io
import unittest
from src.facebook_growth import parse_insights, capture_manual, build_report

HEADER = 'media_id,observed_at,period_start,period_end,views,shares,followers_gained,average_watch_time_seconds,three_second_retention,completion_rate\n'
class FacebookGrowthTests(unittest.TestCase):
    def test_null_and_period_validation(self):
        rows = parse_insights(io.StringIO(HEADER + '123,2026-10-02T00:00:00Z,2026-10-01T00:00:00Z,2026-10-02T00:00:00Z,100,2,,5,.7,\n'))
        self.assertIsNone(rows[0]['followers_gained'])
        with self.assertRaises(ValueError):
            parse_insights(io.StringIO(HEADER + '123,2026-10-02,2026-10-01,2026-10-02,100,2,,5,1.1,\n'))
    def test_manual_snapshots_are_immutable_and_lifetime_only(self):
        posts = [{'facebook_id':'123','experiment':'facebook-world-history-4w','published_at':'2026-10-01T00:00:00Z'}]
        manifest = {'id':'facebook-world-history-4w','observation_hours':[24,72,168]}
        rows = parse_insights(io.StringIO(HEADER + '123,2026-10-02T00:00:00Z,2026-10-01T00:00:00Z,2026-10-02T00:00:00Z,100,2,1,5,.7,.2\n'))
        snaps = capture_manual(posts, manifest, rows, [])
        self.assertEqual(snaps[0]['metrics']['shares']['value'],2)
        self.assertIsNone(snaps[0]['metrics']['shares_per_1000_views']['value'])
        rows[0]['shares'] = 10
        self.assertEqual(capture_manual(posts, manifest, rows, snaps),snaps)
        rows[0]['period_start'] = '2026-10-01T01:00:00+00:00'
        self.assertEqual(capture_manual(posts, manifest, rows, []),[])
    def test_report_excludes_late_and_other_platforms(self):
        metric = {'shares':{'value':2},'views':{'value':100},'followers_gained':{'value':None}}
        snap = {'source':'manual_facebook_dashboard','view_definition':'Dashboard Reel views (includes replays)','platform':'facebook','experiment_id':'facebook-world-history-4w','media_id':'123','target_age_hours':24,'actual_age_hours':24,'late':False,'metrics':metric}
        report = build_report([{'facebook_id':'123','editorial_region':'Asia'}],[snap,{**snap,'late':True},{**snap,'platform':'youtube'}],[])
        group = report['comparisons'][0]
        self.assertEqual(group['sample_size'],1)
        self.assertEqual(group['shares_per_1000_views'],20)
        self.assertIsNone(group['followers_per_1000_views'])

class NativeFacebookExportTests(unittest.TestCase):
    def test_native_lifetime_is_not_a_period_or_snapshot(self):
        from src.facebook_growth import parse_native_export, native_baseline
        source = io.StringIO('Post ID,Post type,Date,Publish time,Title,Duration (sec),Views,Shares,Net follows,Average Seconds viewed,Seconds viewed,Permalink\n123,Reel,Lifetime,09/23/2026 21:05,Example,45,100,2,-1,6,600,https://www.facebook.com/reel/999/\n')
        rows = parse_native_export(source)
        self.assertEqual(rows[0]['net_follows'],-1)
        self.assertEqual(rows[0]['reel_id'],'999')
        report = native_baseline(rows)
        self.assertIsNone(report['observed_at'])
        self.assertEqual(report['totals']['net_follows'],-1)
        self.assertNotIn('followers_per_1000_views',report['totals'])
        self.assertEqual(report['duration_groups'][0]['shares_per_1000_views'],20)

class SnapshotSourceTrustTests(unittest.TestCase):
    def test_api_null_snapshot_does_not_block_manual_source(self):
        from src.facebook_growth import EXPERIMENT
        posts = [{'facebook_id':'123','experiment':EXPERIMENT,'published_at':'2026-10-01T00:00:00Z'}]
        manifest = {'id':EXPERIMENT,'observation_hours':[24,72,168]}
        api = {'platform':'facebook','media_id':'123','target_age_hours':24,'metrics':{}}
        rows = parse_insights(io.StringIO(HEADER + '123,2026-10-02T00:00:00Z,2026-10-01T00:00:00Z,2026-10-02T00:00:00Z,100,2,1,5,.7,.2\n'))
        result = capture_manual(posts,manifest,rows,[api])
        self.assertEqual(len(result),2)
        self.assertEqual(result[0],api)
        self.assertEqual(capture_manual(posts,manifest,rows,result),result)

    def test_sources_and_view_definitions_never_pool(self):
        from src.facebook_growth import EXPERIMENT
        base = {'platform':'facebook','experiment_id':EXPERIMENT,'media_id':'123','target_age_hours':24,'actual_age_hours':24,'late':False,
                'metrics':{'views':{'value':100},'shares':{'value':2}}}
        manual = {**base,'source':'manual_facebook_dashboard','view_definition':'Dashboard Reel views (includes replays)'}
        other = {**manual,'media_id':'456','view_definition':'Unique viewers'}
        report = build_report([], [base,manual,other],[])
        self.assertEqual(len(report['comparisons']),3)
        api_group = next(row for row in report['comparisons'] if row['source']=='meta_api_unverified_views')
        self.assertIsNone(api_group['shares_per_1000_views'])
        self.assertTrue(all(row['sample_size']==1 for row in report['comparisons']))
