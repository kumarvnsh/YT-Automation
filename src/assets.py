"""Fetch stock footage / images from Pexels for each script segment.

Given a Script, returns an ordered list of local media files (video or image)
for each segment, chosen by the segment's keywords. Falls back to a solid-color
clip if downloads fail so the pipeline never hard-stops.
"""
from __future__ import annotations

import random
import subprocess
from dataclasses import dataclass
from pathlib import Path

import requests

from . import imagegen
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
) -> list[list[Asset]]:
    """Return N Assets for each segment (download or fallback)."""
    orientation = (
        cfg.get("assets.orientation_short", "portrait")
        if fmt == "short"
        else cfg.get("assets.orientation_long", "landscape")
    )
    prefer_video = cfg.get("assets.prefer_video", True)
    per_segment = max(int(cfg.get("assets.per_segment_clips", 1)), 1)
    assets: list[list[Asset]] = []

    # One generated illustration per segment beats three interchangeable stock
    # clips, and keeps the per-video image count (and cost) predictable.
    use_ai = imagegen.enabled(cfg) and fmt == "short"
    ai_indices = (
        imagegen.select_segments(cfg, [getattr(s, "beat", "") for s in segments])
        if use_ai
        else set()
    )

    for idx, seg in enumerate(segments):
        query = " ".join(seg.keywords[:3]) or "history"
        dest_base = work_dir / "assets" / f"seg_{idx:02d}"

        found: list[Asset] = []
        if idx in ai_indices:
            made = imagegen.generate(
                cfg,
                seg.narration,
                seg.keywords,
                dest_base.with_name(f"{dest_base.name}_ai").with_suffix(".png"),
                variant=idx,
            )
            if made:
                assets.append([Asset(made, False, seg.keywords)])
                continue
            # Generation failed for this segment — fall through to stock below.

        found = _fetch_one(query, orientation, prefer_video, dest_base, per_segment)
        while len(found) < per_segment:
            sub = len(found)
            clip = _solid_clip(work_dir / "assets" / f"seg_{idx:02d}_{sub:02d}.mp4", 5, orientation)
            found.append(Asset(clip, True, seg.keywords))
        assets.append(found)
    return assets


def _fetch_one(
    query: str,
    orientation: str,
    prefer_video: bool,
    dest_base: Path,
    n: int,
) -> list[Asset]:
    try:
        headers = _headers()
    except RuntimeError:
        return []

    assets: list[Asset] = []
    seen_urls: set[str] = set()
    if prefer_video:
        try:
            r = requests.get(
                PEXELS_VIDEO_URL,
                headers=headers,
                params={"query": query, "per_page": max(8, n * 4), "orientation": orientation},
                timeout=30,
            )
            r.raise_for_status()
            vids = r.json().get("videos", [])
            random.shuffle(vids)
            for v in vids:
                if len(assets) >= n:
                    break
                link = _pick_video_file(v, orientation)
                if not link or link in seen_urls:
                    continue
                seen_urls.add(link)
                dest = dest_base.with_name(f"{dest_base.name}_{len(assets):02d}").with_suffix(".mp4")
                if _download(link, dest):
                    assets.append(Asset(dest, True, [query]))
        except Exception as exc:  # noqa: BLE001
            print(f"  ! pexels video search failed: {exc}")

    if len(assets) >= n:
        return assets

    # Photo fallback.
    try:
        r = requests.get(
            PEXELS_PHOTO_URL,
            headers=headers,
            params={"query": query, "per_page": max(8, (n - len(assets)) * 4), "orientation": orientation},
            timeout=30,
        )
        r.raise_for_status()
        photos = r.json().get("photos", [])
        random.shuffle(photos)
        for p in photos:
            if len(assets) >= n:
                break
            link = p.get("src", {}).get("large2x") or p.get("src", {}).get("large")
            if not link or link in seen_urls:
                continue
            seen_urls.add(link)
            dest = dest_base.with_name(f"{dest_base.name}_{len(assets):02d}").with_suffix(".jpg")
            if _download(link, dest):
                assets.append(Asset(dest, False, [query]))
    except Exception as exc:  # noqa: BLE001
        print(f"  ! pexels photo search failed: {exc}")

    return assets
