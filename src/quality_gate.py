"""Deterministic pre-upload quality checks for a rendered stage.

Everything here is local and offline: state fields, files on disk, and one
isolated ffprobe call. No API calls, no rendering. The report is persisted to
quality.json by the pipeline's quality step and a failed report blocks upload.
"""
from __future__ import annotations

import json
import hashlib
import subprocess
from pathlib import Path

from .config import Config
from .script_generator import enforce_beats, validate_structure

# Every check the gate runs, in report order. All are required: a single fail
# fails the stage, and the score is purely informational.
CHECK_NAMES = [
    "script", "topic", "narration", "structure", "audio", "captions", "assets",
    "video", "dimensions", "duration", "safety", "metadata",
]


def production_checks(cfg: Config, stage: Path, st: dict) -> tuple[dict, list, list]:
    """Offline checks bind evidence, timing and actual composition to this stage."""
    from .research import script_hash, validate_research
    from .alignment import narration_hash, build_storyboard
    from .tts import WordTiming
    from .map_assets import required_maps
    checks, errors, warnings = {}, [], []
    script = st.get('script', {})

    def check(name, ok, message):
        checks[name] = 'pass' if ok else 'fail'
        if not ok:
            errors.append(message)

    verification = st.get('verification', {})
    check('verification', verification.get('status') == 'supported' and
          verification.get('script_hash') == script_hash(script), 'Missing or stale historical verification')
    evidence_errors = validate_research(cfg, st.get('research', {}))
    check('research_evidence', st.get('research', {}).get('status') == 'supported' and not evidence_errors,
          '; '.join(evidence_errors) or 'Research is not supported')
    try:
        maps = required_maps(st.get('research', {}))
        map_ok = [m['map_id'] for m in maps] == [m['map_id'] for m in st.get('maps', [])]
    except (OSError, ValueError, KeyError):
        map_ok = False
    check('map_assets', map_ok, 'Required geographic assets are missing or changed')
    expected_hash = narration_hash(script.get('segments', []))
    board = st.get('storyboard', {})
    check('alignment', board.get('narration_hash') == expected_hash and board.get('coverage') == 1 and
          board.get('timing_source') in ('native', 'forced_aligned'), 'Missing, estimated or stale narration alignment')
    vo = st.get('voiceover', {})
    try:
        rebuilt = build_storyboard(script['segments'], [WordTiming(*w) for w in vo['words']],
            vo['duration'], int(cfg.get(f"video.{st.get('fmt', 'short')}.fps", 30)), vo['timing_source'], strict=True)
        timing_ok = all(board.get(k) == rebuilt.get(k) for k in ('scenes', 'audio_duration', 'last_word_end', 'fps'))
        timing_ok = timing_ok and board.get('audio_sha256') == file_hash(stage / vo['path'])
    except (KeyError, ValueError, OSError, TypeError):
        timing_ok = False
    check('timing_binding', timing_ok, 'Storyboard does not match current narration audio and timestamps')
    audio = st.get('audio_quality', {})
    check('audio_diagnostics', audio.get('narration_hash') == expected_hash and
          audio.get('audio_sha256') == board.get('audio_sha256') and bool(audio.get('audio_sha256')) and
          audio.get('status') in ('pass', 'warn') and not audio.get('errors'), 'Missing or failed audio diagnostics')
    warnings.extend(audio.get('warnings', []))
    try:
        composition = json.loads((stage / 'composition.json').read_text())
    except (OSError, ValueError):
        composition = {}
    try:
        composition_ok = (composition.get('video_sha256') == file_hash(stage / st.get('video', 'video.mp4')) and
                          composition.get('audio_sha256') == file_hash(stage / vo['path']) and
                          composition.get('captions_sha256') == file_hash(stage / st.get('captions', 'captions.ass')))
    except (OSError, KeyError, TypeError):
        composition_ok = False
    check('composition_binding', composition_ok, 'Final video, narration or caption input changed after composition')
    check('caption_composition', bool(composition.get('captions_applied')), 'Required captions were not composed')
    if cfg.get('video.branding.enabled', False):
        check('branding', bool(composition.get('branding_applied')), 'HISTOLD branding was not composed')
    visual = st.get('render_quality', {})
    check('render_quality', bool(visual) and visual.get('narration_hash') == expected_hash and not visual.get('errors'), 'Missing or failed rendered frame checks')
    if any('fallback' in warning for warning in visual.get('warnings', [])):
        check('fallback_review', bool(st.get('visual_reviewed')), 'Stock fallback requires visual review before publishing')
    warnings.extend(visual.get('warnings', []))
    return checks, errors, warnings


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()

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

    # --- four-beat structure (opt-in, shorts only) -------------------------- #
    # Last line of defence: generate_script already retries on a structural
    # failure, but a script that slipped through must not reach upload. Skipped
    # entirely on channels that don't set script.enforce_beats.
    if fmt == "short" and enforce_beats(cfg):
        segments = script.get("segments") or []
        structure_error = validate_structure(
            [str(seg.get("beat", "")).strip().lower() for seg in segments],
            [str(seg.get("narration", "")).strip() for seg in segments],
            cfg.get('script.story_profile'),
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
    warnings = []
    if cfg.get('production.enabled', False):
        extra, failures, warnings = production_checks(cfg, stage, st)
        ordered.update(extra)
        errors.extend(failures)
        if video_ok:
            from .audio_quality import validate_endpoints
            audio_stream = next((s for s in probe.get('streams', []) if s.get('codec_type') == 'audio'), {})
            stream_duration = audio_stream.get('duration')
            # Container duration can mask a short audio stream: require stream duration.
            actual_video_duration = video_stream.get('duration')
            endpoint_errors = validate_endpoints(float(stream_duration) if stream_duration else None,
                float(actual_video_duration) if actual_video_duration else None, st.get('storyboard', {}).get('last_word_end', 0),
                float(cfg.get('audio_quality.endpoint_tolerance', .15)))
            ordered['narration_endpoint'] = 'fail' if endpoint_errors else 'pass'
            errors.extend(endpoint_errors)
    passing = sum(1 for status in ordered.values() if status == "pass")
    return {
        "passed": passing == len(ordered),
        "score": round(100 * passing / len(ordered)),
        "checks": ordered,
        "errors": errors,
        'warnings': warnings,
    }
