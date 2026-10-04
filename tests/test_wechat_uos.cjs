const assert = require('node:assert/strict');
const { test } = require('node:test');
const { EventEmitter } = require('node:events');
const { createHash } = require('node:crypto');
const { installUosCompatibility } = require('../docker/wechat/uos-compat.cjs');

const tick = () => new Promise(resolve => setImmediate(resolve));
async function setup() {
  class Bridge extends EventEmitter {
    entryUrl(cookies) { return cookies.length ? 'https://web.wechat.com/?target=t' : 'https://wx.qq.com?target=t'; }
    uosPatch() { throw new Error('old interceptor must be replaced'); }
  }
  installUosCompatibility(Bridge);
  const bridge = new Bridge(), page = new EventEmitter();
  let interception = false;
  page.setRequestInterception = async enabled => { interception = enabled; };
  page.isClosed = () => false;
  bridge.page = page;
  await bridge.uosPatch(page);
  assert.equal(interception, true);
  assert.equal(page.listenerCount('request'), 1);
  for (const cookies of [[], [{}]]) {
    const url = new URL(bridge.entryUrl(cookies));
    assert.equal(url.searchParams.get('lang'), 'zh_CN');
    assert.deepEqual(url.searchParams.getAll('target'), ['t']);
    assert.equal(url.hostname, cookies.length ? 'web.wechat.com' : 'wx.qq.com');
  }
  return { bridge, page };
}

test('UOS uses the official 1.18.4 public parameter and preserves login headers', async () => {
  const { page } = await setup();
  for (const host of ['wx.qq.com', 'wx2.qq.com', 'wx8.qq.com', 'web.wechat.com', 'web2.wechat.com']) {
    const calls = [];
    page.emit('request', {
      url: () => `https://${host}/cgi-bin/mmwebwx-bin/webwxnewloginpage?ticket=test`,
      headers: () => ({ cookie: 'test-cookie', referer: 'https://wx.qq.com/?target=t' }),
      continue: async options => calls.push(options),
    });
    await tick();
    assert.equal(calls.length, 1);
    assert.equal(calls[0].headers.cookie, 'test-cookie');
    assert.equal(calls[0].headers.referer, 'https://wx.qq.com/?target=t');
    assert.equal(calls[0].headers['client-version'], '2.0.0');
    assert.equal(createHash('sha256').update(calls[0].headers.extspam).digest('hex'),
      '11e2f840f98652979f09996c2c9bcdfef4e19c0e042dfcb00804818a369795e0');
  }
});

test('UOS never modifies unrelated, insecure, or lookalike requests', async () => {
  const { page } = await setup();
  for (const url of ['https://example.com/cgi-bin/mmwebwx-bin/webwxnewloginpage',
    'https://wx.qq.com.evil.test/cgi-bin/mmwebwx-bin/webwxnewloginpage',
    'http://wx.qq.com/cgi-bin/mmwebwx-bin/webwxnewloginpage',
    'https://wx.qq.com:8443/cgi-bin/mmwebwx-bin/webwxnewloginpage',
    'https://wx.qq.com/cgi-bin/mmwebwx-bin/webwxsync', 'data:text/html,hello']) {
    const calls = [];
    page.emit('request', { url: () => url, continue: async options => calls.push(options) });
    await tick();
    assert.deepEqual(calls, [undefined]);
  }
});

test('interception errors are sanitized and obsolete page failures are ignored', async () => {
  const { bridge, page } = await setup();
  const errors = [];
  bridge.on('error', error => errors.push(error.message));
  const request = { url: () => 'https://wx.qq.com/',
    continue: async () => { throw new Error('private login ticket'); } };
  page.emit('request', request);
  await tick();
  assert.deepEqual(errors, ['AGENTCALL_UOS_REQUEST_FAILED']);
  bridge.page = {};
  page.emit('request', request);
  await tick();
  assert.equal(errors.length, 1);
  bridge.page = page;
  page.isClosed = () => true;
  page.emit('request', request);
  await tick();
  assert.equal(errors.length, 1);
});
