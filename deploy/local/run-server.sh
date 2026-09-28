#!/bin/zsh
# AI Article server for on-premise use on macOS (started by launchd, see install.sh).
set -euo pipefail
APP_DIR="${AIA_APP_DIR:-$HOME/ai-article}"
DATA_DIR="${AIA_DATA_DIR:-$HOME/ai-article-data}"
PORT="${AIA_PORT:-8765}"
[ -f "$DATA_DIR/.env" ] && { set -a; . "$DATA_DIR/.env"; set +a; }
export AI_ARTICLE_DATA_DIR="$DATA_DIR"
export AI_ARTICLE_SETUP_CODE_FILE="$DATA_DIR/setup_code.txt"
export AI_ARTICLE_REGISTRATION="${AI_ARTICLE_REGISTRATION:-closed}"
export MPLCONFIGDIR="$DATA_DIR/.cache/matplotlib"
exec "$APP_DIR/.venv/bin/python" -m uvicorn app.main:app --app-dir "$APP_DIR/backend" \
  --host 127.0.0.1 --port "$PORT" --proxy-headers --forwarded-allow-ips 127.0.0.1
