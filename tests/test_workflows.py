from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class WorkflowRegressionTests(unittest.TestCase):
    def test_astrotold_publish_workflow_is_channel_isolated(self) -> None:
        workflow = (ROOT / ".github/workflows/publish-astrotold.yml").read_text()
        required_fragments = (
            "channels/astrotold/config.yaml",
            "ASTROTOLD_YT_CLIENT_SECRET_JSON_B64",
            "ASTROTOLD_YT_TOKEN_JSON_B64",
            "channels/astrotold/secrets",
            "CHANNEL_LABEL=astrotold",
            "channels/astrotold/data/topic_reservations.json",
            "export PUBLISH_SLOT=morning",
            "export PUBLISH_SLOT=evening",
        )
        for fragment in required_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, workflow)

    def test_astrotold_publish_guard_blocks_duplicate_scheduled_uploads(self) -> None:
        workflow = (ROOT / ".github/workflows/publish-astrotold.yml").read_text()
        required_fragments = (
            "actions: write",
            "name: Duplicate-slot guard",
            "if: github.event.inputs.scheduled == 'true'",
            "workflows/publish-astrotold.yml/runs?per_page=20",
            'TZ=Asia/Kolkata date +%F',
            'Path("channels/astrotold/data/published_index.json")',
            'entry.get("channel", "astrotold") != "astrotold"',
            'if [ "$COUNT" -ge 2 ]',
            'gh run cancel "$GITHUB_RUN_ID"',
        )
        for fragment in required_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, workflow)

    def test_astrotold_publish_merges_state_before_commit_retries(self) -> None:
        workflow = (ROOT / ".github/workflows/publish-astrotold.yml").read_text()
        required_fragments = (
            "for attempt in 1 2 3",
            "git fetch origin master",
            "python scripts/merge_json_state.py --ref origin/master",
            "git reset --mixed origin/master",
            "channels/astrotold/data/used_topics.json",
            "channels/astrotold/data/published_index.json",
            "channels/astrotold/data/topic_reservations.json",
            "git push origin HEAD:master",
        )
        for fragment in required_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, workflow)

    def test_publish_preserves_stage_until_artifact_upload(self) -> None:
        workflow = (ROOT / ".github/workflows/publish.yml").read_text()
        self.assertIn('PRESERVE_STAGE_ARTIFACT: "true"', workflow)

    def test_publish_exports_ist_slot_before_pipeline_loop(self) -> None:
        workflow = (ROOT / ".github/workflows/publish.yml").read_text()
        self.assertIn("export PUBLISH_SLOT=morning", workflow)
        self.assertIn("export PUBLISH_SLOT=evening", workflow)
        self.assertIn('HOUR_IST=$(TZ=Asia/Kolkata date +%H)', workflow)

    def test_publish_commits_topic_reservations(self) -> None:
        workflow = (ROOT / ".github/workflows/publish.yml").read_text()
        self.assertIn("data/topic_reservations.json", workflow)

    def test_dashboard_deploys_on_docs_changes(self) -> None:
        workflow = (ROOT / ".github/workflows/deploy-pages.yml").read_text()
        self.assertIn('paths: ["docs/**"]', workflow)
        self.assertIn("workflow_dispatch", workflow)
        self.assertIn("actions/upload-pages-artifact", workflow)
        self.assertIn("actions/deploy-pages", workflow)
        self.assertIn("path: docs", workflow)

    def test_approval_index_points_to_downloaded_source_artifact(self) -> None:
        workflow = (ROOT / ".github/workflows/approve.yml").read_text()
        self.assertIn("PUBLISH_ARTIFACT_RUN_ID: ${{ inputs.source_run_id }}", workflow)

    def test_analytics_only_stages_optional_files_when_they_exist(self) -> None:
        workflow = (ROOT / ".github/workflows/analytics.yml").read_text()
        self.assertNotIn(
            "git add data/analytics.json data/trends.json data/published_index.json data/meta_analytics.json",
            workflow,
        )
        self.assertIn('[ -f "$file" ] && git add "$file"', workflow)

    def test_meta_failure_is_reported_after_other_exports_are_committed(self) -> None:
        workflow = (ROOT / ".github/workflows/analytics.yml").read_text()
        self.assertIn("id: meta_analytics", workflow)
        self.assertIn("continue-on-error: true", workflow)
        self.assertIn(
            'if [ "${{ steps.meta_analytics.outcome }}" = "success" ]',
            workflow,
        )
        self.assertIn("if: steps.meta_analytics.outcome == 'failure'", workflow)

    def test_playlist_workflow_sorts_video_into_selected_playlist(self) -> None:
        workflow = (ROOT / ".github/workflows/playlist.yml").read_text()
        self.assertIn("video_id:", workflow)
        self.assertIn("playlist_id:", workflow)
        self.assertIn("playlist_title:", workflow)
        self.assertIn("python scripts/playlist_sort.py", workflow)
        self.assertIn("--playlist-title", workflow)
        self.assertIn("data/published_index.json", workflow)


if __name__ == "__main__":
    unittest.main()
