from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class WorkflowRegressionTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
