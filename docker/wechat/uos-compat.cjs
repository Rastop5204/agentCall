'use strict';

// UOS login headers from wechaty-puppet-wechat 1.18.4 (Apache-2.0).
// https://github.com/wechaty/puppet-wechat/blob/v1.18.4/src/bridge.ts
// Credit: Wechaty contributors and @luvletter2333 (upstream issue #127).
// This is upstream's public protocol constant, not a user/session credential.
const UOS_EXTSPAM = 'Go8FCIkFEokFCggwMDAwMDAwMRAGGvAESySibk50w5Wb3uTl2c2h64jVVrV7gNs06GFlWplHQbY/5FfiO++1yH4ykCyNPWKXmco+wfQzK5R98D3so7rJ5LmGFvBLjGceleySrc3SOf2Pc1gVehzJgODeS0lDL3/I/0S2SSE98YgKleq6Uqx6ndTy9yaL9qFxJL7eiA/R3SEfTaW1SBoSITIu+EEkXff+Pv8NHOk7N57rcGk1w0ZzRrQDkXTOXFN2iHYIzAAZPIOY45Lsh+A4slpgnDiaOvRtlQYCt97nmPLuTipOJ8Qc5pM7ZsOsAPPrCQL7nK0I7aPrFDF0q4ziUUKettzW8MrAaiVfmbD1/VkmLNVqqZVvBCtRblXb5FHmtS8FxnqCzYP4WFvz3T0TcrOqwLX1M/DQvcHaGGw0B0y4bZMs7lVScGBFxMj3vbFi2SRKbKhaitxHfYHAOAa0X7/MSS0RNAjdwoyGHeOepXOKY+h3iHeqCvgOH6LOifdHf/1aaZNwSkGotYnYScW8Yx63LnSwba7+hESrtPa/huRmB9KWvMCKbDThL/nne14hnL277EDCSocPu3rOSYjuB9gKSOdVmWsj9Dxb/iZIe+S6AiG29Esm+/eUacSba0k8wn5HhHg9d4tIcixrxveflc8vi2/wNQGVFNsGO6tB5WF0xf/plngOvQ1/ivGV/C1Qpdhzznh0ExAVJ6dwzNg7qIEBaw+BzTJTUuRcPk92Sn6QDn2Pu3mpONaEumacjW4w6ipPnPw+g2TfywJjeEcpSZaP4Q3YV5HG8D6UjWA4GSkBKculWpdCMadx0usMomsSS/74QgpYqcPkmamB4nVv1JxczYITIqItIKjD35IGKAUwAA==';
const LOGIN_PATH = '/cgi-bin/mmwebwx-bin/webwxnewloginpage';
const LOGIN_HOSTS = new Set(['wx.qq.com', 'wx2.qq.com', 'wx8.qq.com',
  'web.wechat.com', 'web2.wechat.com']);

function installUosCompatibility(Bridge) {
  const originalEntryUrl = Bridge.prototype.entryUrl;
  Bridge.prototype.entryUrl = function (cookies) {
    const url = new URL(originalEntryUrl.call(this, cookies));
    url.searchParams.set('lang', 'zh_CN');
    url.searchParams.set('target', 't');
    return url.toString();
  };
  // Replace the old interceptor; stacking both would continue a request twice.
  Bridge.prototype.uosPatch = async function (page) {
    await page.setRequestInterception(true);
    page.on('request', request => {
      Promise.resolve().then(() => {
        const url = new URL(request.url());
        if (url.protocol === 'https:' && !url.port && LOGIN_HOSTS.has(url.hostname)
            && url.pathname === LOGIN_PATH) {
          // Deliberately log no URL, query, headers, cookies, or account details.
          console.info('agentCall UOS 1.18.4 login headers applied.');
          return request.continue({ headers: { ...request.headers(),
            'client-version': '2.0.0', extspam: UOS_EXTSPAM } });
        }
        return request.continue();
      }).catch(() => {
        // Chromium can close an intercepted request during refresh/stop.
        // Never let the URL-bearing Puppeteer exception reach process logs.
        if (this.page === page && !page.isClosed()) {
          this.emit('error', new Error('AGENTCALL_UOS_REQUEST_FAILED'));
        }
      });
    });
  };
}

module.exports = { installUosCompatibility };
