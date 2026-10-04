const assert = require('node:assert/strict');
const { test } = require('node:test');
const { EventEmitter } = require('node:events');
const { installBridgeCompatibility } = require('../docker/wechat/bridge-compat.cjs');

test('login long polling does not prevent bridge initialization or cookie restoration', async () => {
  const cookies = [{ name: 'session', value: 'test-only' }];
  let restored = false, ready = false;
  class Bridge extends EventEmitter {
    constructor() { super(); this.options = { memory: { get: async () => cookies } }; }
    async uosPatch() {}
    onDialog() {}
    entryUrl() { return 'https://wx.qq.com'; }
  }
  const page = new EventEmitter();
  page.goto = async (_, options) => {
    if (options.waitUntil !== 'domcontentloaded') throw Error('login poll prevents load');
  };
  page.setCookie = async (...value) => { assert.deepEqual(value, cookies); restored = true; };
  page.reload = async options => {
    assert.ok(restored);
    if (options.waitUntil !== 'domcontentloaded') throw Error('login poll prevents load');
    page.emit('domcontentloaded'); // The page intentionally never emits load.
  };
  installBridgeCompatibility(Bridge, 'session');
  const bridge = new Bridge();
  bridge.on('load', value => { assert.equal(value, page); ready = true; });
  await bridge.initPage({ newPage: async () => page });
  assert.ok(ready);
});
