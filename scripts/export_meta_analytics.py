#!/usr/bin/env python3
"""Export Facebook Reels + Instagram Reels stats to data/meta_analytics.json.

Usage:
  python scripts/export_meta_analytics.py [--config <path>] [--limit 50]

Uses the same META_ACCESS_TOKEN system-user token as the cross-poster
(src/meta_uploader.py) and the page_id / ig_user_id from config meta:.
Failures are explicit: missing credentials, permissions, or API errors return a
non-zero exit code so the analytics workflow cannot silently publish stale or
zeroed data.

Output shape (mirrors data/analytics.json videos):
  {"facebook":  {"updated": iso, "videos": [{id,title,publishedAt,thumbnail,views,likes,comments,url}]},
   "instagram": {"updated": iso, "videos": [...]}}
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
from src.meta_uploader import GRAPH, _page_access_token, _raise_graph  # noqa: E402


def _first_line(text: str) -> str:
    return (text or "").strip().splitlines()[0][:120] if (text or "").strip() else "(untitled)"


def _insight_views(insights: dict) -> int:
    """Pull a play/view count out of a video_insights blob, whatever Meta calls it today."""
    for item in (insights or {}).get("data", []):
        name = item.get("name", "")
        if "play" in name or "views" in name:
            values = item.get("values", [])
            if values:
                v = values[0].get("value")
                if isinstance(v, (int, float)):
                    return int(v)
    raise RuntimeError(
        "Facebook video_insights contained no recognizable view metric; verify "
        "the token and system user have Page insights access."
    )


def fetch_facebook(token: str, page_id: str, ver: str, limit: int) -> list[dict]:
    page_token = _page_access_token(token, page_id, ver)
    r = requests.get(
        f"{GRAPH}/{ver}/{page_id}/videos",
        params={
            "fields": "id,description,created_time,permalink_url,picture,"
                      "likes.summary(true),comments.summary(true),video_insights",
            "limit": limit,
            "access_token": page_token,
        },
        timeout=60,
    )
    _raise_graph(r, "Facebook video analytics")
    videos = []
    for v in r.json().get("data", []):
        permalink = v.get("permalink_url", "")
        videos.append({
            "id": v["id"],
            "title": _first_line(v.get("description", "")),
            "publishedAt": v.get("created_time", ""),
            "thumbnail": v.get("picture", ""),
            "views": _insight_views(v.get("video_insights")),
            "likes": ((v.get("likes") or {}).get("summary") or {}).get("total_count", 0),
            "comments": ((v.get("comments") or {}).get("summary") or {}).get("total_count", 0),
            "url": f"https://www.facebook.com{permalink}" if permalink.startswith("/") else permalink,
        })
    return videos


def _ig_media_views(media_id: str, token: str, ver: str, metric: str) -> tuple[int, str]:
    """Try the given insights metric, falling back across API renames (views/plays)."""
    errors = []
    for m in (metric, "views" if metric == "plays" else "plays"):
        r = requests.get(f"{GRAPH}/{ver}/{media_id}/insights",
                         params={"metric": m, "access_token": token}, timeout=30)
        if not r.ok:
            errors.append(f"{m} [{r.status_code}]: {r.text}")
            continue
        data = r.json().get("data", [])
        if data and data[0].get("values"):
            value = data[0]["values"][0].get("value")
            if isinstance(value, (int, float)):
                return int(value), m
        errors.append(f"{m}: empty insights data")
    raise RuntimeError(
        f"Instagram insights failed for media {media_id}; verify the token has "
        f"instagram_manage_insights and pages_read_engagement. {'; '.join(errors)}"
    )


def fetch_instagram(token: str, ig_user_id: str, ver: str, limit: int) -> list[dict]:
    r = requests.get(
        f"{GRAPH}/{ver}/{ig_user_id}/media",
        params={
            "fields": "id,caption,media_type,timestamp,permalink,thumbnail_url,"
                      "media_url,like_count,comments_count",
            "limit": limit,
            "access_token": token,
        },
        timeout=60,
    )
    _raise_graph(r, "Instagram media list")
    videos = []
    metric = "views"  # v22+ name; falls back to "plays" on older API versions
    for m in r.json().get("data", []):
        if m.get("media_type") not in ("VIDEO", "REELS"):
            continue
        views, metric = _ig_media_views(m["id"], token, ver, metric)
        videos.append({
            "id": m["id"],
            "title": _first_line(m.get("caption", "")),
            "publishedAt": m.get("timestamp", ""),
            "thumbnail": m.get("thumbnail_url") or m.get("media_url", ""),
            "views": views,
            "likes": m.get("like_count", 0),
            "comments": m.get("comments_count", 0),
            "url": m.get("permalink", ""),
        })
    return videos


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=None)
    ap.add_argument("--limit", type=int, default=50)
    args = ap.parse_args()

    token = env("META_ACCESS_TOKEN")
    if not token:
        print("META_ACCESS_TOKEN not set — Meta analytics export cannot run.", file=sys.stderr)
        return 1

    cfg = load_config(args.config)
    ver = cfg.get("meta.api_version", "v21.0")
    page_id = str(cfg.get("meta.page_id", "")).strip()
    ig_user_id = str(cfg.get("meta.ig_user_id", "")).strip()
    now = datetime.now(timezone.utc).isoformat()

    out: dict = {}
    failures: list[str] = []
    if page_id:
        try:
            vids = fetch_facebook(token, page_id, ver, args.limit)
            vids.sort(key=lambda v: v.get("publishedAt", ""), reverse=True)
            out["facebook"] = {"updated": now, "videos": vids}
            print(f"facebook: {len(vids)} videos")
        except Exception as exc:  # noqa: BLE001
            print(f"! facebook export failed: {exc}")
            failures.append(f"facebook: {exc}")
    if ig_user_id:
        try:
            vids = fetch_instagram(token, ig_user_id, ver, args.limit)
            vids.sort(key=lambda v: v.get("publishedAt", ""), reverse=True)
            out["instagram"] = {"updated": now, "videos": vids}
            print(f"instagram: {len(vids)} videos")
        except Exception as exc:  # noqa: BLE001
            print(f"! instagram export failed: {exc}")
            failures.append(f"instagram: {exc}")

    if failures:
        print(
            "Meta analytics export was incomplete: " + " | ".join(failures),
            file=sys.stderr,
        )
        return 1
    if not out:
        print("nothing exported — leaving data/meta_analytics.json untouched.", file=sys.stderr)
        return 1
    path = base_dir() / "data" / "meta_analytics.json"
    # Merge with existing file so one failed platform doesn't wipe the other's data.
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            existing.update(out)
            out = existing
        except json.JSONDecodeError:
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
