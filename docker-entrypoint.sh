#!/bin/sh
set -e

CONFIG_FILE="/data/.openclaw/openclaw.json"

# Write default gateway config if it doesn't already exist on the volume.
# This ensures Railway (and similar platforms) get the trustedProxies and
# allowInsecureAuth settings needed to skip device pairing behind a reverse proxy.
if [ ! -f "$CONFIG_FILE" ]; then
  mkdir -p /data/.openclaw
  cat > "$CONFIG_FILE" <<'EOF'
{
  "gateway": {
    "trustedProxies": ["100.64.0.0/16"],
    "controlUi": {
      "allowInsecureAuth": true
    }
  }
}
EOF
  echo "[entrypoint] wrote default config to $CONFIG_FILE"
else
  echo "[entrypoint] config already exists at $CONFIG_FILE, skipping write"
fi

# Railway sets PORT env var for the expected listening port (usually 8080).
# The gateway uses OPENCLAW_GATEWAY_PORT instead, so bridge the two.
export OPENCLAW_GATEWAY_PORT="${OPENCLAW_GATEWAY_PORT:-${PORT:-8080}}"

exec node openclaw.mjs gateway --allow-unconfigured --bind lan "$@"
