"""Assemble the final video with ffmpeg.

Pipeline:
  1. For each segment, build a normalized clip of the right duration (derived
     from the segment's share of the voiceover) at the target resolution.
     - video assets: trimmed/looped + scaled + cropped to fill, Ken-Burns N/A
     - image assets: zoom-pan ("Ken Burns") for motion
  2. Concatenate all segment clips (video only).
  3. Mux the voiceover (+ optional background music ducked under it).
  4. Burn in ASS captions.

Everything uses ffmpeg via subprocess for reliability and zero heavy deps.
"""
from __future__ import annotations

import os
import random
import subprocess
from pathlib import Path

from .config import Config
from .assets import Asset


def _encode_settings(cfg: Config) -> tuple[str, str]:
    """Resolve ffmpeg preset/crf: env override > config > sane default."""
    preset = os.environ.get("FFMPEG_PRESET") or cfg.get("video.encode.preset", "veryfast")
    crf = os.environ.get("FFMPEG_CRF") or str(cfg.get("video.encode.crf", 23))
    return preset, str(crf)


_SUBTITLES_OK: bool | None = None


def _has_subtitles_filter() -> bool:
    """Whether this ffmpeg build includes the libass-backed `subtitles` filter."""
    global _SUBTITLES_OK
    if _SUBTITLES_OK is None:
        try:
            out = subprocess.run(
                ["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True
            )
            _SUBTITLES_OK = " subtitles " in out.stdout
        except Exception:  # noqa: BLE001
            _SUBTITLES_OK = False
    return _SUBTITLES_OK


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(cwd) if cwd else None)
    if proc.returncode != 0:
        raise RuntimeError(
            "ffmpeg failed:\n" + " ".join(cmd[:12]) + " ...\n" + proc.stderr[-1500:]
        )


def _dims(cfg: Config, fmt: str) -> tuple[int, int, int]:
    node = "video.short" if fmt == "short" else "video.long"
    return (
        cfg.get(f"{node}.width"),
        cfg.get(f"{node}.height"),
        cfg.get(f"{node}.fps", 30),
    )


def _segment_durations(total: float, n: int, word_times) -> list[float]:
    """Distribute total duration across n segments.

    If we have word timings we could align precisely, but a proportional split
    by narration length is robust and visually fine. Here we split evenly with a
    small floor so no clip is too short.
    """
    if n == 0:
        return []
    base = max(total / n, 1.2)
    return [base] * n


