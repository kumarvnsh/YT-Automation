#!/bin/bash
# Laptop-side topic strategist (launchd: com.histold.strategist). Refreshes
# data/strategy.json in its own detached master worktree, so the main checkout
# and its branches are never touched, then pushes it for the publish runs to read.
set -euo pipefail
REPO=/Users/vnshkumar/Documents/YT-Automation
WT="$HOME/.histold/strategist"
PY="$REPO/.venv/bin/python"

git -C "$REPO" fetch -q origin
[ -d "$WT" ] || { mkdir -p "$(dirname "$WT")"; git -C "$REPO" worktree add -q --detach "$WT" origin/master; }
cd "$WT"
git checkout -q --detach origin/master
"$PY" scripts/strategist.py
git add data/strategy.json
git diff --cached --quiet && exit 0
git commit -q -m "chore: refresh topic strategy [skip ci]"
for _ in 1 2 3; do   # bots push to master hourly; rebase and retry on a race
  git push -q origin HEAD:master && exit 0
  git fetch -q origin && git rebase -q origin/master
done
exit 1
