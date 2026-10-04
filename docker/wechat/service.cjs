'use strict';

// The unencrypted listener is only for this Compose project's private bridge.
// External puppet endpoints are configured separately in the TLS-verifying client.
const fs = require('fs/promises');
const { setTimeout: delay } = require('timers/promises');
const { PuppetWeChat } = require('wechaty-puppet-wechat');
const { PuppetServer } = require('wechaty-puppet-service');
const { MemoryCard } = require('memory-card');
const { Bridge } = require('wechaty-puppet-wechat/dist/src/bridge.js');
const { MEMORY_SLOT } = require('wechaty-puppet-wechat/dist/src/config.js');
require('./bridge-compat.cjs').installBridgeCompatibility(Bridge, MEMORY_SLOT);
require('./uos-compat.cjs').installUosCompatibility(Bridge);

process.umask(0o077);

async function loadToken() {
  const path = process.env.AGENTCALL_WECHAT_TOKEN_FILE;
  for (let attempt = 0; attempt < 120; attempt++) {
    try {
      const token = (await fs.readFile(path, 'utf8')).trim();
      if (/^puppet_[a-f0-9]{48}$/.test(token)) return token;
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
    }
    await delay(1000);
  }
  throw new Error('Local transport credential was not initialized');
}

async function main() {
  if (process.env.AGENTCALL_WECHAT_LOCAL_ONLY !== '1') {
    throw new Error('This listener requires the private local deployment');
  }
  const token = await loadToken();
  const memory = new MemoryCard('/data/wechat-session');
  await memory.load();
  const puppet = new PuppetWeChat({ endpoint: '/usr/bin/chromium',
    launchOptions: { args: ['--disable-dev-shm-usage'] } });
  puppet.setMemory(memory);
  puppet.on('error', () => console.error('WeChat session error; see agentCall channel status.'));
  const server = new PuppetServer({ endpoint: '0.0.0.0:8788', token, puppet, tls: { disable: true } });
  await server.start();
  console.log('agentCall private WeChat bridge is ready.');

  let closing = false;
  async function shutdown() {
    if (closing) return;
    closing = true;
    const timeout = setTimeout(() => process.exit(1), 20000);
    timeout.unref();
    try {
      await puppet.stop();
      await server.stop();
      process.exit(0);
    } catch {
      process.exit(1);
    }
  }
  process.on('SIGTERM', shutdown);
  process.on('SIGINT', shutdown);
}

main().catch(() => {
  console.error('agentCall WeChat bridge could not start; verify its private volume and dependencies.');
  process.exit(1);
});
