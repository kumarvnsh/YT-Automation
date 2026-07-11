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

PLAYLIST_RULES = [
    (
        "Lost Cities",
        [
            "lost city",
            "lost cities",
            "city",
            "ruins",
            "buried",
            "desert",
            "temple",
            "atlantis",
            "pompeii",
            "petra",
        ],
    ),
    (
        "Vanished Civilizations",
        [
            "civilization",
            "civilisation",
            "empire",
            "collapsed",
            "vanished",
            "maya",
            "mayan",
            "olmec",
            "sumer",
            "sumerian",
            "indus",
            "mohenjo",
            "harappa",
        ],
    ),
    (
        "Forgotten Ancient Technology",
        [
            "technology",
            "machine",
            "device",
            "invention",
            "engineering",
            "gear",
            "mechanism",
            "map",
            "tool",
            "weapon",
        ],
    ),
    (
        "Ancient Mysteries",
        [
            "mystery",
            "mysteries",
            "unknown",
            "unsolved",
            "ancient",
            "secret",
            "hidden",
            "strange",
        ],
    ),
    (
        DEFAULT_PLAYLIST_TITLE,
        [
            "erased",
            "forgotten",
            "history",
            "records",
            "miscredited",
            "ignored",
            "vanished from history",
        ],
    ),
]


def _normalize_title(title: str) -> str:
    return " ".join(title.lower().split())


def _video_text(video: dict) -> str:
    parts = [
        str(video.get("title", "")),
        str(video.get("description", "")),
        " ".join(str(tag) for tag in video.get("tags", []) or []),
    ]
    return _normalize_title(" ".join(parts))


def infer_playlist_title(video: dict) -> str:
    text = _video_text(video)
    best_title = DEFAULT_PLAYLIST_TITLE
    best_score = 0
    for title, keywords in PLAYLIST_RULES:
        score = sum(1 for keyword in keywords if keyword in text)
        if score > best_score:
            best_title = title
            best_score = score
    return best_title


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


class _ChannelPlaylists:
    """One fetch of the channel's playlists, shared across a bulk run.

    Memberships load lazily per playlist and are cached, so sorting 50 videos
    costs one playlists.list plus one playlistItems.list per *target* playlist
    instead of refetching everything per video. A playlist whose items cannot
    be read (playlistNotFound) caches as None and is treated as unusable.
    """

    def __init__(self, youtube):
        self.youtube = youtube
        self.uploads_id = export_analytics._uploads_playlist_id(youtube)
        self.playlists = export_analytics._owned_playlists(youtube, self.uploads_id)
        self._members: dict[str, set[str] | None] = {}

    def members(self, playlist_id: str) -> set[str] | None:
        if playlist_id not in self._members:
            try:
                self._members[playlist_id] = set(
                    export_analytics._playlist_video_ids(self.youtube, playlist_id)
                )
            except Exception as exc:  # noqa: BLE001
                if not export_analytics._is_playlist_not_found(exc):
                    raise
                self._members[playlist_id] = None
        return self._members[playlist_id]

    def create(self, title: str) -> dict:
        playlist = _create_playlist(self.youtube, title)
        self.playlists.append(playlist)
        # Brand-new playlist: known empty. Never list it right away — a fresh
        # playlist can 404 on playlistItems.list before it propagates.
        self._members[playlist["id"]] = set()
        return playlist


def _resolve_target_playlist(
    channel: _ChannelPlaylists,
    playlist_id: str | None,
    playlist_title: str,
) -> dict:
    if playlist_id:
        target = next((p for p in channel.playlists if p["id"] == playlist_id), None)
        if target is None:
            raise ValueError(f"Playlist {playlist_id!r} not found on this channel.")
        return target

    wanted = _normalize_title(playlist_title or DEFAULT_PLAYLIST_TITLE)
    target = next(
        (p for p in channel.playlists if _normalize_title(p.get("title", "")) == wanted),
        None,
    )
    if target is not None:
        return target
    return channel.create(playlist_title or DEFAULT_PLAYLIST_TITLE)


def add_video_to_playlist(
    youtube,
    video_id: str,
    playlist_id: str | None = None,
    playlist_title: str = DEFAULT_PLAYLIST_TITLE,
    channel: _ChannelPlaylists | None = None,
) -> dict:
    channel = channel or _ChannelPlaylists(youtube)
    if playlist_id == channel.uploads_id:
        raise ValueError("Refusing to add videos to the automatic uploads playlist.")

    target = _resolve_target_playlist(channel, playlist_id, playlist_title)

    existing_ids = channel.members(target["id"])
    if existing_ids is None:
        raise ValueError(
            f"Playlist {target['title']!r} ({target['id']}) exists but its items "
            "cannot be read (YouTube returned playlistNotFound). Delete or recreate "
            "that playlist on the channel, or pass an explicit --playlist-id."
        )
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
    existing_ids.add(video_id)
    return {
        "status": "inserted",
        "video_id": video_id,
        "playlist_id": target["id"],
        "playlist_title": target["title"],
        "playlist_item_id": resp.get("id", ""),
    }


def bulk_sort_videos(youtube, videos: list[dict]) -> list[dict]:
    channel = _ChannelPlaylists(youtube)
    results = []
    for video in videos:
        if not video.get("is_unsorted"):
            continue
        video_id = video.get("id")
        if not video_id:
            continue
        try:
            result = add_video_to_playlist(
                youtube,
                video_id,
                playlist_title=infer_playlist_title(video),
                channel=channel,
            )
        except Exception as exc:  # noqa: BLE001 - keep sorting the rest
            result = {"status": "error", "video_id": video_id, "error": str(exc)}
        results.append(result)
    return results


def _analytics_videos() -> list[dict]:
    path = base_dir() / "data" / "analytics.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    channels = data.get("channels")
    if isinstance(channels, dict) and channels:
        channel = channels.get("histold") or next(iter(channels.values()))
        return channel.get("videos", [])
    return data.get("videos", [])


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
    parser.add_argument("--video-id", default="")
    parser.add_argument("--playlist-id", default="")
    parser.add_argument("--playlist-title", default=DEFAULT_PLAYLIST_TITLE)
    parser.add_argument("--bulk", action="store_true", help="sort every unsorted video from data/analytics.json")
    args = parser.parse_args()
    if not args.bulk and not args.video_id:
        parser.error("--video-id is required unless --bulk is set")

    cfg = load_config(args.config)
    from googleapiclient.discovery import build

    creds = get_credentials(interactive=False, scopes=[*UPLOAD_SCOPES, MANAGE_SCOPE])
    youtube = build("youtube", "v3", credentials=creds)
    _verify_channel(cfg, youtube)

    if args.bulk:
        results = bulk_sort_videos(youtube, _analytics_videos())
        errors = 0
        for result in results:
            if result["status"] == "error":
                errors += 1
                print(f"error: {result['video_id']} — {result['error']}")
                continue
            _record_assignment(result)
            print(
                f"{result['status']}: {result['video_id']} -> "
                f"{result['playlist_title']} ({result['playlist_id']})"
            )
        print(f"bulk complete: {len(results)} video(s) processed, {errors} error(s)")
        if errors and errors == len(results):
            return 1
    else:
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
