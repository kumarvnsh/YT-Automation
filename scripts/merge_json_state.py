#!/usr/bin/env python3
"""Union-merge append-only JSON state files with a git ref before committing.

Usage:
  python scripts/merge_json_state.py [--ref origin/master]

Several workflows append to the same JSON lists (topic reservations, used
topics, approval queue, published index) and race each other: the plain
commit → pull --rebase → push flow dies on a content conflict when another
run pushed first. This script makes the working-tree copy a superset of the
ref's copy so the subsequent commit applies cleanly on top of the ref:

- entries only on the ref are kept (in their positions),
- entries only in the working tree are appended,
- entries present in both keep the working-tree version (this run may have
  mutated them, e.g. retitled_at / auto_dispatched_at / playlist_ids).

Files that are missing, unparsable, or not list-shaped are left untouched.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

# file -> keys that identify an entry
IDENTITY_KEYS = {
    "data/topic_reservations.json": ("job_id",),
    "data/used_topics.json": ("title", "date"),
    "data/pending_approvals.json": ("run_id", "stage_dir_name"),
    "data/published_index.json": ("video_id",),
    "channels/astrotold/data/topic_reservations.json": ("job_id",),
    "channels/astrotold/data/used_topics.json": ("title", "date"),
    "channels/astrotold/data/published_index.json": ("video_id",),
}


def _identity(entry: dict, keys: tuple[str, ...]) -> tuple:
    return tuple(str(entry.get(key)) for key in keys)


def _ref_version(ref: str, rel_path: str) -> list | None:
    proc = subprocess.run(
        ["git", "show", f"{ref}:{rel_path}"], capture_output=True, text=True
    )
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else None


def merge_lists(base: list, local: list, keys: tuple[str, ...]) -> list:
    """Union of base and local; local wins for entries present in both."""
    local_by_id = {
        _identity(entry, keys): entry for entry in local if isinstance(entry, dict)
    }
    merged, seen = [], set()
    for entry in base:
        ident = _identity(entry, keys) if isinstance(entry, dict) else None
        merged.append(local_by_id.get(ident, entry) if ident else entry)
        seen.add(ident)
    for entry in local:
        ident = _identity(entry, keys) if isinstance(entry, dict) else None
        if ident is None or ident not in seen:
            merged.append(entry)
            seen.add(ident)
    return merged


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="origin/master")
    args = parser.parse_args()

    for rel_path, keys in IDENTITY_KEYS.items():
        path = Path(rel_path)
        if not path.exists():
            continue
        try:
            local = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not isinstance(local, list):
            continue
        base = _ref_version(args.ref, rel_path)
        if base is None:
            continue
        merged = merge_lists(base, local, keys)
        if merged != local:
            path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
            print(
                f"merged {rel_path}: {len(local)} local ∪ {len(base)} "
                f"{args.ref} → {len(merged)}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
