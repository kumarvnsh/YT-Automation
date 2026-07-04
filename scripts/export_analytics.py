#!/usr/bin/env python3
"""Export per-channel YouTube analytics (recent uploads + stats) to data/analytics.json.

Usage:
  python scripts/export_analytics.py [--config <path>] [--label histold] [--limit 50]

Uses playlistItems.list against the channel's uploads playlist (1 quota unit)
+ videos.list for statistics (1 unit per <=50 ids) instead of search.list
(100 units/call) — matters at YouTube's 10,000 units/day cap when this runs
several times a day across multiple channels.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config, base_dir  # noqa: E402
from src.youtube_uploader import SCOPES, build_analytics_client, get_credentials  # noqa: E402


def _uploads_playlist_id(youtube) -> str:
    resp = youtube.channels().list(part="contentDetails", mine=True).execute()
    items = resp.get("items", [])
    if not items:
        raise RuntimeError("No YouTube channel found for the authorized credentials.")
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def _recent_video_ids(youtube, playlist_id: str, limit: int) -> list[str]:
    ids: list[str] = []
    page_token = None
    while len(ids) < limit:
        resp = youtube.playlistItems().list(
            part="contentDetails",
            playlistId=playlist_id,
            maxResults=min(50, limit - len(ids)),
            pageToken=page_token,
        ).execute()
        for item in resp.get("items", []):
            ids.append(item["contentDetails"]["videoId"])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return ids


def _video_stats(youtube, video_ids: list[str]) -> list[dict]:
    out: list[dict] = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        resp = youtube.videos().list(part="snippet,statistics", id=",".join(batch)).execute()
        for v in resp.get("items", []):
            sn, stt = v["snippet"], v.get("statistics", {})
            thumbs = sn.get("thumbnails", {})
            thumb = (thumbs.get("medium") or thumbs.get("default") or {}).get("url", "")
            out.append(
                {
                    "id": v["id"],
                    "title": sn.get("title", ""),
                    "publishedAt": sn.get("publishedAt", ""),
                    "thumbnail": thumb,
                    "views": int(stt.get("viewCount", 0)),
                    "likes": int(stt.get("likeCount", 0)),
                    "comments": int(stt.get("commentCount", 0)),
                }
            )
    return out


def _retention_and_subs(
    youtube_analytics,
    video_ids: list[str],
    start_date: str,
    end_date: str,
) -> dict[str, dict]:
    if not video_ids:
        return {}
    try:
        resp = youtube_analytics.reports().query(
            ids="channel==MINE",
            startDate=start_date,
            endDate=end_date,
            metrics="averageViewPercentage,subscribersGained",
            dimensions="video",
            filters=f"video=={','.join(video_ids)}",
            maxResults=len(video_ids),
        ).execute()
    except Exception as exc:  # noqa: BLE001 - analytics should not block basic stats
        print(f"  ! YouTube Analytics retention/subscribers skipped: {exc}", file=sys.stderr)
        return {}

    out: dict[str, dict] = {}
    for row in resp.get("rows", []):
        if len(row) < 3:
            continue
        out[row[0]] = {
            "avg_view_pct": float(row[1] or 0),
            "subs_gained": int(row[2] or 0),
        }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=None)
    parser.add_argument("--label", default="channel", help="Channel key for the merged JSON, e.g. histold/medimyth.")
    parser.add_argument("--limit", type=int, default=50, help="Max recent videos to fetch stats for.")
    args = parser.parse_args()

    load_config(args.config)
    from googleapiclient.discovery import build

    creds = get_credentials(interactive=False)
    youtube = build("youtube", "v3", credentials=creds)

    playlist_id = _uploads_playlist_id(youtube)
    video_ids = _recent_video_ids(youtube, playlist_id, args.limit)
    videos = _video_stats(youtube, video_ids)
    today = datetime.now(timezone.utc).date().isoformat()
    published_dates = [
        v["publishedAt"][:10]
        for v in videos
        if isinstance(v.get("publishedAt"), str) and len(v["publishedAt"]) >= 10
    ]
    start_date = min(published_dates) if published_dates else today
    try:
        analytics_creds = get_credentials(interactive=False, scopes=SCOPES)
        youtube_analytics = build_analytics_client(analytics_creds)
        retention = _retention_and_subs(youtube_analytics, video_ids, start_date, today)
    except Exception as exc:  # noqa: BLE001 - keep basic analytics working
        print(f"  ! YouTube Analytics client skipped: {exc}", file=sys.stderr)
        retention = {}
    for video in videos:
        video.update(retention.get(video["id"], {}))
    videos.sort(key=lambda v: v["publishedAt"], reverse=True)

    out = {
        "channel": args.label,
        "updated": datetime.now(timezone.utc).isoformat(),
        "videos": videos,
    }

    out_path = base_dir() / "data" / "analytics.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {out_path} ({len(videos)} videos)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
