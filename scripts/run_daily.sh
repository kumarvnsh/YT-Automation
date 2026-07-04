#!/usr/bin/env bash
# Daily runner. Point your scheduler (cron / launchd / GitHub Actions) at this.
#
# Example cron (every day at 09:00):
#   0 9 * * * /Users/vnshkumar/Documents/YT-Automation/scripts/run_daily.sh >> \
#             /Users/vnshkumar/Documents/YT-Automation/logs/cron.log 2>&1
set -euo pipefail

# Schedulers (launchd/cron) start with a minimal PATH. Add common locations so
# python3 and ffmpeg (incl. Homebrew on Apple Silicon / Intel) are found.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

# Resolve project root regardless of where the scheduler invokes us from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

mkdir -p logs

# Activate venv if present.
if [ -d ".venv" ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] starting daily run"

# Default: one Short every day. Change to --format both for short + long-form,
# or set a weekday check to only make long-form on, say, Sundays.
python -m src.main --format short

echo "[$(date '+%Y-%m-%d %H:%M:%S')] done"
