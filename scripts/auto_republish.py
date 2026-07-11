#!/usr/bin/env python3
"""Auto-dispatch republish runs for stuck videos (runs after each analytics export).

Usage:
  python scripts/auto_republish.py [--config <path>] [--label histold] [--dry-run]

Reads data/analytics.json + data/published_index.json, finds videos that are
older than republish.min_age_hours with fewer than republish.view_threshold
views, and dispatches .github/workflows/republish.yml for each (up to
republish.max_per_check per run) via the GitHub API.

Safety rails:
  - disabled unless config republish.auto is true
  - one auto attempt per video ever (auto_dispatched_at marker in the index)
  - repost mode requires an artifact mapping younger than republish.max_age_days
  - videos created by a repost (republished_from) are never auto-flagged again
  - manual dashboard buttons are unaffected

Requires GH_TOKEN + GITHUB_REPOSITORY env (provided by GitHub Actions).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config, base_dir, env  # noqa: E402


def _hours_ago(iso: str) -> float:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return -1.0
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600


def _dispatch(mode: str, video_id: str, entry: dict, label: str) -> None:
    repo = env("GITHUB_REPOSITORY")
    token = env("GH_TOKEN") or env("GITHUB_TOKEN")
    if not repo or not token:
        raise RuntimeError("GH_TOKEN / GITHUB_REPOSITORY not set — cannot dispatch.")
    resp = requests.post(
        f"https://api.github.com/repos/{repo}/actions/workflows/republish.yml/dispatches",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        json={
            "ref": env("GITHUB_REF_NAME") or "master",
            "inputs": {
                "mode": mode,
                "video_id": video_id,
                "source_run_id": str(entry.get("run_id", "")),
                "stage_dir_name": entry.get("stage_dir_name", ""),
            },
        },
        timeout=30,
    )
    resp.raise_for_status()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=None)
    ap.add_argument("--label", default="histold")
    ap.add_argument("--dry-run", action="store_true", help="report candidates, dispatch nothing")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if not cfg.get("republish.auto", False):
        print("auto-republish disabled (republish.auto is false) — nothing to do.")
        return

    threshold = int(cfg.get("republish.view_threshold", 100))
    min_age_h = float(cfg.get("republish.min_age_hours", 30))
    max_age_d = float(cfg.get("republish.max_age_days", 13))
    max_per_check = int(cfg.get("republish.max_per_check", 1))
    mode = cfg.get("republish.mode", "repost")
    if mode not in ("repost", "retitle"):
        raise SystemExit(f"invalid republish.mode: {mode!r}")

    analytics_path = base_dir() / "data" / "analytics.json"
    index_path = base_dir() / "data" / "published_index.json"
    analytics = json.loads(analytics_path.read_text(encoding="utf-8"))
    channel = (analytics.get("channels") or {}).get(args.label) or analytics
    videos = channel.get("videos", [])
    entries = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else []
    by_id = {e.get("video_id"): e for e in entries}

    candidates = []
    for v in videos:
        age_h = _hours_ago(v.get("publishedAt", ""))
        if age_h < min_age_h or (v.get("views") or 0) >= threshold:
            continue
        entry = by_id.get(v.get("id"))
        if entry is None:
            continue  # pre-index video: no artifact mapping, leave to manual retitle
        if entry.get("republished_as") or entry.get("republished_from"):
            continue
        if entry.get("auto_dispatched_at"):
            continue  # one auto attempt per video, ever
        if _hours_ago(entry.get("retitled_at") or "") >= 0 and _hours_ago(entry["retitled_at"]) < 24:
            continue  # fresh retitle: give the new title 24h before reflagging
        if mode == "repost" and not (entry.get("run_id") and entry.get("stage_dir_name")):
            continue  # retitle-only index entry: no artifact mapping to repost
        if mode == "repost" and _hours_ago(entry.get("published_at", "")) > max_age_d * 24:
            continue  # artifact expired
        if mode == "retitle" and entry.get("retitled_at"):
            continue
        candidates.append((v, entry))

    print(f"{len(candidates)} candidate(s) under {threshold} views after {min_age_h:.0f}h "
          f"(mode={mode}, max {max_per_check}/check).")

    for v, entry in candidates[:max_per_check]:
        vid = v["id"]
        print(f"  → {'[dry-run] would dispatch' if args.dry_run else 'dispatching'} "
              f"{mode} for {vid}: {v.get('title', '')!r} ({v.get('views', 0)} views)")
        if args.dry_run:
            continue
        _dispatch(mode, vid, entry, args.label)
        entry["auto_dispatched_at"] = datetime.now(timezone.utc).isoformat()
        index_path.write_text(json.dumps(entries, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
