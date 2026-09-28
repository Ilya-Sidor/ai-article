#!/bin/zsh
# Public HTTPS address via a Cloudflare quick tunnel (free, no account). The current
# address is written to $DATA_DIR/public_url.txt; it changes when the tunnel restarts.
set -uo pipefail
DATA_DIR="${AIA_DATA_DIR:-$HOME/ai-article-data}"
PORT="${AIA_PORT:-8765}"
CLOUDFLARED="${AIA_CLOUDFLARED:-$HOME/.local/bin/cloudflared}"
rm -f "$DATA_DIR/public_url.txt"
"$CLOUDFLARED" tunnel --no-autoupdate --url "http://127.0.0.1:$PORT" 2>&1 | while IFS= read -r line; do
  print -r -- "$line"
  url=$(print -r -- "$line" | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' || true)
  [ -n "$url" ] && print -r -- "$url" > "$DATA_DIR/public_url.txt"
done
