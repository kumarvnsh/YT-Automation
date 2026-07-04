"""Checkpointed, resumable video pipeline.

The runtime environment (e.g. a sandboxed scheduler) may cap each invocation at a
few seconds and kill background processes. To survive that, the pipeline is split
into discrete, idempotent steps that persist their output to a stage directory and
record progress in state.json. A killed run can be resumed; a step-at-a-time mode
lets a scheduler complete the whole pipeline across several short calls.

Steps: script → voiceover → captions → assets → render → upload
Each step is skipped if already marked done in state.json.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .config import Config, base_dir
from . import topics

STEP_ORDER = ["script", "voiceover", "captions", "assets", "render", "compose", "upload"]


# --------------------------------------------------------------------------- #
# stage + state helpers
# --------------------------------------------------------------------------- #
def _slug(text: str, n: int = 40) -> str:
    keep = "".join(c if c.isalnum() or c in " -_" else "" for c in text)
    return "_".join(keep.lower().split())[:n] or "video"


def _out_root(cfg: Config) -> Path:
    return base_dir() / cfg.get("output.dir", "output")


def new_stage(
    cfg: Config,
    fmt: str,
    *,
    no_upload: bool,
    dry_run: bool,
    topic: str | None = None,
    privacy: str | None = None,
) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    stage = _out_root(cfg) / f"{ts}_{fmt}"
    stage.mkdir(parents=True, exist_ok=True)
    target = ["script"] if dry_run else list(STEP_ORDER)
    state = {
        "fmt": fmt,
        "created": ts,
        "target_steps": target,
        "done": [],
        "complete": False,
        "error": None,
        "no_upload": no_upload,
        "overrides": {"topic": topic, "privacy": privacy},
    }
    save_state(stage, state)
    return stage


def stage_path(cfg: Config, name: str) -> Path:
    """Resolve an explicit stage directory by name (bypasses active-stage lookup)."""
    return _out_root(cfg) / name


def state_path(stage: Path) -> Path:
    return stage / "state.json"


def load_state(stage: Path) -> dict:
    return json.loads(state_path(stage).read_text(encoding="utf-8"))


def save_state(stage: Path, state: dict) -> None:
    state_path(stage).write_text(json.dumps(state, indent=2), encoding="utf-8")


def active_stage(cfg: Config, fmt: str | None = None) -> Path | None:
    """Newest stage that has not completed and has no fatal error."""
    root = _out_root(cfg)
    if not root.exists():
        return None
    candidates = []
    for d in root.iterdir():
        sp = d / "state.json"
        if not sp.exists():
            continue
        try:
            st = json.loads(sp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if st.get("complete"):
            continue
        if fmt and st.get("fmt") != fmt:
            continue
        candidates.append((st.get("created", d.name), d))
    if not candidates:
        return None
    candidates.sort()
    return candidates[-1][1]


# --------------------------------------------------------------------------- #
# step implementations  (each takes cfg, stage, state and mutates state)
# --------------------------------------------------------------------------- #
def _rebuild_segments(state: dict):
    from .script_generator import Segment

    return [
        Segment(s["narration"], s.get("keywords", []))
        for s in state["script"]["segments"]
    ]


def _effective_mode(cfg: Config, fmt: str) -> str:
    """Visual mode for this format. Long-form always b-roll (mascot render too heavy).

    Shorts honor config video.mode: 'broll' | 'mascot' | 'captions_only'.
    """
    if fmt != "short":
        return "broll"
    mode = cfg.get("video.mode", "broll")
    return mode if mode in ("broll", "mascot", "captions_only", "motion_graphics") else "broll"


def _step_script(cfg: Config, stage: Path, st: dict) -> None:
    from .script_generator import generate_script

    overrides = st.get("overrides", {})
    script = generate_script(cfg, st["fmt"], topic_override=overrides.get("topic"))
    st["script"] = script.to_dict()
    st["title"] = script.title
    (stage / "script.json").write_text(json.dumps(script.to_dict(), indent=2), encoding="utf-8")
    (stage / "metadata.json").write_text(
        json.dumps(
            {
                "title": script.title,
                "description": script.description,
                "tags": script.tags,
                "format": st["fmt"],
                "privacy_status": overrides.get("privacy") or cfg.get("youtube.privacy_status", "private"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _step_voiceover(cfg: Config, stage: Path, st: dict) -> None:
    from .tts import synthesize
    from .script_generator import Script

    segs = _rebuild_segments(st)
    narration = " ".join(s.narration.strip() for s in segs).strip()
    vo = synthesize(cfg, narration, stage / "voiceover.mp3")
    st["voiceover"] = {
        "path": "voiceover.mp3",
        "duration": vo.duration,
        "words": [[w.word, w.start, w.end] for w in vo.words],
    }


def _step_captions(cfg: Config, stage: Path, st: dict) -> None:
    if not cfg.get("video.captions.enabled", True):
        st["captions"] = None
        return
    from .captions import build_ass
    from .tts import WordTiming

    words = [WordTiming(*w) for w in st["voiceover"]["words"]]
    mode = _effective_mode(cfg, st["fmt"])
    position = {"mascot": "top", "captions_only": "center",
                "motion_graphics": "center"}.get(mode, "bottom")
    build_ass(cfg, words, stage / "captions.ass", st["fmt"], position=position)
    st["captions"] = "captions.ass"


def _step_assets(cfg: Config, stage: Path, st: dict) -> None:
    # captions_only / motion_graphics need no footage; broll & mascot fetch b-roll.
    if _effective_mode(cfg, st["fmt"]) in ("captions_only", "motion_graphics"):
        st["assets"] = []
        return
    from .assets import fetch_for_segments

    segs = _rebuild_segments(st)
    assets = fetch_for_segments(cfg, segs, stage, st["fmt"])
    st["assets"] = [[str(Path(a.path).relative_to(stage)), a.is_video] for a in assets]


def _step_render(cfg: Config, stage: Path, st: dict) -> None:
    """Build the silent visuals. broll → silent.mp4; mascot → bg.mp4 + owl.mov."""
    from .assets import Asset
    from .tts import WordTiming
    from . import video_builder as vb

    duration = st["voiceover"]["duration"]
    assets = [Asset(stage / p, is_video, []) for p, is_video in st["assets"]]
    words = [WordTiming(*w) for w in st["voiceover"]["words"]]
    mode = _effective_mode(cfg, st["fmt"])

    if mode == "mascot":
        from .mascot import render_overlay

        vb.build_broll_silent(cfg, assets, duration, words, st["fmt"], stage, "bg.mp4")
        mascot_dir = base_dir() / cfg.get("video.mascot.dir", "Mascot")
        render_overlay(cfg, stage / "voiceover.mp3", duration, st["fmt"],
                       stage / "owl.mov", mascot_dir)
    elif mode == "captions_only":
        from .mascot import render_background

        render_background(cfg, duration, st["fmt"], stage / "silent.mp4")
    elif mode == "motion_graphics":
        from .motion import render_motion_video

        render_motion_video(cfg, duration, st["fmt"], stage / "silent.mp4")
    else:
        vb.build_broll_silent(cfg, assets, duration, words, st["fmt"], stage, "silent.mp4")


def _step_compose(cfg: Config, stage: Path, st: dict) -> None:
    """Add captions + audio. mascot → also overlay the owl onto the b-roll."""
    from . import video_builder as vb

    ass = (stage / st["captions"]) if st.get("captions") else None
    vo = stage / "voiceover.mp3"
    out = stage / "video.mp4"

    if _effective_mode(cfg, st["fmt"]) == "mascot":
        vb.compose_overlay(cfg, stage / "bg.mp4", stage / "owl.mov", vo, ass, out, stage, base_dir())
    else:
        vb.finalize(cfg, stage / "silent.mp4", vo, ass, out, stage, base_dir())
    st["video"] = "video.mp4"


def _step_upload(cfg: Config, stage: Path, st: dict) -> None:
    if not cfg.get("youtube.enabled", False):
        st["upload_skipped"] = "youtube.enabled=false"
        return
    from .youtube_uploader import upload_video

    sc = st["script"]
    privacy_override = st.get("overrides", {}).get("privacy")
    vid = upload_video(
        cfg, stage / "video.mp4", sc["title"], sc["description"], sc["tags"],
        privacy_override=privacy_override,
    )
    st["youtube_id"] = vid
    st["youtube_url"] = f"https://youtu.be/{vid}"


STEP_FUNCS = {
    "script": _step_script,
    "voiceover": _step_voiceover,
    "captions": _step_captions,
    "assets": _step_assets,
    "render": _step_render,
    "compose": _step_compose,
    "upload": _step_upload,
}


# --------------------------------------------------------------------------- #
# runner
# --------------------------------------------------------------------------- #
def _skip_upload(st: dict) -> bool:
    return bool(st.get("no_upload")) and "upload" not in st.get("done", [])


def _pending_steps(st: dict) -> list[str]:
    skip = _skip_upload(st)
    return [s for s in st["target_steps"] if s not in st["done"] and not (s == "upload" and skip)]


def _is_satisfied(st: dict) -> bool:
    skip = _skip_upload(st)
    return all(s in st["done"] for s in st["target_steps"] if not (s == "upload" and skip))


def run_stage(
    cfg: Config,
    stage: Path,
    *,
    one_step: bool,
    notifier=None,
    force_upload: bool = False,
    privacy_override: str | None = None,
) -> dict:
    """Execute pending steps for a stage. If one_step, do exactly one then return.

    force_upload reopens a stage that previously finished with no_upload=True
    (its "upload" step was skipped, not failed) so the upload step now runs —
    used by the approval-queue flow after a human approves a rendered draft.
    privacy_override lets that approval choose a privacy status different from
    whatever (if anything) was set when the draft was originally generated.
    Returns the (updated) state dict. Sends notifications on terminal events.
    """
    st = load_state(stage)
    dirty = False
    if force_upload and st.get("no_upload"):
        st["no_upload"] = False
        st["complete"] = False
        dirty = True
    if privacy_override:
        st.setdefault("overrides", {})["privacy"] = privacy_override
        dirty = True
    if dirty:
        save_state(stage, st)

    pending = _pending_steps(st)

    for step in pending:
        print(f"  → step: {step}")
        try:
            STEP_FUNCS[step](cfg, stage, st)
            st["done"].append(step)
            st["error"] = None
            save_state(stage, st)
        except Exception as exc:  # noqa: BLE001
            st["error"] = str(exc)
            save_state(stage, st)
            print(f"  !! step '{step}' failed: {exc}")
            if notifier is not None:
                notifier.notify_failure(st["fmt"], str(exc))
            raise
        if one_step:
            break

    # Completion handling
    if _is_satisfied(st) and not st.get("complete"):
        st["complete"] = True
        save_state(stage, st)
        if not st.get("topic_recorded"):
            topics.record_topic(st.get("title", "untitled"), st["fmt"])
            st["topic_recorded"] = True
            save_state(stage, st)
        if notifier is not None:
            notifier.notify_success(
                st["fmt"], st.get("title", ""), st.get("youtube_url")
            )
        print("  ✓ stage complete")
        _cleanup(cfg, stage, st)
    return st


def _cleanup(cfg: Config, stage: Path, st: dict) -> None:
    """After a successful upload, delete this run's local files; also prune old runs."""
    import shutil

    if st.get("youtube_id") and cfg.get("output.delete_after_upload", False):
        shutil.rmtree(stage, ignore_errors=True)
        print(f"  🧹 removed local renders for {stage.name} (already on YouTube)")
    _prune_old_stages(cfg, keep=stage)


def _prune_old_stages(cfg: Config, keep: Path | None = None) -> None:
    """Delete leftover stage folders older than output.keep_days (failed/no-upload runs)."""
    import shutil
    import time

    root = _out_root(cfg)
    if not root.exists():
        return
    days = cfg.get("output.keep_days", 3)
    cutoff = time.time() - days * 86400
    for d in root.iterdir():
        # Only touch timestamped stage dirs (e.g. 20260628_144312_short); leave others alone.
        if not d.is_dir() or d == keep or not d.name[:8].isdigit():
            continue
        try:
            if d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        except FileNotFoundError:
            pass


def remaining_steps(stage: Path) -> list[str]:
    st = load_state(stage)
    return _pending_steps(st)
