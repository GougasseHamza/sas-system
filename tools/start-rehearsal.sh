#!/usr/bin/env bash
set -euo pipefail

# One-command launcher for the owned rehearsal lab.
#   ./tools/start-rehearsal.sh defender
#   ./tools/start-rehearsal.sh attacker sami

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

role="${1:-defender}"
workers="${SAS_WORKERS:-10}"

case "$role" in
  defender|blue)
    exec uv run --no-sync python lab/onlychess/rehearsal_defender.py \
      --workers "$workers"
    ;;
  attacker|red)
    name="${2:-}"
    if [[ -z "$name" ]]; then
      echo "usage: $0 attacker <team-name>" >&2
      exit 2
    fi
    export SAS_RUN_ID="${SAS_RUN_ID:-$(date +%s)}"
    # The attacker launcher owns the cell/worker loop. Run it beside the same
    # database-backed TUI so Sami can watch and steer the red agents too.
    mkdir -p .arena
    uv run --no-sync python lab/onlychess/rehearsal_attacker.py \
      --name "$name" >".arena/${name}-attacker-launcher.log" 2>&1 &
    runner_pid=$!
    cleanup() {
      kill "$runner_pid" 2>/dev/null || true
      wait "$runner_pid" 2>/dev/null || true
    }
    trap cleanup EXIT INT TERM
    uv run --no-sync sas-arena \
      --config config/arena.onlychess-attacker.toml \
      tui events/sas2025-onlychess-remote/event.toml
    ;;
  *)
    echo "usage: $0 [defender|attacker NAME]" >&2
    exit 2
    ;;
esac
