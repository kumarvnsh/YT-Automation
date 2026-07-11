#!/usr/bin/env python3
"""Retitle or republish an underperforming video (dashboard Underperformers panel).

Usage:
  python scripts/republish.py --mode retitle --video-id VIDEO_ID [--config <path>]
  python scripts/republish.py --mode repost  --video-id VIDEO_ID --stage-dir-name NAME [--config <path>]

retitle: keeps the video up, swaps its title for a fresh LLM-generated one.
repost : re-uploads the original mp4 (rehydrated by republish.yml from the run
         artifact into output/<stage_dir_name>) under a new title, then deletes
         the old video. Upload-first ordering: a failed delete never loses the
         video; a failed upload deletes nothing.

Both modes need the full 'youtube' manage scope (GITHUB_ACTIONS_SETUP.md §6).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config, base_dir, env  # noqa: E402
from src.notify import Notifier  # noqa: E402
from src.script_generator import regenerate_title  # noqa: E402
from src.youtube_uploader import (  # noqa: E402
    UPLOAD_SCOPES,
    MANAGE_SCOPE,
    delete_video,
    get_credentials,
    get_video_snippet,
    update_video_title,
    upload_video,
)


def _index_path() -> Path:
    return base_dir() / "data" / "published_index.json"


def _load_index() -> list[dict]:
    p = _index_path()
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def _save_index(entries: list[dict]) -> None:
    """Best-effort: an index write error must never lose an uploaded video ID."""
    try:
        p = _index_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(f"  ! published-index update failed (continuing): {exc}")


def _find_entry(entries: list[dict], video_id: str) -> dict | None:
    return next((e for e in entries if e.get("video_id") == video_id), None)


def _manage_client(cfg):
    from googleapiclient.discovery import build

    creds = get_credentials(interactive=False, scopes=[*UPLOAD_SCOPES, MANAGE_SCOPE])
    return build("youtube", "v3", credentials=creds)


def _record_retitle(video_id: str, new_title: str) -> None:
    """Persist retitled_at so the dashboard's retitle cooldown works.

    Videos published before the index existed get a minimal entry appended;
    its empty run_id/stage_dir_name keeps repost (manual and auto) disabled.
    """
    entries = _load_index()
    entry = _find_entry(entries, video_id)
    if entry is None:
        entry = {
            "video_id": video_id,
            "run_id": "",
            "stage_dir_name": "",
            "title": "",
            "published_at": "",
            "channel": env("CHANNEL_LABEL") or "histold",
            "scheduled": False,
            "workflow": "republish",
            "retitled_at": None,
            "republished_from": None,
            "republished_as": None,
        }
        entries.append(entry)
    entry["title"] = new_title
    entry["retitled_at"] = datetime.now(timezone.utc).isoformat()
    _save_index(entries)


def do_retitle(cfg, video_id: str) -> None:
    youtube = _manage_client(cfg)
    snippet = get_video_snippet(youtube, video_id)
    old_title = snippet.get("title", "")
    context = f"{old_title}\n\n{snippet.get('description', '')}"

    new_title = regenerate_title(cfg, old_title, context)
    update_video_title(cfg, video_id, new_title)

    _record_retitle(video_id, new_title)

    Notifier(cfg).send(
        f"🔁 Retitled underperformer\n“{old_title}”\n→ “{new_title}”\nhttps://youtu.be/{video_id}"
    )
    print(f"STATUS: DONE retitle {video_id} → {new_title!r}")


def do_repost(cfg, video_id: str, stage_dir_name: str) -> None:
    stage = base_dir() / cfg.get("output.dir", "output") / stage_dir_name
    video_path = stage / "video.mp4"
    meta_path = stage / "metadata.json"
    if not video_path.exists() or not meta_path.exists():
        raise RuntimeError(
            f"Rehydrated stage incomplete: expected {video_path} and {meta_path} "
            f"(artifact expired or wrong stage_dir_name?)"
        )
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    old_title = meta.get("title", "")
    narration = ""
    script_path = stage / "script.json"
    if script_path.exists():
        sc = json.loads(script_path.read_text(encoding="utf-8"))
        narration = " ".join(s.get("narration", "") for s in sc.get("segments", []))
    context = f"{old_title}\n\n{meta.get('description', '')}\n\n{narration}"

    new_title = regenerate_title(cfg, old_title, context)

    # Upload FIRST so a failure never loses the video.
    new_id = upload_video(
        cfg, video_path, new_title, meta.get("description", ""), meta.get("tags", []),
        privacy_override=meta.get("privacy_status"),
    )

    entries = _load_index()
    old_entry = _find_entry(entries, video_id)
    if old_entry:
        old_entry["republished_as"] = new_id
    entries.append({
        "video_id": new_id,
        "run_id": env("GITHUB_RUN_ID") or "",
        "stage_dir_name": stage_dir_name,
        "title": new_title,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "channel": env("CHANNEL_LABEL") or "histold",
        "scheduled": False,
        "workflow": "republish",
        "retitled_at": None,
        "republished_from": video_id,
        "republished_as": None,
    })
    _save_index(entries)

    delete_failed = None
    try:
        delete_video(cfg, video_id)
    except Exception as exc:  # noqa: BLE001
        delete_failed = str(exc)
        print(f"  ! delete of old video failed (new upload is live): {exc}")

    msg = (
        f"🔁 Republished underperformer\n“{old_title}”\n→ “{new_title}”\n"
        f"https://youtu.be/{new_id}"
    )
    if delete_failed:
        msg += f"\n⚠️ Old video NOT deleted — remove manually: https://youtu.be/{video_id}"
    Notifier(cfg).send(msg)
    print(f"STATUS: DONE repost {video_id} → {new_id}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=None, help="channel config path (default: root config.yaml)")
    ap.add_argument("--mode", choices=["retitle", "repost"], required=True)
    ap.add_argument("--video-id", required=True)
    ap.add_argument("--stage-dir-name", default=None, help="required for --mode repost")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.mode == "retitle":
        do_retitle(cfg, args.video_id)
    else:
        if not args.stage_dir_name:
            ap.error("--stage-dir-name is required for --mode repost")
        do_repost(cfg, args.video_id, args.stage_dir_name)


if __name__ == "__main__":
    main()
