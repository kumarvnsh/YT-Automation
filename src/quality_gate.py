"""Deterministic pre-upload quality checks for a rendered stage.

Everything here is local and offline: state fields, files on disk, and one
isolated ffprobe call. No API calls, no rendering. The report is persisted to
quality.json by the pipeline's quality step and a failed report blocks upload.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .config import Config
from .script_generator import validate_structure

# Every check the gate runs, in report order. All are required: a single fail
# fails the stage, and the score is purely informational.
CHECK_NAMES = [
    "script", "topic", "narration", "structure", "audio", "captions", "assets",
    "video", "dimensions", "duration", "safety", "metadata",
]

WORDS_PER_MINUTE = 150


def _ffprobe(video_path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format",
         "-of", "json", str(video_path)],
        check=True, capture_output=True, text=True,
    )
    return json.loads(out.stdout)


def _file_ok(stage: Path, rel: str | None) -> bool:
    if not rel:
        return False
    path = stage / rel
    return path.is_file() and path.stat().st_size > 0


def _narration_text(script: dict) -> str:
    return " ".join(
        str(seg.get("narration", "")).strip()
        for seg in script.get("segments", [])
    ).strip()


def validate_stage(cfg: Config, stage: Path, st: dict) -> dict:
    """Return passed, score, checks, and errors without making API calls."""
    checks: dict[str, str] = {}
    errors: list[str] = []

    def result(name: str, ok: bool, error: str | None = None) -> None:
        checks[name] = "pass" if ok else "fail"
        if not ok and error:
            errors.append(error)

    min_ratio = float(cfg.get("quality.min_duration_ratio", 0.75))
    max_ratio = float(cfg.get("quality.max_duration_ratio", 1.35))
    fmt = st.get("fmt", "short")
    script = st.get("script") or {}
    narration = _narration_text(script)

    # --- script structure -------------------------------------------------- #
    script_ok = bool(
        str(script.get("title", "")).strip()
        and str(script.get("description", "")).strip()
        and isinstance(script.get("segments"), list)
        and narration
    )
    result("script", script_ok, "script is missing title/description/segments")

    # --- metadata (upload-facing fields) ----------------------------------- #
    title = str(script.get("title", "")).strip()
    tags = script.get("tags")
    metadata_ok = bool(
        title and len(title) <= 100
        and str(script.get("description", "")).strip()
        and isinstance(tags, list) and tags
    )
    result("metadata", metadata_ok, "upload metadata (title/description/tags) incomplete")

    # --- topic reservation -------------------------------------------------- #
    reservation = st.get("topic_reservation") or {}
    result(
        "topic",
        reservation.get("status") == "reserved",
        "stage has no reserved topic",
    )

    # --- narration length vs configured target ----------------------------- #
    target_key = "script.shorts_target_seconds" if fmt == "short" else "script.longform_target_seconds"
    target_seconds = float(cfg.get(target_key, 20 if fmt == "short" else 420))
    target_words = max(1.0, target_seconds * WORDS_PER_MINUTE / 60)
    word_ratio = len(narration.split()) / target_words
    result(
        "narration",
        bool(narration) and min_ratio <= word_ratio <= max_ratio,
        f"narration length ratio {word_ratio:.2f} outside [{min_ratio}, {max_ratio}]",
    )

    # --- four-beat structure (shorts only) ---------------------------------- #
    # Last line of defence: generate_script already retries on a structural
    # failure, but a script that slipped through must not reach upload.
    if fmt == "short":
        segments = script.get("segments") or []
        structure_error = validate_structure(
            [str(seg.get("beat", "")).strip().lower() for seg in segments],
            [str(seg.get("narration", "")).strip() for seg in segments],
        )
        result("structure", structure_error is None, structure_error)
    else:
        result("structure", True)

    # --- voiceover audio ---------------------------------------------------- #
    voiceover = st.get("voiceover") or {}
    audio_ok = (
        _file_ok(stage, voiceover.get("path"))
        and float(voiceover.get("duration") or 0) > 0
        and bool(voiceover.get("words"))
    )

    # --- captions ----------------------------------------------------------- #
    if cfg.get("video.captions.enabled", True):
        result("captions", _file_ok(stage, st.get("captions")), "timed captions missing")
    else:
        result("captions", True)

    # --- stored assets ------------------------------------------------------ #
    assets_ok = True
    for segment_assets in st.get("assets") or []:
        # New shape: list of [path, is_video] sub-clips per segment; old shape:
        # one [path, is_video] pair per segment.
        pairs = segment_assets if segment_assets and isinstance(segment_assets[0], list) else [segment_assets]
        for pair in pairs:
            if not _file_ok(stage, pair[0]):
                assets_ok = False
                errors.append(f"asset missing: {pair[0]}")
    checks["assets"] = "pass" if assets_ok else "fail"

    # --- rendered video + probe-based checks -------------------------------- #
    video_ok = _file_ok(stage, st.get("video"))
    dimensions_ok = duration_ok = False
    if video_ok:
        try:
            probe = _ffprobe(stage / st["video"])
        except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
            video_ok = False
            errors.append(f"ffprobe failed: {exc}")
        else:
            streams = probe.get("streams", [])
            video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
            has_audio_stream = any(s.get("codec_type") == "audio" for s in streams)
            video_ok = video_stream is not None
            audio_ok = audio_ok and has_audio_stream
            if not has_audio_stream:
                errors.append("rendered video has no audio stream")

            want_w = int(cfg.get(f"video.{fmt}.width", 1080 if fmt == "short" else 1920))
            want_h = int(cfg.get(f"video.{fmt}.height", 1920 if fmt == "short" else 1080))
            got_w = int((video_stream or {}).get("width") or 0)
            got_h = int((video_stream or {}).get("height") or 0)
            dimensions_ok = (got_w, got_h) == (want_w, want_h)
            if not dimensions_ok:
                errors.append(f"video is {got_w}x{got_h}, expected {want_w}x{want_h}")

            vo_duration = float(voiceover.get("duration") or 0)
            video_duration = float(probe.get("format", {}).get("duration") or 0)
            if vo_duration > 0 and video_duration > 0:
                ratio = video_duration / vo_duration
                duration_ok = min_ratio <= ratio <= max_ratio
                if not duration_ok:
                    errors.append(
                        f"video/voiceover duration ratio {ratio:.2f} outside [{min_ratio}, {max_ratio}]"
                    )
            else:
                errors.append("video or voiceover duration unavailable")
    else:
        errors.append("rendered video missing")

    result("audio", audio_ok, "voiceover audio missing or empty")
    checks["video"] = "pass" if video_ok else "fail"
    checks["dimensions"] = "pass" if dimensions_ok else "fail"
    checks["duration"] = "pass" if duration_ok else "fail"

    # --- deterministic content safety --------------------------------------- #
    banned = [str(t).lower() for t in (cfg.get("quality.banned_terms") or [])]
    haystack = " ".join([title, str(script.get("description", "")), narration]).lower()
    hits = [term for term in banned if term and term in haystack]
    result("safety", not hits, f"banned terms found: {', '.join(hits)}" if hits else None)

    ordered = {name: checks[name] for name in CHECK_NAMES}
    passing = sum(1 for status in ordered.values() if status == "pass")
    return {
        "passed": passing == len(ordered),
        "score": round(100 * passing / len(ordered)),
        "checks": ordered,
        "errors": errors,
    }
