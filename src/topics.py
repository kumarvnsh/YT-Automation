"""Topic selection for the history / did-you-know niche, with de-duplication.

A pool of seed angles keeps the channel varied. The actual concrete topic is
proposed by the LLM (see script_generator), but we pass it a fresh angle + the
list of recently-used topics so it does not repeat itself.
"""
from __future__ import annotations

import json
import random
from datetime import date
from pathlib import Path

from .config import base_dir


def _used_topics_file():
    return base_dir() / "data" / "used_topics.json"

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


def record_topic(title: str, fmt: str) -> None:
    """Persist a produced topic so future runs don't repeat it."""
    used = _load_used()
    used.append({"title": title, "format": fmt, "date": date.today().isoformat()})
    f = _used_topics_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(used, indent=2), encoding="utf-8")
