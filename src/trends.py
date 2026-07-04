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


def fetch_trending_searches(region: str | None = None, limit: int = 18) -> list[str]:
    """Return current trending search terms for the region(s)."""
    terms: list[str] = []
    for geo in _region_list(region):
        url = f"https://trends.google.com/trending/rss?geo={geo}"
        try:
            r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            # <item><title>Term</title> ... — grab item titles.
            items = re.findall(r"<item>(.*?)</item>", r.text, flags=re.DOTALL)
            for it in items:
                m = re.search(r"<title>(.*?)</title>", it, flags=re.DOTALL)
                if m:
                    t = re.sub(r"<!\[CDATA\[|\]\]>", "", m.group(1)).strip()
                    if t:
                        terms.append(t)
        except Exception as exc:  # noqa: BLE001
            print(f"  (trends fetch failed for {geo}: {exc})")
    # De-dup preserving order, cap.
    seen, uniq = set(), []
    for t in terms:
        k = t.lower()
        if k not in seen:
            seen.add(k)
            uniq.append(t)
    random.shuffle(uniq)
    return uniq[:limit]


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
