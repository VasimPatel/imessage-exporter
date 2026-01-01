#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname)" != "Darwin" ]]; then
  echo "This installer is intended for macOS LaunchAgents." >&2
  exit 1
fi

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
PLIST_PATH="${HOME}/Library/LaunchAgents/com.user.messagesync.plist"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"

mkdir -p "${HOME}/Library/LaunchAgents"
mkdir -p "${PROJECT_ROOT}/logs"

cat > "${PLIST_PATH}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.user.messagesync</string>
    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON_BIN}</string>
        <string>${PROJECT_ROOT}/message_sync.py</string>
        <string>start</string>
        <string>--daemon</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${PROJECT_ROOT}</string>
    <key>KeepAlive</key>
    <true/>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${PROJECT_ROOT}/logs/launchagent.out.log</string>
    <key>StandardErrorPath</key>
    <string>${PROJECT_ROOT}/logs/launchagent.err.log</string>
</dict>
</plist>
EOF

launchctl unload "${PLIST_PATH}" >/dev/null 2>&1 || true
launchctl load "${PLIST_PATH}"

echo "LaunchAgent installed to ${PLIST_PATH}"
echo "Logs will appear in ${PROJECT_ROOT}/logs/"
