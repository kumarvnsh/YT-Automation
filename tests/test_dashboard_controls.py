from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class DashboardControlTests(unittest.TestCase):
    def test_refresh_analytics_button_exists(self) -> None:
        html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="btnRefreshAnalytics"', html)

    def test_refresh_analytics_dispatches_the_analytics_workflow(self) -> None:
        app_js = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
        self.assertIn('getElementById("btnRefreshAnalytics")', app_js)
        self.assertIn('dispatchWorkflow(s, "analytics.yml", {})', app_js)

    def test_analytics_workflow_supports_manual_dispatch(self) -> None:
        workflow = (ROOT / ".github/workflows/analytics.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch", workflow)


if __name__ == "__main__":
    unittest.main()
