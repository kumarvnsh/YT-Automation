from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import topics


class _Cfg:
    """Minimal stand-in for Config: only .get(key, default) is used here."""

    def __init__(self, values: dict) -> None:
        self._values = values

    def get(self, key: str, default=None):
        return self._values.get(key, default)


class SeriesCadenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        patcher = patch("src.topics.base_dir", return_value=self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _used_count(self, count: int) -> None:
        path = self.tmp / "data" / "used_topics.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([{"title": f"t{i}"} for i in range(count)]))

    def test_series_turn_fires_on_every_third_video(self) -> None:
        # Arrange
        cfg = _Cfg({"series.name": "History's Almost Moments", "series.every": 3})

        # Act: episode is due when the produced-topic count is a multiple of 3.
        fired = []
        for produced in range(7):
            self._used_count(produced)
            fired.append(topics.series_turn(cfg) is not None)

        # Assert
        self.assertEqual([True, False, False, True, False, False, True], fired)

    def test_series_turn_returns_name_when_due(self) -> None:
        # Arrange
        self._used_count(3)
        cfg = _Cfg({"series.name": "History's Almost Moments", "series.every": 3})

        # Act
        result = topics.series_turn(cfg)

        # Assert
        self.assertEqual("History's Almost Moments", result)

    def test_series_turn_disabled_by_blank_name(self) -> None:
        # Arrange
        self._used_count(3)
        cfg = _Cfg({"series.name": "  ", "series.every": 1})

        # Act / Assert
        self.assertIsNone(topics.series_turn(cfg))

    def test_series_turn_disabled_when_config_absent(self) -> None:
        # Arrange
        self._used_count(3)

        # Act / Assert
        self.assertIsNone(topics.series_turn(_Cfg({})))


if __name__ == "__main__":
    unittest.main()
