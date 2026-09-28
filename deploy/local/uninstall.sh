#!/bin/zsh
# Stop and remove the launchd services (data in ~/ai-article-data is kept).
for name in server tunnel; do
  launchctl bootout "gui/$(id -u)/org.aiarticle.$name" 2>/dev/null || true
  rm -f "$HOME/Library/LaunchAgents/org.aiarticle.$name.plist"
done
echo "stopped"
