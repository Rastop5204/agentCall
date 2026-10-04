'use strict';

// The current WeChat login page starts a long-lived JSONP login request before
// window.load. Waiting for load makes the pinned bridge time out despite a
// usable DOM. Its existing readyAngular() still waits for the actual app before
// injecting handlers; only the navigation lifecycle boundary changes here.
function installBridgeCompatibility(Bridge, memorySlot) {
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
