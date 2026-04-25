#!/bin/bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
RUNTIME_ROOT="$HOME/.gemia/automation"
LOG_DIR="$RUNTIME_ROOT/logs"
PLIST_PATH="$HOME/Library/LaunchAgents/com.gemia.five-day-loop.plist"
OUT_LOG="$LOG_DIR/launchd-supervisor.out.log"
ERR_LOG="$LOG_DIR/launchd-supervisor.err.log"
LABEL="com.gemia.five-day-loop"
UID_VALUE="$(id -u)"

mkdir -p "$LOG_DIR"
mkdir -p "$HOME/Library/LaunchAgents"

cat >"$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
      <string>/bin/bash</string>
      <string>${DIR}/_run_gemia_controller.sh</string>
      <string>run-supervisor</string>
      <string>--duration-days</string>
      <string>5</string>
      <string>--poll-sec</string>
      <string>300</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/xiehaibo/Code/gemia</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${OUT_LOG}</string>
    <key>StandardErrorPath</key>
    <string>${ERR_LOG}</string>
    <key>EnvironmentVariables</key>
    <dict>
      <key>PATH</key>
      <string>/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
      <key>PYTHONPATH</key>
      <string>/Users/xiehaibo/Code/gemia</string>
      <key>NO_PROXY</key>
      <string>*</string>
      <key>no_proxy</key>
      <string>*</string>
    </dict>
  </dict>
</plist>
PLIST

launchctl bootout "gui/${UID_VALUE}/${LABEL}" >/dev/null 2>&1 || true
launchctl bootstrap "gui/${UID_VALUE}" "$PLIST_PATH"
launchctl enable "gui/${UID_VALUE}/${LABEL}"
launchctl kickstart -k "gui/${UID_VALUE}/${LABEL}"

echo "Started Gemia five-day supervisor via launchd: ${LABEL}"
echo "stdout: ${OUT_LOG}"
echo "stderr: ${ERR_LOG}"
