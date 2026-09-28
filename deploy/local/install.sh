#!/bin/zsh
# Install AI Article as two per-user launchd services (server + tunnel) on macOS.
# Prerequisites: the repository cloned to $APP_DIR with .venv, cloudflared in ~/.local/bin.
set -euo pipefail
APP_DIR="${AIA_APP_DIR:-$HOME/ai-article}"
DATA_DIR="${AIA_DATA_DIR:-$HOME/ai-article-data}"
AGENTS="$HOME/Library/LaunchAgents"
mkdir -p "$DATA_DIR/logs" "$AGENTS"
chmod 700 "$DATA_DIR"
if [ ! -f "$DATA_DIR/setup_code.txt" ]; then
  python3 -c "import secrets; print(secrets.token_urlsafe(12))" > "$DATA_DIR/setup_code.txt"
  chmod 600 "$DATA_DIR/setup_code.txt"
fi
for name in server tunnel; do
  label="org.aiarticle.$name"
  cat > "$AGENTS/$label.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key><array><string>/bin/zsh</string><string>$APP_DIR/deploy/local/run-$name.sh</string></array>
  <key>EnvironmentVariables</key><dict><key>AIA_APP_DIR</key><string>$APP_DIR</string><key>AIA_DATA_DIR</key><string>$DATA_DIR</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$DATA_DIR/logs/$name.log</string>
  <key>StandardErrorPath</key><string>$DATA_DIR/logs/$name.log</string>
</dict></plist>
PLIST
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$AGENTS/$label.plist"
done
echo "installed; public address will appear in $DATA_DIR/public_url.txt"