def _build_segment_clip(
    asset: Asset, duration: float, w: int, h: int, fps: int, dest: Path, preset: str = "veryfast"
) -> Path:
    """Produce a silent, normalized clip of exactly `duration` seconds."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    scale_fill = (
        f"scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},setsar=1,fps={fps}"
    )
    if asset.is_video:
        # Loop the input if shorter than needed, then trim to duration.
        cmd = [
            "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(asset.path),
            "-t", f"{duration:.3f}",
            "-vf", scale_fill,
            "-an", "-c:v", "libx264", "-preset", preset, "-pix_fmt", "yuv420p", "-r", str(fps),
            str(dest),
        ]
    else:
        # Image -> Ken Burns slow zoom.
        frames = max(int(duration * fps), 1)
        zoom = "zoompan=z='min(zoom+0.0008,1.12)':d={d}:s={w}x{h}:fps={fps}".format(
            d=frames, w=w, h=h, fps=fps
        )
        vf = (
            f"scale={w*2}:{h*2}:force_original_aspect_ratio=increase,"
            f"crop={w*2}:{h*2},{zoom},setsar=1"
        )
        cmd = [
            "ffmpeg", "-y", "-loop", "1", "-i", str(asset.path),
            "-t", f"{duration:.3f}",
            "-vf", vf,
            "-c:v", "libx264", "-preset", preset, "-pix_fmt", "yuv420p", "-r", str(fps),
            str(dest),
        ]
    _run(cmd)
    return dest


def _concat(clips: list[Path], dest: Path) -> Path:
    listfile = dest.parent / "concat_list.txt"
    listfile.write_text(
        "".join(f"file '{c.resolve()}'\n" for c in clips), encoding="utf-8"
    )
    _run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile),
        "-c", "copy", str(dest),
    ])
    return dest


def _pick_music(cfg: Config, root: Path) -> Path | None:
    if not cfg.get("video.background_music.enabled", False):
        return None
    music_dir = root / "assets" / "music"
    if not music_dir.exists():
        return None
    tracks = [p for p in music_dir.iterdir() if p.suffix.lower() in {".mp3", ".m4a", ".wav"}]
    return random.choice(tracks) if tracks else None


def build_video(
    cfg: Config,
    assets: list[Asset],
    voiceover_path: Path,
    voiceover_duration: float,
    word_times,
    ass_path: Path | None,
    fmt: str,
    work_dir: Path,
    out_path: Path,
    project_root: Path,
) -> Path:
    """Render the final mp4 (b-roll + captions + audio). Returns out_path."""
    silent_video = build_broll_silent(
        cfg, assets, voiceover_duration, word_times, fmt, work_dir, "silent.mp4"
    )
    return finalize(cfg, silent_video, voiceover_path, ass_path, out_path,
                    work_dir, project_root)


def build_broll_silent(
    cfg: Config, assets: list[Asset], voiceover_duration: float, word_times,
    fmt: str, work_dir: Path, out_name: str = "silent.mp4",
) -> Path:
    """Build a silent background video by concatenating per-segment b-roll clips."""
    w, h, fps = _dims(cfg, fmt)
    preset, _ = _encode_settings(cfg)
    durations = _segment_durations(voiceover_duration + 0.5, len(assets), word_times)
    clips: list[Path] = []
    for i, (asset, dur) in enumerate(zip(assets, durations)):
        clip = _build_segment_clip(
            asset, dur, w, h, fps, work_dir / "clips" / f"clip_{i:02d}.mp4", preset
        )
        clips.append(clip)
    return _concat(clips, work_dir / out_name)


def compose_overlay(
    cfg: Config,
    bg_video: Path,
    owl_overlay: Path,
    voiceover_path: Path,
    ass_path: Path | None,
    out_path: Path,
    work_dir: Path,
    project_root: Path,
) -> Path:
    """Single-pass compose for mascot mode: overlay the (alpha) owl onto the
    b-roll background, burn captions, and mux audio (+ optional music) — one encode.
    """
    preset, crf = _encode_settings(cfg)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # inputs: 0=bg, 1=owl(alpha), 2=voiceover, [3=music]
    inputs = ["-i", str(bg_video.resolve()), "-i", str(owl_overlay.resolve()),
              "-i", str(voiceover_path.resolve())]
    music = _pick_music(cfg, project_root)
    if music:
        vol = cfg.get("video.background_music.volume", 0.12)
        inputs += ["-stream_loop", "-1", "-i", str(music)]
        filter_a = (f"[2:a]volume=1.0[vo];[3:a]volume={vol}[bg];"
                    f"[vo][bg]amix=inputs=2:duration=first:dropout_transition=2[aout]")
    else:
        filter_a = "[2:a]volume=1.0[aout]"

    captions_on = bool(ass_path and cfg.get("video.captions.enabled", True)
                       and _has_subtitles_filter())
    run_cwd = None
    if captions_on:
        run_cwd = Path(ass_path).parent
        ass_name = Path(ass_path).name
        filter_v = f"[0:v][1:v]overlay=0:0[ov];[ov]subtitles=filename={ass_name}[vout]"
    else:
        if ass_path and cfg.get("video.captions.enabled", True):
            print("  ! WARNING: no 'subtitles' filter (missing libass) — no captions.")
        filter_v = "[0:v][1:v]overlay=0:0[vout]"

    _run([
        "ffmpeg", "-y", *inputs,
        "-filter_complex", filter_v + ";" + filter_a,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", preset, "-crf", crf, "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-shortest",
        str(out_path.resolve()),
    ], cwd=run_cwd)
    return out_path


def finalize(
    cfg: Config,
    silent_video: Path,
    voiceover_path: Path,
    ass_path: Path | None,
    out_path: Path,
    work_dir: Path,
    project_root: Path,
) -> Path:
    """Burn captions onto a silent video and mux the voiceover (+ optional music).

    Shared by the b-roll path (build_video) and the mascot path. Two passes:
    captions via isolated -vf (robust on ffmpeg 8.x), then audio mux with video
    copied (fast, no re-encode).
    """
    preset, crf = _encode_settings(cfg)

    # --- Audio: voiceover (+ optional ducked music) ---
    music = _pick_music(cfg, project_root)
    audio_inputs = ["-i", str(voiceover_path)]
    if music:
        vol = cfg.get("video.background_music.volume", 0.12)
        audio_inputs += ["-stream_loop", "-1", "-i", str(music)]
        filter_a = (
            f"[1:a]volume=1.0[vo];"
            f"[2:a]volume={vol}[bg];"
            f"[vo][bg]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )
    else:
        filter_a = "[1:a]volume=1.0[aout]"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    captions_on = bool(ass_path and cfg.get("video.captions.enabled", True))
    if captions_on and not _has_subtitles_filter():
        print(
            "  ! WARNING: this ffmpeg build has no 'subtitles' filter (missing libass) — "
            "rendering WITHOUT burned captions. Install a libass-enabled ffmpeg "
            "(e.g. `brew install ffmpeg`) to get captions."
        )
        captions_on = False

    # --- Pass 1: burn captions onto the silent video ---
    # Use an isolated -vf with the explicit `filename=` option and run ffmpeg
    # from the stage dir (bare filename, no slashes/colons/quotes). This avoids
    # filtergraph-parser quirks in newer ffmpeg (8.x rejects the shorthand /
    # quoted-path forms inside -filter_complex).
    if captions_on:
        stage_dir = Path(ass_path).parent
        ass_name = Path(ass_path).name
        captioned = work_dir / "captioned.mp4"
        _run([
            "ffmpeg", "-y", "-i", str(silent_video.resolve()),
            "-vf", f"subtitles=filename={ass_name}",
            "-c:v", "libx264", "-preset", preset, "-crf", crf, "-pix_fmt", "yuv420p",
            "-an", str(captioned.resolve()),
        ], cwd=stage_dir)
        video_src = captioned
    else:
        video_src = silent_video

    # --- Pass 2: mux audio (+ optional music). Video is copied (no re-encode). ---
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_src.resolve()),
        *audio_inputs,
        "-filter_complex", filter_a,
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(out_path.resolve()),
    ]
    _run(cmd)
    return out_path
