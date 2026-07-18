from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import topics


def _video(title: str, views: int, likes: int) -> dict:
    return {"id": title[:4], "title": title, "views": views, "likes": likes}


class PerformanceExampleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        patcher = patch("src.topics.base_dir", return_value=self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write_analytics(self, videos: list[dict]) -> None:
        path = self.tmp / "data" / "analytics.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"channels": {"histold": {"videos": videos}}}))

    def test_performance_examples_splits_by_like_rate(self) -> None:
        # Arrange: 4% like-rate winner, 0.5% like-rate loser.
        self._write_analytics([
            _video("High like rate", views=200, likes=8),
            _video("Low like rate", views=200, likes=1),
        ])

        # Act
        winners, losers = topics.performance_examples()

        # Assert
        self.assertEqual(["High like rate"], winners)
        self.assertEqual(["Low like rate"], losers)

    def test_performance_examples_ignores_low_view_videos(self) -> None:
        # Arrange: 50% like-rate but only 10 views — statistical noise.
        self._write_analytics([_video("Tiny sample", views=10, likes=5)])

        # Act
        winners, losers = topics.performance_examples()

        # Assert
        self.assertEqual(([], []), (winners, losers))

    def test_performance_examples_returns_empty_without_analytics_file(self) -> None:
        # Arrange: no data/analytics.json written.
        # Act
        winners, losers = topics.performance_examples()

        # Assert
        self.assertEqual(([], []), (winners, losers))


if __name__ == "__main__":
    unittest.main()
