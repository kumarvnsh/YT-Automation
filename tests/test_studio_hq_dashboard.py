"""Read-model checks for the dashboard's allowlisted payload."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from studio_hq.archive import atomic_json, digest
from studio_hq.dashboard_snapshot import snapshot


class DashboardSnapshotTests(unittest.TestCase):
    def test_snapshot_excludes_astrotold_and_private_source_fields(self):
        with TemporaryDirectory() as folder:
            state = Path(folder)/'state'
            state.mkdir()
            roots = {name: Path(folder)/name for name in ('histold', 'astrotold', 'shotsnip')}
            for channel in ('histold', 'astrotold'):
                atomic_json(roots[channel]/'data/published_index.json', [{
                    'channel': channel, 'title': f'{channel.title()} story https://outside.invalid/private?token=secret',
                    'video_id': 'videoID1234', 'published_at': '2026-10-01T00:00:00Z',
                    'refresh_token': 'secret',
                }])
            video = roots['shotsnip']/'work/scheduler/one/finished-short.mp4'
            video.parent.mkdir(parents=True)
            ident = 'a'*24
            manifest = state/'manifests'/f'{ident}.json'
            atomic_json(manifest, {'channel': 'shotsnip', 'local_dir': str(video.parent),
                                   'status': 'verified_local_retained',
                                   'files': [{'name': video.name, 'size': 40,
                                              'drive_file_id': 'abcdefghijklmnop'}]})
            atomic_json(state/'archive-receipts'/manifest.name, {'manifest_sha256': digest(manifest)})
            atomic_json(state/'manifests'/('b'*24 + '.json'), {
                'channel': 'astrotold', 'status': 'verified_local_retained',
                'local_dir': str(roots['astrotold']), 'files': [{'name': 'story.mp4', 'size': 99}],
            })
            atomic_json(state/'last-finished-cleanup-tick.json', {
                'status': 'removed', 'removed': 1, 'bytes_removed': 40,
                'channel': 'shotsnip', 'original': str(video),
            })
            exported = snapshot({'state_dir': state, 'roots': roots})
            self.assertEqual({x['name'] for x in exported['channels']}, {'Histold', 'Shotsnip'})
            self.assertEqual({r['title'] for r in exported['activity']}, {'Histold story'})
            self.assertEqual({r['channel'] for r in exported['archives']}, {'shotsnip'})
            self.assertEqual(set(exported['analytics']), {'histold', 'shotsnip'})
            self.assertEqual(exported['archives'][0]['state'], 'Drive only')
            self.assertTrue(exported['archives'][0]['url'].startswith('https://drive.google.com/'))
            payload = json.dumps(exported)
            for private in ('Astrotold', 'astrotold', 'secret', 'outside.invalid', 'original', 'local_dir', 'refresh_token'):
                self.assertNotIn(private, payload)

    def test_platform_metrics_keep_report_gaps_and_split_lifetime_definitions(self):
        with TemporaryDirectory() as folder:
            roots = {name: Path(folder)/name for name in ('histold', 'shotsnip')}
            location = roots['shotsnip']/'work/dashboard'
            atomic_json(location/'analytics.json', {
                'videos': [{'id': 'videoID1234', 'views': 200}],
                'owner_analytics': {'status': 'connected', 'start': '2026-10-01', 'end': '2026-10-03',
                                    'daily': [{'day': '2026-10-01', 'views': 0, 'estimatedMinutesWatched': 0},
                                              {'day': '2026-10-03', 'views': 10, 'estimatedMinutesWatched': 60}],
                                    'videos': [{'video': 'videoID1234', 'views': 10}]},
            })
            atomic_json(roots['histold']/'data/meta_analytics.json', {'facebook': {'videos': [
                {'id': 'a', 'views_metric': 'fb_reels_total_plays', 'views': 20,
                 'url': 'https://www.facebook.com/reel/12?access_token=secret'},
                {'id': 'b', 'views_metric': 'legacy_video_views', 'views': 50},
                {'id': 'b', 'views_metric': 'legacy_video_views', 'views': 50},
                {'id': 'c', 'views_metric': 'legacy_video_views', 'views': None},
            ]}})
            exported = snapshot({'state_dir': folder, 'roots': roots})['analytics']
            daily, lifetime = exported['shotsnip']
            self.assertEqual([p['value'] for p in daily['points']], [0, None, 10])
            self.assertEqual([m['value'] for m in daily['metrics']], [10, 1, None])
            self.assertEqual(lifetime['metrics'][0]['value'], 200)
            reels, legacy = exported['histold']
            self.assertEqual([reels['metrics'][0]['value'], legacy['metrics'][0]['value']], [20, 50])
            self.assertEqual(reels['videos'][0]['url'], 'https://www.facebook.com/reel/12')

    def test_pacing_keeps_unobserved_medians_unavailable(self):
        with TemporaryDirectory() as folder:
            roots = {name: Path(folder)/name for name in ('histold', 'shotsnip')}
            atomic_json(roots['shotsnip']/'work/dashboard/growth-experiment.json', {
                'published': 1, 'target_clips': 24, 'checkpoints': {'youtube': {
                    'tight_20s': {'24': {'eligible': 1, 'measured': 1, 'median': 0}},
                    'context_30s': {'24': {'eligible': 0, 'measured': 0, 'median': None}},
                }},
            })
            pacing = snapshot({'state_dir': folder, 'roots': roots})['pacing_experiment']
            self.assertEqual((pacing['published'], pacing['target']), (1, 24))
            self.assertEqual([group['median'] for group in pacing['groups']], [0, None])
