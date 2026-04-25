#!/bin/bash
set -euo pipefail

LABEL="com.gemia.five-day-loop"
UID_VALUE="$(id -u)"
PLIST_PATH="$HOME/Library/LaunchAgents/${LABEL}.plist"

launchctl bootout "gui/${UID_VALUE}/${LABEL}" >/dev/null 2>&1 || true
rm -f "$PLIST_PATH"
echo "Stopped ${LABEL}"
