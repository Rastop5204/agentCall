const assert = require('node:assert/strict');
const { test } = require('node:test');
const { installTimestampCompatibility } = require('../docker/wechat/timestamp-compat.cjs');

test('payload timestamps prefer each message CreateTime over the display anchor', async () => {
  class Puppet {
    async messageRawPayloadParser(rawPayload) {
      return { id: rawPayload.MsgId, text: rawPayload.MMActualContent, timestamp: rawPayload.MMDisplayTime };
    }
  }
  installTimestampCompatibility(Puppet);
  const payload = await new Puppet().messageRawPayloadParser({
    MsgId: '42',
    CreateTime: 1791162600,
    MMDisplayTime: 1791162000,
    MMActualContent: 'reply sent after the request',
  });
  assert.equal(payload.timestamp, 1791162600);
  const fallback = await new Puppet().messageRawPayloadParser({ MsgId: '43', MMDisplayTime: 1791162000 });
  assert.equal(fallback.timestamp, 1791162000);
});

test('installation tolerates a missing parser hook', async () => {
  class Puppet {}
  installTimestampCompatibility(Puppet);
  assert.equal(Puppet.prototype.messageRawPayloadParser, undefined);
});
