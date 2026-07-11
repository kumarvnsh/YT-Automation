#!/usr/bin/env python3
"""Add a YouTube video to one owned playlist.

Triggered by the dashboard via .github/workflows/playlist.yml. The script is
idempotent: if the video is already in the target playlist, it exits
successfully without inserting a duplicate.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import export_analytics  # noqa: E402
from src.config import base_dir, load_config  # noqa: E402
from src.youtube_uploader import MANAGE_SCOPE, UPLOAD_SCOPES, get_credentials, _verify_channel  # noqa: E402

DEFAULT_PLAYLIST_TITLE = "Erased From History"


def _normalize_title(title: str) -> str:
    return " ".join(title.lower().split())


def _create_playlist(youtube, title: str) -> dict:
    resp = youtube.playlists().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": title,
                "description": "Automatically created by the Histold dashboard.",
            },
            "status": {"privacyStatus": "public"},
        },
    ).execute()
    return {
        "id": resp["id"],
        "title": resp.get("snippet", {}).get("title", title),
        "video_count": int(resp.get("contentDetails", {}).get("itemCount", 0)),
    }


def _resolve_target_playlist(
    youtube,
    playlists: list[dict],
    playlist_id: str | None,
    playlist_title: str,
) -> dict:
    if playlist_id:
        target = next((p for p in playlists if p["id"] == playlist_id), None)
        if target is None:
            raise ValueError(f"Playlist {playlist_id!r} not found on this channel.")
        return target

    wanted = _normalize_title(playlist_title or DEFAULT_PLAYLIST_TITLE)
    target = next(
        (p for p in playlists if _normalize_title(p.get("title", "")) == wanted),
        None,
    )
    if target is not None:
        return target
    return _create_playlist(youtube, playlist_title or DEFAULT_PLAYLIST_TITLE)


def add_video_to_playlist(
    youtube,
    video_id: str,
    playlist_id: str | None = None,
    playlist_title: str = DEFAULT_PLAYLIST_TITLE,
) -> dict:
    uploads_playlist_id = export_analytics._uploads_playlist_id(youtube)
    if playlist_id == uploads_playlist_id:
        raise ValueError("Refusing to add videos to the automatic uploads playlist.")

    playlists = export_analytics._owned_playlists(youtube, uploads_playlist_id)
    target = _resolve_target_playlist(youtube, playlists, playlist_id, playlist_title)

    existing_ids = set(export_analytics._playlist_video_ids(youtube, target["id"]))
    if video_id in existing_ids:
        return {
            "status": "already-present",
            "video_id": video_id,
            "playlist_id": target["id"],
            "playlist_title": target["title"],
        }

    resp = youtube.playlistItems().insert(
        part="snippet",
        body={
            "snippet": {
                "playlistId": target["id"],
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
            }
        },
    ).execute()
    return {
        "status": "inserted",
        "video_id": video_id,
        "playlist_id": target["id"],
        "playlist_title": target["title"],
        "playlist_item_id": resp.get("id", ""),
    }


def _record_assignment(result: dict) -> None:
    path = base_dir() / "data" / "published_index.json"
    if not path.exists():
        return
    entries = json.loads(path.read_text(encoding="utf-8"))
    changed = False
    for entry in entries:
        if entry.get("video_id") != result["video_id"]:
            continue
        playlist_ids = entry.setdefault("playlist_ids", [])
        if result["playlist_id"] not in playlist_ids:
            playlist_ids.append(result["playlist_id"])
            changed = True
        assignments = entry.setdefault("playlist_assignments", [])
        if not any(a.get("playlist_id") == result["playlist_id"] for a in assignments):
            assignments.append(
                {
                    "playlist_id": result["playlist_id"],
                    "playlist_title": result.get("playlist_title", ""),
                    "assigned_at": datetime.now(timezone.utc).isoformat(),
                    "status": result["status"],
                }
            )
            changed = True
    if changed:
        path.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=None)
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--playlist-id", default="")
    parser.add_argument("--playlist-title", default=DEFAULT_PLAYLIST_TITLE)
    args = parser.parse_args()

    cfg = load_config(args.config)
    from googleapiclient.discovery import build

    creds = get_credentials(interactive=False, scopes=[*UPLOAD_SCOPES, MANAGE_SCOPE])
    youtube = build("youtube", "v3", credentials=creds)
    _verify_channel(cfg, youtube)

    result = add_video_to_playlist(
        youtube,
        args.video_id,
        playlist_id=args.playlist_id or None,
        playlist_title=args.playlist_title,
    )
    _record_assignment(result)
    print(
        f"{result['status']}: {result['video_id']} -> "
        f"{result['playlist_title']} ({result['playlist_id']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
