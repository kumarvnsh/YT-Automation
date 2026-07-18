#!/usr/bin/env bash
# Run one channel's daily Short from a scheduler-safe environment.
set -euo pipefail

usage() {
  echo "Usage: $0 <config-path> <morning|evening>" >&2
}

if [ "$#" -ne 2 ]; then
  usage
  exit 2
fi

CONFIG_PATH="${1:-}"
PUBLISH_SLOT="${2:-}"

case "$PUBLISH_SLOT" in
  morning|evening)
    ;;
  *)
    echo "ERROR: publish slot must be 'morning' or 'evening'." >&2
    usage
    exit 2
    ;;
esac

# Cron and launchd use a minimal PATH. Include standard and Homebrew locations.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

if [ -d ".venv" ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

export PUBLISH_SLOT
python -m src.main --config "$CONFIG_PATH" --format short
