"""Fetch stock footage / images from Pexels for each script segment.

Given a Script, returns an ordered list of local media files (video or image),
one (or more) per segment, chosen by the segment's keywords. Falls back to a
solid-color clip if a download fails so the pipeline never hard-stops.
"""
from __future__ import annotations

import random
import subprocess
from dataclasses import dataclass
from pathlib import Path

import requests

from .config import Config, env

PEXELS_VIDEO_URL = "https://api.pexels.com/videos/search"
PEXELS_PHOTO_URL = "https://api.pexels.com/v1/search"


@dataclass
class Asset:
    path: Path
    is_video: bool
    keywords: list[str]


def _headers() -> dict:
    key = env("PEXELS_API_KEY")
    if not key:
        raise RuntimeError("PEXELS_API_KEY missing in .env (free: pexels.com/api).")
    return {"Authorization": key}


def _download(url: str, dest: Path) -> bool:
    try:
        with requests.get(url, stream=True, timeout=90) as r:
            r.raise_for_status()
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as fh:
                for chunk in r.iter_content(chunk_size=1 << 16):
                    fh.write(chunk)
        return dest.stat().st_size > 0
    except Exception as exc:  # noqa: BLE001 - keep pipeline resilient
        print(f"  ! download failed ({exc}) for {url[:60]}...")
        return False


def _pick_video_file(video_json: dict, orientation: str) -> str | None:
    """Choose a reasonable-resolution mp4 link from a Pexels video result."""
    files = [f for f in video_json.get("video_files", []) if f.get("file_type") == "video/mp4"]
    if not files:
        return None
    want_portrait = orientation == "portrait"
    # Prefer files matching orientation and ~1080p.
    def score(f):
        w, h = f.get("width") or 0, f.get("height") or 0
        portrait = h >= w
        orient_match = 0 if portrait == want_portrait else 1
        res_gap = abs((h if want_portrait else w) - (1920 if want_portrait else 1080))
        return (orient_match, res_gap)

    files.sort(key=score)
    return files[0].get("link")


def _solid_clip(dest: Path, seconds: float, orientation: str) -> Path:
    """Generate a plain gradient clip as a last-resort fallback."""
    size = "1080x1920" if orientation == "portrait" else "1920x1080"
    color = random.choice(["0x1a1a2e", "0x16213e", "0x0f3460", "0x222831"])
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i",
         f"color=c={color}:s={size}:d={max(seconds,1):.2f}:r=30",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(dest)],
        check=True, capture_output=True,
    )
    return dest


def fetch_for_segments(
    cfg: Config,
    segments: list,            # list[script_generator.Segment]
    work_dir: Path,
    fmt: str,
) -> list[Asset]:
    """Return one Asset per segment (download or fallback)."""
    orientation = (
        cfg.get("assets.orientation_short", "portrait")
        if fmt == "short"
        else cfg.get("assets.orientation_long", "landscape")
    )
    prefer_video = cfg.get("assets.prefer_video", True)
    assets: list[Asset] = []

    for idx, seg in enumerate(segments):
        query = " ".join(seg.keywords[:3]) or "history"
        dest_base = work_dir / "assets" / f"seg_{idx:02d}"
        asset = _fetch_one(query, orientation, prefer_video, dest_base)
        if asset is None:
            clip = _solid_clip(dest_base.with_suffix(".mp4"), 5, orientation)
            asset = Asset(clip, True, seg.keywords)
        assets.append(asset)
    return assets


def _fetch_one(query: str, orientation: str, prefer_video: bool, dest_base: Path) -> Asset | None:
    try:
        headers = _headers()
    except RuntimeError:
        return None

    if prefer_video:
        try:
            r = requests.get(
                PEXELS_VIDEO_URL,
                headers=headers,
                params={"query": query, "per_page": 8, "orientation": orientation},
                timeout=30,
            )
            r.raise_for_status()
            vids = r.json().get("videos", [])
            random.shuffle(vids)
            for v in vids:
                link = _pick_video_file(v, orientation)
                if link and _download(link, dest_base.with_suffix(".mp4")):
                    return Asset(dest_base.with_suffix(".mp4"), True, [query])
        except Exception as exc:  # noqa: BLE001
            print(f"  ! pexels video search failed: {exc}")

    # Photo fallback.
    try:
        r = requests.get(
            PEXELS_PHOTO_URL,
            headers=headers,
            params={"query": query, "per_page": 8, "orientation": orientation},
            timeout=30,
        )
        r.raise_for_status()
        photos = r.json().get("photos", [])
        random.shuffle(photos)
        for p in photos:
            link = p.get("src", {}).get("large2x") or p.get("src", {}).get("large")
            if link and _download(link, dest_base.with_suffix(".jpg")):
                return Asset(dest_base.with_suffix(".jpg"), False, [query])
    except Exception as exc:  # noqa: BLE001
        print(f"  ! pexels photo search failed: {exc}")

    return None
