"""Trend-aware topic signals for timely, on-niche history videos.

Free sources, no API keys:
  - Trending searches: Google Trends daily RSS (per region; 'global' merges a few).
  - On this day in history: byabbe.se On-This-Day API (reliable, historical).

All fetches are best-effort: on any network/parse error they return an empty
list so topic selection can fall back gracefully.
"""
from __future__ import annotations

import datetime as _dt
import random
import re

import requests

# Regions merged when the channel asks for "global"/blank.
_GLOBAL_REGIONS = ["US", "GB", "IN", "AU", "CA"]


def _region_list(region: str | None) -> list[str]:
    r = (region or "").strip().upper()
    if not r or r in ("GLOBAL", "WORLD", "WORLDWIDE"):
        return _GLOBAL_REGIONS
    return [r]


def _strip_cdata(text: str) -> str:
    return re.sub(r"<!\[CDATA\[|\]\]>", "", text).strip()


def _parse_trend_items(xml: str) -> list[tuple[str, int, str]]:
    """Return (term, approx_traffic, news_headline) per RSS <item>."""
    out = []
    for it in re.findall(r"<item>(.*?)</item>", xml, flags=re.DOTALL):
        m = re.search(r"<title>(.*?)</title>", it, flags=re.DOTALL)
        if not m:
            continue
        term = _strip_cdata(m.group(1))
        if not term:
            continue
        traffic = 0
        t = re.search(r"<ht:approx_traffic>(.*?)</ht:approx_traffic>", it, flags=re.DOTALL)
        if t:
            digits = re.sub(r"[^0-9]", "", _strip_cdata(t.group(1)))
            traffic = int(digits) if digits else 0
        headline = ""
        n = re.search(r"<ht:news_item_title>(.*?)</ht:news_item_title>", it, flags=re.DOTALL)
        if n:
            headline = _strip_cdata(n.group(1))
        out.append((term, traffic, headline))
    return out


def fetch_trending_searches(region: str | None = None, limit: int = 18) -> list[str]:
    """Return current trending searches, biggest first, with news context.

    Each entry is "term — first news headline" when the RSS carries one; the
    headline is what lets a bare query like "england game time" read as the
    World Cup. Entries are ranked by approximate search traffic (not shuffled)
    so a major ongoing event surfaces at the top instead of by lottery.
    """
    collected: list[tuple[str, int, str]] = []
    for geo in _region_list(region):
        url = f"https://trends.google.com/trending/rss?geo={geo}"
        try:
            r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            collected.extend(_parse_trend_items(r.text))
        except Exception as exc:  # noqa: BLE001
            print(f"  (trends fetch failed for {geo}: {exc})")
    # De-dup by term keeping the highest-traffic occurrence, then rank.
    best: dict[str, tuple[str, int, str]] = {}
    for term, traffic, headline in collected:
        key = term.lower()
        if key not in best or traffic > best[key][1]:
            best[key] = (term, traffic, headline)
    ranked = sorted(best.values(), key=lambda item: item[1], reverse=True)
    return [
        f"{term} — {headline}" if headline else term
        for term, _traffic, headline in ranked[:limit]
    ]


def fetch_on_this_day(when: _dt.date | None = None, limit: int = 12) -> list[str]:
    """Return 'YEAR — event' strings for today's date in history."""
    d = when or _dt.date.today()
    url = f"https://byabbe.se/on-this-day/{d.month}/{d.day}/events.json"
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        events = r.json().get("events", [])
    except Exception as exc:  # noqa: BLE001
        print(f"  (on-this-day fetch failed: {exc})")
        return []
    random.shuffle(events)
    out = []
    for e in events[:limit]:
        year = e.get("year", "")
        desc = (e.get("description", "") or "").strip()
        if desc:
            out.append(f"{year} — {desc}")
    return out


def gather_signals(cfg) -> dict:
    """Collect trend + on-this-day signals per the channel's config."""
    region = cfg.get("channel.trend_region", "")
    return {
        "trends": fetch_trending_searches(region),
        "on_this_day": fetch_on_this_day(),
        "date": _dt.date.today().strftime("%B %d"),
    }
