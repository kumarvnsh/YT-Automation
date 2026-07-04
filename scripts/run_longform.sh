#!/usr/bin/env bash
# Manually-run weekly long-form generator for Histold.
# Just run this on your Mac:  bash scripts/run_longform.sh
# (No 45s cap locally, so the full ~7-min render completes in one go.)
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

mkdir -p logs

# Use the project venv if present.
if [ -d ".venv" ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] starting weekly LONG-FORM run"
# --resume continues an incomplete run if one exists (e.g. after an error),
# otherwise it starts a fresh one. Avoids re-generating work on retry.
python -m src.main --format long --resume 2>&1 | tee -a logs/longform.log
echo "[$(date '+%Y-%m-%d %H:%M:%S')] done"
