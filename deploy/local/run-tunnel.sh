#!/bin/zsh
# Public HTTPS address for the local server through a free tunnel without an account.
#   AIA_TUNNEL=serveo (default): SSH to serveo.net on port 443 — works where only 443 is open (VPNs, strict networks).
#                                Without an account the address is random and changes every 10–30 minutes. For a
#                                permanent https://<name>.serveo.net add ~/.ssh/aiarticle_tunnel.pub at
#                                console.serveo.net/ssh/keys, reserve <name> in the console and put the name into
#                                $DATA_DIR/tunnel_name.txt (or AIA_TUNNEL_NAME).
#   AIA_TUNNEL=cloudflare:       Cloudflare quick tunnel (needs outbound 7844; the address changes on every restart).
# The current address is written to $DATA_DIR/public_url.txt.
set -uo pipefail
DATA_DIR="${AIA_DATA_DIR:-$HOME/ai-article-data}"
PORT="${AIA_PORT:-8765}"
KEY="${AIA_TUNNEL_KEY:-$HOME/.ssh/aiarticle_tunnel}"
record() {
  while IFS= read -r line; do
    line=$(print -r -- "$line" | sed 's/\x1b\[[0-9;]*m//g')
    print -r -- "$line"
    url=$(print -r -- "$line" | grep -oE 'https://[a-z0-9-]+\.(trycloudflare\.com|serveousercontent\.com|serveo\.net)' || true)
    [ -n "$url" ] && print -r -- "$url" > "$DATA_DIR/public_url.txt"
  done
}
rm -f "$DATA_DIR/public_url.txt"
if [ "${AIA_TUNNEL:-serveo}" = "cloudflare" ]; then
  "${AIA_CLOUDFLARED:-$HOME/.local/bin/cloudflared}" tunnel --no-autoupdate --url "http://127.0.0.1:$PORT" 2>&1 | record
else
  [ -f "$KEY" ] || ssh-keygen -q -t ed25519 -N "" -C "ai-article tunnel" -f "$KEY"
  NAME="${AIA_TUNNEL_NAME:-$(cat "$DATA_DIR/tunnel_name.txt" 2>/dev/null | tr -d '[:space:]')}"
  ssh -T -p 443 -i "$KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 \
      -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -o ConnectTimeout=20 \
      -R "${NAME:+$NAME:}80:127.0.0.1:$PORT" serveo.net 2>&1 | record
  sleep 5  # a refused reserved name must not turn into a tight reconnect loop
fi
# launchd (KeepAlive) restarts the script when the tunnel drops
