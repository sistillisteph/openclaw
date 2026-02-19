#!/bin/sh
set -e

CONFIG_FILE="/data/.openclaw/openclaw.json"

# Write default gateway config if missing or if it lacks trustedProxies.
# This ensures Railway (and similar platforms) get the trustedProxies and
# allowInsecureAuth settings needed to skip device pairing behind a reverse proxy.
NEEDS_WRITE=false
if [ ! -f "$CONFIG_FILE" ]; then
  NEEDS_WRITE=true
elif ! grep -q "trustedProxies" "$CONFIG_FILE" 2>/dev/null; then
  echo "[entrypoint] existing config missing trustedProxies, overwriting"
  NEEDS_WRITE=true
fi

if [ "$NEEDS_WRITE" = "true" ]; then
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
  echo "[entrypoint] config already exists at $CONFIG_FILE with trustedProxies, skipping write"
fi

# Point the gateway at the config on the persistent volume.
# Without this it looks in ~/.openclaw/ (root's homedir) and misses our config.
export OPENCLAW_STATE_DIR="${OPENCLAW_STATE_DIR:-/data/.openclaw}"

# Railway sets PORT env var for the expected listening port (usually 8080).
# The gateway uses OPENCLAW_GATEWAY_PORT instead, so bridge the two.
export OPENCLAW_GATEWAY_PORT="${OPENCLAW_GATEWAY_PORT:-${PORT:-8080}}"

exec node openclaw.mjs gateway --allow-unconfigured --bind lan "$@"
