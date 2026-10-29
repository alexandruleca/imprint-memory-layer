#!/usr/bin/env bash
# Install (or update) the disposable runner supervisor as a login LaunchAgent
# (ADR 0115). The scripts are copied outside the checkout so switching branches
# never changes what the agent runs. Pass --uninstall to remove it.
set -euo pipefail
# shellcheck source=common.sh
. "$(dirname "$0")/common.sh"

agent=tech.imprint.vm-runner
dest="$HOME/.local/share/github-runners/imprint-vm"
plist="$HOME/Library/LaunchAgents/$agent.plist"
logs="$HOME/Library/Logs/$agent"
domain="gui/$(id -u)"

launchctl bootout "$domain/$agent" 2>/dev/null || true
if [[ ${1:-} == --uninstall ]]; then
  rm -f "$plist"
  rm -rf "$dest"
  log "removed $agent"
  exit 0
fi

mkdir -p "$dest" "$logs" "$(dirname "$plist")"
install -m 0644 "$(dirname "$0")/common.sh" "$dest/common.sh"
install -m 0755 "$(dirname "$0")/supervise.sh" "$(dirname "$0")/prepare-image.sh" "$dest/"
cat >"$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$agent</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$dest/supervise.sh</string></array>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>$HOME/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>60</integer>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>$logs/supervise.log</string>
  <key>StandardErrorPath</key><string>$logs/supervise.log</string>
</dict>
</plist>
PLIST
launchctl bootstrap "$domain" "$plist"
log "started $agent; log: $logs/supervise.log"
