'use strict';

// wechaty-puppet-wechat's raw payload parser uses MMDisplayTime as the message
// timestamp. The web client groups nearby messages under one display time, so
// replies can inherit an anchor from before a request was even sent, and the
// gateway's anti-replay check then rejects them. CreateTime is each message's
// own send time; prefer it and keep the original as the fallback.
function installTimestampCompatibility(PuppetWeChat) {
  const original = PuppetWeChat.prototype.messageRawPayloadParser;
  if (!original) return;
  PuppetWeChat.prototype.messageRawPayloadParser = async function (rawPayload) {
    const payload = await original.call(this, rawPayload);
    const created = Number(rawPayload && rawPayload.CreateTime);
    if (payload && Number.isFinite(created) && created > 0) {
      payload.timestamp = created;
    }
    return payload;
  };
}

module.exports = { installTimestampCompatibility };
