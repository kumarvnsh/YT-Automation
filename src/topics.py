"""Topic selection for the history / did-you-know niche, with de-duplication.

A pool of seed angles keeps the channel varied. The actual concrete topic is
proposed by the LLM (see script_generator), but we pass it a fresh angle + the
list of recently-used topics so it does not repeat itself.
"""
from __future__ import annotations

import json
import random
import re
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

from .config import base_dir


def _used_topics_file():
    return base_dir() / "data" / "used_topics.json"


def _reservations_file() -> Path:
    return base_dir() / "data" / "topic_reservations.json"

# Broad angles to keep the content fresh across many days.
SEED_ANGLES = [
    "an ancient civilization most people misunderstand",
    "a surprising origin of an everyday object",
    "a forgotten woman who changed history",
    "a 'did you know' fact about a famous historical figure",
    "a strange law or custom from the past",
    "a misconception about a well-known historical event",
    "an accidental invention or discovery",
    "a mystery from history that is still unsolved",
    "a turning point in a war that hinged on something tiny",
    "the real story behind a famous landmark",
    "an everyday word with a bizarre historical origin",
    "a coincidence in history that seems impossible",
    "daily life in a specific ancient city",
    "a technology the ancients had that we 'reinvented' later",
    "a historical figure who lived a wildly unexpected double life",
]


def _load_used() -> list[dict]:
    f = _used_topics_file()
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
    return []


def recent_titles(limit: int = 60) -> list[str]:
    """Return the most recently used topic titles (for the LLM to avoid)."""
    used = _load_used()
    return [u["title"] for u in used[-limit:] if u.get("title")]


def pick_angle(cfg=None) -> str:
    """Pick a random seed angle to steer today's topic.

    Uses per-channel angles from config (channel.angles) when provided, so each
    channel stays on its niche; falls back to the history defaults.
    """
    angles = cfg.get("channel.angles") if cfg is not None else None
    return random.choice(angles if angles else SEED_ANGLES)


def topic_fingerprint(title: str) -> str:
    """Normalize a title into an order-insensitive keyword fingerprint."""
    words = re.findall(r"[a-z0-9]+", title.lower())
    ignored = {"a", "an", "and", "the", "of", "to", "in", "how", "why"}
    return "_".join(sorted({word for word in words if word not in ignored}))


def _load_reservations() -> list[dict]:
    path = _reservations_file()
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def _write_reservations(entries: list[dict]) -> None:
    path = _reservations_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def _fingerprint_is_near(left: str, right: str) -> bool:
    return SequenceMatcher(None, left, right).ratio() >= 0.82


def reserve_topic(title: str, fmt: str, slot: str, job_id: str) -> dict:
    """Reserve a topic for one job, rejecting near-duplicates across slots.

    Idempotent per job_id: a retry after a partial run returns the original
    reservation instead of tripping the duplicate check against itself.
    """
    entries = _load_reservations()
    existing = next((item for item in entries if item["job_id"] == job_id), None)
    if existing:
        return existing
    fingerprint = topic_fingerprint(title)
    recent = [item["fingerprint"] for item in entries[-60:]]
    recent += [topic_fingerprint(t) for t in recent_titles(60)]
    if any(_fingerprint_is_near(fingerprint, other) for other in recent):
        raise ValueError(f"duplicate topic rejected: {title}")
    reservation = {
        "job_id": job_id,
        "title": title,
        "fingerprint": fingerprint,
        "format": fmt,
        "slot": slot,
        "date": date.today().isoformat(),
        "status": "reserved",
    }
    _write_reservations(entries + [reservation])
    return reservation


def series_turn(cfg) -> str | None:
    """Return the series name when this video is a series episode, else None.

    Cadence is counted off how many topics have been produced, so it needs no
    extra state file and stays correct across restarts.
    """
    name = (cfg.get("series.name") or "").strip() if cfg is not None else ""
    if not name:
        return None
    every = int(cfg.get("series.every", 3))
    if every < 1 or len(_load_used()) % every:
        return None
    return name


# Average view percentage bounds. Above 100% the average viewer watched the
# short more than once through — that loop is the strongest Shorts ranking
# signal, and on this channel only 44s+ videos have ever reached it.
WINNER_AVG_VIEW_PCT = 100.0
LOSER_AVG_VIEW_PCT = 55.0
# Like-rate fallback, used only for videos with no retention row yet.
WINNER_LIKE_RATE = 0.025
LOSER_LIKE_RATE = 0.01


def performance_examples(min_views: int = 50, k: int = 4) -> tuple[list[str], list[str]]:
    """Past titles split by retention: (winners, losers).

    Ranks on `avg_view_pct` (YouTube Analytics averageViewPercentage), which
    scripts/export_analytics.py already writes into analytics.json. Falls back
    to like-rate for rows with no retention figure — videos too new for the
    analytics lag, or exported before retention was collected. Videos below
    min_views are ignored: 1 like on 12 views is noise.
    """
    path = base_dir() / "data" / "analytics.json"
    if not path.exists():
        return [], []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return [], []
    videos = []
    for channel in (data.get("channels") or {"_": data}).values():
        videos += channel.get("videos") or []

    retained, liked = [], []
    for video in videos:
        if (video.get("views") or 0) < min_views or not video.get("title"):
            continue
        avg_view_pct = video.get("avg_view_pct")
        if avg_view_pct:
            retained.append((float(avg_view_pct), video["title"]))
        else:
            liked.append(((video.get("likes") or 0) / video["views"], video["title"]))

    # Retention rows win outright; like-rate only fills gaps in the k slots.
    retained.sort(reverse=True)
    liked.sort(reverse=True)
    winners = [t for pct, t in retained[:k] if pct >= WINNER_AVG_VIEW_PCT]
    losers = [t for pct, t in retained[-k:] if pct <= LOSER_AVG_VIEW_PCT]
    if len(winners) < k:
        winners += [t for rate, t in liked[: k - len(winners)] if rate >= WINNER_LIKE_RATE]
    if len(losers) < k:
        losers += [t for rate, t in liked[-(k - len(losers)):] if rate < LOSER_LIKE_RATE]
    return winners, losers


def record_topic(title: str, fmt: str) -> None:
    """Persist a produced topic so future runs don't repeat it."""
    used = _load_used()
    used.append({"title": title, "format": fmt, "date": date.today().isoformat()})
    f = _used_topics_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(used, indent=2), encoding="utf-8")
