'use strict';

// The current WeChat login page starts a long-lived JSONP login request before
// window.load. Waiting for load makes the pinned bridge time out despite a
// usable DOM. Its existing readyAngular() still waits for the actual app before
// injecting handlers; only the navigation lifecycle boundary changes here.
function installBridgeCompatibility(Bridge, memorySlot) {
  const originalStart = Bridge.prototype.start;
  const originalLoad = Bridge.prototype.onLoad;
  const originalBlocked = Bridge.prototype.testBlockedMessage;
  const loading = new WeakMap();
  if (originalStart) Bridge.prototype.start = function () {
    // The upstream watchdog reuses Bridge after failure without removing its
    // bound load listener. Multiple injections race and close the new page.
    this.removeAllListeners('load');
    return originalStart.call(this);
  };
  if (originalLoad) Bridge.prototype.onLoad = function (page) {
    if (loading.has(page)) return loading.get(page);
    const task = Promise.resolve().then(() => originalLoad.call(this, page))
      .catch(() => this.emit('error', new Error('AGENTCALL_PAGE_CLOSED')))
      .finally(() => loading.delete(page));
    loading.set(page, task);
    return task;
  };
  if (originalBlocked) Bridge.prototype.testBlockedMessage = async function (text) {
    const html = text || await this.innerHTML();
    const message = await originalBlocked.call(this, html);
    if (!message) return message;
    const xml = this.preHtmlToXml(html);
    const code = /<ret>\s*(-?\d{1,8})\s*<\/ret>/.exec(xml);
    // Forward only the protocol code, never page HTML, login URLs or cookies.
    return 'AGENTCALL_LOGIN_REJECTED:' + (code ? code[1] : 'unknown');
  };
  // The upstream QR watchdog also reloads the page every two minutes.
  Bridge.prototype.reload = async function () {
    if (!this.page) throw new Error('no page');
    await this.page.reload({ waitUntil: 'domcontentloaded' });
  };
  Bridge.prototype.initPage = async function (browser) {
    const page = this.page = await browser.newPage();
    await this.uosPatch(page);
    page.on('error', error => this.emit('error', error));
    page.on('dialog', this.onDialog.bind(this));
    const cookies = await this.options.memory.get(memorySlot);
    await page.goto(this.entryUrl(cookies), { waitUntil: 'domcontentloaded' });
    if (cookies && cookies.length) await page.setCookie(...cookies);
    page.on('domcontentloaded', () => this.emit('load', page));
    await page.reload({ waitUntil: 'domcontentloaded' });
    return page;
  };
}

module.exports = { installBridgeCompatibility };
