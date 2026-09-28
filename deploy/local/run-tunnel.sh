#!/bin/zsh
# Public HTTPS address for the local server through a free tunnel without an account.
#   AIA_TUNNEL=serveo (default): SSH to serveo.net on port 443 — works where only 443 is open (VPNs, strict networks);
#                                a dedicated key (~/.ssh/aiarticle_tunnel) keeps the address stable.
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
  ssh -T -p 443 -i "$KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 \
      -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -o ConnectTimeout=20 \
      -R "80:127.0.0.1:$PORT" serveo.net 2>&1 | record
fi
# launchd (KeepAlive) restarts the script when the tunnel drops
