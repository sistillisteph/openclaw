FROM node:22-bookworm

# Install Bun (required for build scripts)
RUN curl -fsSL https://bun.sh/install | bash
ENV PATH="/root/.bun/bin:${PATH}"

RUN corepack enable

WORKDIR /app

ARG OPENCLAW_DOCKER_APT_PACKAGES=""
RUN if [ -n "$OPENCLAW_DOCKER_APT_PACKAGES" ]; then \
      apt-get update && \
      DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends $OPENCLAW_DOCKER_APT_PACKAGES && \
      apt-get clean && \
      rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*; \
    fi

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml .npmrc ./
COPY ui/package.json ./ui/package.json
COPY patches ./patches
COPY scripts ./scripts

RUN pnpm install --frozen-lockfile

# Optionally install Chromium and Xvfb for browser automation.
# Build with: docker build --build-arg OPENCLAW_INSTALL_BROWSER=1 ...
# Adds ~300MB but eliminates the 60-90s Playwright install on every container start.
# Must run after pnpm install so playwright-core is available in node_modules.
ARG OPENCLAW_INSTALL_BROWSER=""
RUN if [ -n "$OPENCLAW_INSTALL_BROWSER" ]; then \
      apt-get update && \
      DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends xvfb && \
      node /app/node_modules/playwright-core/cli.js install --with-deps chromium && \
      apt-get clean && \
      rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*; \
    fi

COPY . .
RUN pnpm build
# Force pnpm for UI build (Bun may fail on ARM/Synology architectures)
ENV OPENCLAW_PREFER_PNPM=1
RUN pnpm ui:build

ENV NODE_ENV=production

# Allow non-root user to write temp files during runtime/tests.
RUN chown -R node:node /app

RUN mkdir -p /data/.openclaw /data/.clawdbot /data/workspace && chown -R node:node /data

# Write default Railway/reverse-proxy config into the image.
# trustedProxies: trust Railway's internal proxy network so client IPs are resolved correctly.
# allowInsecureAuth: allow token-only auth to skip device pairing over HTTP
#   (Railway terminates TLS at the edge, so the container sees HTTP).
RUN echo '{"gateway":{"trustedProxies":["100.64.0.0/10"],"controlUi":{"allowInsecureAuth":true}}}' \
  > /data/.openclaw/openclaw.json

# Tell the gateway to read config/state from /data/.openclaw (the persistent volume).
# Without this it defaults to ~/.openclaw which is the root user's homedir.
ENV OPENCLAW_STATE_DIR=/data/.openclaw

COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# Security hardening: Run as non-root user
# The node:22-bookworm image includes a 'node' user (uid 1000)
# This reduces the attack surface by preventing container escape via root privileges
# Start gateway server via entrypoint script.
# The entrypoint writes config to the volume if needed, then starts the gateway
# bound to all interfaces.
#
# Required env var for auth: OPENCLAW_GATEWAY_TOKEN or OPENCLAW_GATEWAY_PASSWORD
ENTRYPOINT ["/app/docker-entrypoint.sh"]
