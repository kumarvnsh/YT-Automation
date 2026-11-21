"""Draft previews: only allowlisted files are kept, and details are read back safely."""
import io
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
import zipfile

from studio_hq import media

STAGE = '20261113_160219_981654_short'


def artifact_zip(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as bundle:
        for name, body in files.items():
            bundle.writestr(name, body)
    return buffer.getvalue()


class DraftPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = {'dashboard_private_snapshot': str(self.root/'snapshot.json'), 'github_repo': 'owner/repo', 'github_cli': 'gh'}

    def github(self, files, expired=False, size=1000):
        def run(args, **kwargs):
            if args[-1].endswith('/artifacts'):
                listing = {'artifacts': [{'id': 7, 'name': 'run-' + STAGE, 'expired': expired, 'size_in_bytes': size}]}
                return subprocess.CompletedProcess(args, 0, json.dumps(listing).encode(), b'')
            return subprocess.CompletedProcess(args, 0, artifact_zip(files), b'')
        return run

    def test_preview_keeps_only_allowlisted_files_and_reads_details(self):
        (self.root/'drafts/20261101_000000_short').mkdir(parents=True)  # a stale preview gets cleaned up
        files = {'video.mp4': b'mp4', 'metadata.json': json.dumps({'title': 'Ice', 'tags': ['desert', 3], 'privacy_status': 'private'}),
                 'script.json': json.dumps({'segments': [{'narration': 'Line one'}, {'narration': ''}]}),
                 'quality.json': json.dumps({'passed': False, 'score': 95, 'errors': ['Claim C3 lacks support']}),
                 'thumbnail.jpg': b'jpg', 'secret.env': 'TOKEN=1', 'nested/../../escape.mp4': b'x'}
        body = {'stage': STAGE, 'run_id': '43458259520', '_pending': [{'stage': STAGE}]}
        self.assertIn('Preview ready', media.prepare_draft(self.config, body, run=self.github(files)))
        kept = sorted(path.name for path in (self.root/'drafts'/STAGE).iterdir())
        self.assertEqual(kept, ['metadata.json', 'quality.json', 'script.json', 'thumbnail.jpg', 'video.mp4'])
        self.assertFalse((self.root/'drafts/20261101_000000_short').exists())
        index = {}
        preview = media.draft_preview(self.config, STAGE, index)
        self.assertEqual((preview['title'], preview['tags'], preview['narration']), ('Ice', ['desert'], ['Line one']))
        self.assertEqual(preview['quality']['errors'], ['Claim C3 lacks support'])
        self.assertEqual(set(index), {preview['video'], preview['thumbnail']})
        self.assertTrue(all(Path(path).is_relative_to((self.root/'drafts').resolve()) for path in index.values()))

    def test_expired_or_videoless_drafts_are_refused(self):
        body = {'stage': STAGE, 'run_id': '1234567', '_pending': []}
        with self.assertRaisesRegex(ValueError, 'expired'):
            media.prepare_draft(self.config, body, run=self.github({'video.mp4': b'x'}, expired=True))
        with self.assertRaisesRegex(ValueError, 'no rendered video'):
            media.prepare_draft(self.config, body, run=self.github({'metadata.json': '{}'}))
        self.assertIsNone(media.draft_preview(self.config, STAGE, {}))


if __name__ == '__main__':
    unittest.main()
