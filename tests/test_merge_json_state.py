from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.merge_json_state import IDENTITY_KEYS, merge_lists  # noqa: E402


class MergeListsTests(unittest.TestCase):
    def test_remote_only_entries_are_kept_and_local_only_appended(self) -> None:
        base = [{"job_id": "a"}, {"job_id": "b"}]
        local = [{"job_id": "a"}, {"job_id": "c"}]

        merged = merge_lists(base, local, ("job_id",))

        self.assertEqual(["a", "b", "c"], [e["job_id"] for e in merged])

    def test_local_mutations_win_for_shared_entries(self) -> None:
        base = [{"video_id": "v1", "retitled_at": None}]
        local = [{"video_id": "v1", "retitled_at": "2026-07-11T10:00:00Z"}]

        merged = merge_lists(base, local, ("video_id",))

        self.assertEqual("2026-07-11T10:00:00Z", merged[0]["retitled_at"])

    def test_compound_identity_keys(self) -> None:
        base = [{"title": "Rome", "date": "2026-07-10"}]
        local = [{"title": "Rome", "date": "2026-07-11"}]

        merged = merge_lists(base, local, ("title", "date"))

        self.assertEqual(2, len(merged))

    def test_concurrent_appends_both_survive(self) -> None:
        # The exact race that broke the publish run: two runs each appended
        # their own reservation on top of the same ancestor.
        ancestor = [{"job_id": "old"}]
        remote = ancestor + [{"job_id": "run-a"}]
        local = ancestor + [{"job_id": "run-b"}]

        merged = merge_lists(remote, local, ("job_id",))

        self.assertEqual(["old", "run-a", "run-b"], [e["job_id"] for e in merged])

    def test_facebook_only_posts_remain_distinct(self):
        first = {'video_id': None, 'facebook_id': 'fb1', 'stage_dir_name': 'stage1'}
        second = {'video_id': None, 'facebook_id': 'fb2', 'stage_dir_name': 'stage2'}
        self.assertEqual(merge_lists([first], [first, second], ('video_id',)), [first, second])

    def test_same_stage_enrichment_preserves_platform_ids_in_both_merge_directions(self):
        old = {'video_id': None, 'facebook_id': 'fb1', 'stage_dir_name': 'stage1', 'title': 'old'}
        enriched = {**old, 'video_id': 'yt1', 'instagram_id': 'ig1'}
        for remote, local in ((old, enriched), (enriched, old)):
            merged = merge_lists([remote], [local], ('video_id',))
            self.assertEqual(len(merged), 1)
            self.assertEqual(merged[0]['video_id'], 'yt1')
            self.assertEqual(merged[0]['instagram_id'], 'ig1')

    def test_legacy_facebook_fallback_and_unidentified_rows_are_preserved(self):
        first = {'facebook_id': 'fb1'}
        second = {'facebook_id': 'fb2'}
        self.assertEqual(merge_lists([first], [first, second], ('video_id',)), [first, second])
        self.assertEqual(merge_lists([{}], [{}], ('video_id',)), [{}, {}])

    def test_astrotold_channel_state_uses_the_same_identity_rules(self) -> None:
        self.assertEqual(
            ("job_id",),
            IDENTITY_KEYS["channels/astrotold/data/topic_reservations.json"],
        )
        self.assertEqual(
            ("title", "date"),
            IDENTITY_KEYS["channels/astrotold/data/used_topics.json"],
        )
        self.assertEqual(
            ("video_id",),
            IDENTITY_KEYS["channels/astrotold/data/published_index.json"],
        )


class WorkflowContractTests(unittest.TestCase):
    def test_publish_commit_step_merges_instead_of_rebasing(self) -> None:
        workflow = (ROOT / ".github/workflows/publish.yml").read_text()
        self.assertIn("merge_json_state.py", workflow)
        self.assertIn("git reset --mixed origin/master", workflow)
        self.assertNotIn("git pull --rebase --autostash", workflow)


if __name__ == "__main__":
    unittest.main()
