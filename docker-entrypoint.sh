#!/bin/sh
set -e

CONFIG_FILE="/data/.openclaw/openclaw.json"

# Always rewrite config on startup to pick up the latest env vars.
# Uses TELEGRAM_BOT_TOKEN env var if set; otherwise Telegram is left unconfigured.
mkdir -p /data/.openclaw

if [ -n "$TELEGRAM_BOT_TOKEN" ]; then
  # Build allowFrom array from TELEGRAM_ALLOW_FROM (comma-separated user IDs).
  # If not set, falls back to pairing mode.
  if [ -n "$TELEGRAM_ALLOW_FROM" ]; then
    # Convert comma-separated IDs to JSON array: "123,456" -> [123, 456]
    ALLOW_JSON=$(echo "$TELEGRAM_ALLOW_FROM" | sed 's/[[:space:]]//g' | awk -F',' '{
      printf "["
      for (i=1; i<=NF; i++) {
        if (i>1) printf ", "
        printf "%s", $i
      }
      printf "]"
    }')
    DM_POLICY="allowlist"
    ALLOW_LINE="\"allowFrom\": ${ALLOW_JSON},"
  else
    DM_POLICY="pairing"
    ALLOW_LINE=""
  fi

  cat > "$CONFIG_FILE" <<EOF
{
  "gateway": {
    "trustedProxies": ["100.64.0.0/10"],
    "controlUi": {
      "allowInsecureAuth": true
    }
  },
  "channels": {
    "telegram": {
      "enabled": true,
      "botToken": "${TELEGRAM_BOT_TOKEN}",
      "dmPolicy": "${DM_POLICY}",
      ${ALLOW_LINE}
      "groups": {
        "*": { "requireMention": true }
      }
    }
  }
}
EOF
  echo "[entrypoint] wrote config with Telegram enabled (dmPolicy=${DM_POLICY})"
else
  cat > "$CONFIG_FILE" <<'EOF'
{
  "gateway": {
    "trustedProxies": ["100.64.0.0/10"],
    "controlUi": {
      "allowInsecureAuth": true
    }
  }
}
EOF
  echo "[entrypoint] wrote config (no TELEGRAM_BOT_TOKEN set, Telegram disabled)"
fi

# Point the gateway at the config on the persistent volume.
export OPENCLAW_STATE_DIR="${OPENCLAW_STATE_DIR:-/data/.openclaw}"

# Railway sets PORT env var for the expected listening port.
# The gateway uses OPENCLAW_GATEWAY_PORT instead, so bridge the two.
export OPENCLAW_GATEWAY_PORT="${OPENCLAW_GATEWAY_PORT:-${PORT:-8080}}"

# If CODEX_AUTH_JSON is set, write it to ~/.codex/auth.json so the Codex CLI
# can authenticate with the stored OAuth token.
if [ -n "$CODEX_AUTH_JSON" ]; then
  mkdir -p "$HOME/.codex"
  echo "$CODEX_AUTH_JSON" > "$HOME/.codex/auth.json"
  chmod 600 "$HOME/.codex/auth.json"
  # Default to Codex model when no explicit DEFAULT_MODEL is set.
  : "${DEFAULT_MODEL:=openai-codex/gpt-5.3-codex}"
  echo "[entrypoint] wrote Codex auth.json from CODEX_AUTH_JSON env var"
fi

# If DEFAULT_MODEL is set (explicitly or via Codex above), patch the config.
if [ -n "$DEFAULT_MODEL" ]; then
  node -e "
    const fs = require('fs');
    const p = '$CONFIG_FILE';
    const cfg = JSON.parse(fs.readFileSync(p, 'utf8'));
    cfg.agents = cfg.agents || {};
    cfg.agents.defaults = cfg.agents.defaults || {};
    cfg.agents.defaults.model = { primary: '$DEFAULT_MODEL' };
    fs.writeFileSync(p, JSON.stringify(cfg, null, 2) + '\n');
  "
  echo "[entrypoint] default model set to $DEFAULT_MODEL"
fi

exec node openclaw.mjs gateway --allow-unconfigured --bind lan "$@"
