FROM node:22-bookworm-slim

ENV NODE_ENV=production \
    PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true \
    WECHATY_LOG=error \
    AGENTCALL_WECHAT_TOKEN_FILE=/run/agentcall/agentcall-wechat-token

RUN apt-get update && apt-get install -y --no-install-recommends chromium ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 agentcall \
    && useradd --uid 10001 --gid 10001 --home-dir /data --no-create-home agentcall \
    && mkdir /data \
    && chown agentcall:agentcall /data \
    && chmod 700 /data

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund
COPY --chown=agentcall:agentcall service.cjs bridge-compat.cjs uos-compat.cjs LICENSE.wechaty ./
USER agentcall
WORKDIR /data
HEALTHCHECK --interval=15s --timeout=3s --start-period=30s --retries=3 \
    CMD node -e "const s=require('net').connect(8788,'127.0.0.1');s.on('connect',()=>{s.end();process.exit(0)});s.on('error',()=>process.exit(1));setTimeout(()=>process.exit(1),2000)"
CMD ["node", "/app/service.cjs"]
