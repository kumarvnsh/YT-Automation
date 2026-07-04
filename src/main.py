"""Orchestrator CLI for the checkpointed, resumable pipeline.

Common usage:
  python -m src.main --format short                # full run (all steps), upload per config
  python -m src.main --format short --no-upload    # render only
  python -m src.main --format short --dry-run      # script only
  python -m src.main --resume                      # finish the newest incomplete run
  python -m src.main --step                        # do ONE pending step then exit
                                                   #   (scheduler calls this repeatedly)

Step/resume modes are what make this survive a time-capped, no-background
environment: each step persists to the stage dir, so progress is never lost.

Each run lives in output/<timestamp>_<fmt>/ with state.json + all artifacts.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys

from .config import load_config, Config
from . import pipeline


def _resolve_stage(cfg: Config, args):
    """Pick the stage to work on for this invocation."""
    if args.stage:
        stage = pipeline.stage_path(cfg, args.stage)
        if not pipeline.state_path(stage).exists():
            print(f"ERROR: no such stage: {stage}", file=sys.stderr)
            raise SystemExit(2)
        print(f"Targeting explicit stage: {stage.name}")
        return stage
    if args.resume or args.step:
        stage = pipeline.active_stage(cfg, args.format if args.format != "both" else None)
        if stage is not None:
            print(f"Resuming stage: {stage.name}")
            return stage
        print("No incomplete stage found — starting a new one.")
    fmt = "short" if args.format == "both" else args.format
    return pipeline.new_stage(
        cfg, fmt, no_upload=args.no_upload, dry_run=args.dry_run,
        topic=args.topic, privacy=args.privacy,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Histold automated history-video pipeline")
    parser.add_argument("--format", choices=["short", "long", "both"], default="short")
    parser.add_argument("--no-upload", action="store_true", help="Render but don't upload.")
    parser.add_argument("--dry-run", action="store_true", help="Script + metadata only.")
    parser.add_argument("--resume", action="store_true",
                        help="Finish the newest incomplete run instead of starting fresh.")
    parser.add_argument("--step", action="store_true",
                        help="Execute exactly ONE pending step then exit (for time-capped schedulers).")
    parser.add_argument("--config", default=None)
    parser.add_argument("--topic", default=None,
                        help="Override today's topic direction (skips trend/angle logic).")
    parser.add_argument("--privacy", choices=["private", "unlisted", "public"], default=None,
                        help="Override youtube.privacy_status for this run.")
    parser.add_argument("--stage", default=None,
                        help="Explicit stage dir name under output/ to target, bypassing "
                             "active-stage lookup (used to resume a specific --no-upload run).")
    parser.add_argument("--force-upload", action="store_true",
                        help="With --resume/--stage, upload a stage that previously finished "
                             "with --no-upload (approval-queue flow).")
    args = parser.parse_args(argv)

    if not shutil.which("ffmpeg") and not args.dry_run:
        print("ERROR: ffmpeg not found on PATH (brew install ffmpeg).", file=sys.stderr)
        return 2

    cfg = load_config(args.config)
    from .notify import Notifier

    notifier = Notifier(cfg)

    # "both" only makes sense for a full (non-step) run; handle as two stages.
    formats = ["short", "long"] if (args.format == "both" and not (args.step or args.resume)) else None

    try:
        if formats:
            results = []
            for fmt in formats:
                stage = pipeline.new_stage(
                    cfg, fmt, no_upload=args.no_upload, dry_run=args.dry_run,
                    topic=args.topic, privacy=args.privacy,
                )
                st = pipeline.run_stage(cfg, stage, one_step=False, notifier=notifier,
                                         force_upload=args.force_upload)
                results.append(_summary(stage, st))
            print("\n=== Summary ===")
            print(json.dumps(results, indent=2))
            return 0

        stage = _resolve_stage(cfg, args)
        st = pipeline.run_stage(cfg, stage, one_step=args.step, notifier=notifier,
                                 force_upload=args.force_upload)
        result = _summary(stage, st)

        # Clear status line for schedulers to parse.
        if st.get("complete"):
            print(f"\nSTATUS: DONE  {result.get('youtube_url') or result.get('video') or ''}")
        elif args.step:
            left = pipeline.remaining_steps(stage)
            print(f"\nSTATUS: MORE  remaining={','.join(left)}")
        print("\n=== Summary ===")
        print(json.dumps(result, indent=2))
        return 0

    except Exception as exc:  # noqa: BLE001
        print(f"STATUS: ERROR  {exc}", file=sys.stderr)
        return 1


def _summary(stage, st: dict) -> dict:
    return {
        "stage": stage.name,
        "format": st.get("fmt"),
        "title": st.get("title"),
        "complete": st.get("complete", False),
        "done": st.get("done", []),
        "video": st.get("video"),
        "youtube_url": st.get("youtube_url"),
        "error": st.get("error"),
    }


if __name__ == "__main__":
    raise SystemExit(main())
