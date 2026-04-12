#!/bin/bash
# Gemia autonomous loop — runs as cron job every 2 hours
# Checks agent_log.md; if idle >1.5h, kicks off next pending task via codex

set -e
GEMIA_DIR="/Users/xiehaibo/Code/gemia"
LOG="$GEMIA_DIR/agent_log.md"
HUMAN_FILE="$GEMIA_DIR/HUMAN_NEEDED.md"
LOCK="/tmp/gemia_loop.lock"
ACPX_CODEX="$GEMIA_DIR/acpx-codex.sh"

# Singleton guard
if [ -f "$LOCK" ]; then
  pid=$(cat "$LOCK")
  if kill -0 "$pid" 2>/dev/null; then
    echo "[$(date)] Loop already running (pid $pid), skipping." >> /tmp/gemia_cron.log
    exit 0
  fi
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a /tmp/gemia_cron.log; }

# Check last log entry time
LAST_TS=$(grep -Eo '[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}' "$LOG" 2>/dev/null | tail -1)
if [ -n "$LAST_TS" ]; then
  LAST_EPOCH=$(date -j -f "%Y-%m-%d %H:%M" "$LAST_TS" "+%s" 2>/dev/null || date -d "$LAST_TS" "+%s" 2>/dev/null || echo 0)
  NOW_EPOCH=$(date "+%s")
  IDLE_MIN=$(( (NOW_EPOCH - LAST_EPOCH) / 60 ))
  if [ "$IDLE_MIN" -lt 90 ]; then
    log "Last activity ${IDLE_MIN}m ago (<90m). Skipping."
    exit 0
  fi
  log "Idle for ${IDLE_MIN}m — kicking loop forward."
else
  log "No timestamp found in log — starting fresh loop."
fi

# Ask Claude Code (via acpx) to continue the loop
PROMPT="[GEMIA AUTO-LOOP] Check /Users/xiehaibo/Code/gemia/agent_log.md.
Find the next pending task in the 41-function list.
Use codex (via /Users/xiehaibo/Code/gemia/acpx-codex.sh) to implement it.
Verify it passes twice. Update agent_log.md. Continue to next task.
Follow all circuit-breaker rules from the project README.
Commit every 5 completed functions."

export OPENAI_BASE_URL="https://openrouter.ai/api/v1"
export OPENAI_API_KEY="${OPENROUTER_API_KEY:-$(grep OPENROUTER_API_KEY ~/.zshrc | head -1 | sed 's/.*=//' | tr -d '"')}"
export http_proxy="" https_proxy="" HTTP_PROXY="" HTTPS_PROXY=""

log "Sending prompt to codex agent..."
"$ACPX_CODEX" --format json --json-strict codex exec "$PROMPT" >> /tmp/gemia_cron.log 2>&1 || true

log "Loop iteration complete."
echo "$(date '+%Y-%m-%d %H:%M:%S') auto-loop triggered" >> "$LOG"
