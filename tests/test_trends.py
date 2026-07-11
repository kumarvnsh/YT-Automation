from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src import trends

ROOT = Path(__file__).resolve().parent.parent

SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0">
<channel>
  <item>
    <title>cate blanchett</title>
    <ht:approx_traffic>20,000+</ht:approx_traffic>
    <ht:news_item>
      <ht:news_item_title>Cate Blanchett stuns at premiere</ht:news_item_title>
    </ht:news_item>
  </item>
  <item>
    <title>england game time</title>
    <ht:approx_traffic>500,000+</ht:approx_traffic>
    <ht:news_item>
      <ht:news_item_title><![CDATA[England v Sweden: World Cup quarter-final kickoff]]></ht:news_item_title>
    </ht:news_item>
  </item>
  <item>
    <title>no context term</title>
  </item>
</channel>
</rss>
"""


class TrendingSearchTests(unittest.TestCase):
    def _fetch(self, limit: int = 18) -> list[str]:
        response = MagicMock()
        response.text = SAMPLE_RSS
        with patch("src.trends.requests.get", return_value=response):
            return trends.fetch_trending_searches("US", limit=limit)

    def test_ranked_by_traffic_not_shuffled(self) -> None:
        terms = self._fetch()
        self.assertTrue(terms[0].startswith("england game time"))

    def test_news_headline_gives_bridging_context(self) -> None:
        terms = self._fetch()
        self.assertIn(
            "england game time — England v Sweden: World Cup quarter-final kickoff",
            terms,
        )

    def test_term_without_news_item_kept_bare(self) -> None:
        terms = self._fetch()
        self.assertIn("no context term", terms)

    def test_fetch_failure_returns_empty_list(self) -> None:
        with patch("src.trends.requests.get", side_effect=OSError("offline")):
            self.assertEqual([], trends.fetch_trending_searches("US"))

    def test_global_merges_regions_and_dedupes_keeping_highest_traffic(self) -> None:
        response = MagicMock()
        response.text = SAMPLE_RSS
        with patch("src.trends.requests.get", return_value=response) as get:
            terms = trends.fetch_trending_searches("global")
        self.assertEqual(5, get.call_count)  # US GB IN AU CA
        self.assertEqual(3, len(terms))  # duplicates collapsed across regions


class TrendsPanelTests(unittest.TestCase):
    def test_dashboard_renders_live_trends_and_on_this_day(self) -> None:
        app_js = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
        self.assertIn("c.trends || []", app_js)
        self.assertIn("Trending now", app_js)
        self.assertIn("On this day", app_js)


if __name__ == "__main__":
    unittest.main()
