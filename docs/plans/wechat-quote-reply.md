# 改动计划:微信"引用"回复支持

状态:已完成(2026-10-06 实施并通过真机复测;161 项 Python 测试与 7 项 Node 测试通过)。

## 背景与实测结论

目标:用户用微信自带"引用消息"回复某条请求时,回复应归属到被引用的那条请求,且存档正文只含用户输入部分,而不是整段引用渲染。

2026-10-06 用测试账号完成三组真机实验,结论:

1. **引用回复以纯文本到达**,不是 appmsg XML。实测格式(单行,内含字面 `<br/>`):
   ```
   「被引用者名:被引用原文……[AC:请求编号]……」<br/>- - - - - - - - - - - - - - -<br/>用户输入的正文
   ```
   分隔线为 15 个 `- `(空格分隔)。被引用原文完整嵌入,含其 `[AC:编号]`。
2. **编号扫描天然覆盖引用**:`Store.add_wechat_message` 的编号正则扫描全文,被引用原文里的 `[AC:id]` 即"用户引用了哪条"。实验 2/3 中编号匹配本身成功,无需消息 ID。
3. **归属失败的真正原因是时间戳**:三条回复的 `received_at` 完全相同(12:46:54),早于 ask2/ask3 的 `sending_at`(12:47:13/12:47:33),被 `store.py` 的防重放检查(`received ≥ sending_at`)拒绝,落成"无法安全确定回复归属"诊断。对照组 ask1 恰在 12:46:53 发出而侥幸通过。
4. **根因在 puppet 层**:`wechaty-puppet-wechat` 0.28.1 的 `messageRawPayloadParser` 用 `rawPayload.MMDisplayTime` 作 payload timestamp。`MMDisplayTime` 是网页客户端的显示分组锚点(约 5 分钟内的消息共用第一条的时间),不是逐条真实时间;`rawPayload.CreateTime` 才是。
5. **出站消息 ID 拿不到**(佐证):`puppet-wechat` 0.28.1 的 `messageSendText` 返回 void,gRPC 响应 id 为空 → `transport_message_id` 实际部署恒为 None。因此 store 预留的 `reference_id → transport_message_id` 匹配在本地 web 协议下没有数据来源;引用归属实际依赖上述"编号在被引用原文里"的机制,已足够。
6. 裸文本对照实验正常:单条 waiting 时裸文本归属成功,正文干净。

## 改动项

### 1. Node 侧:时间戳改用 CreateTime

`docker/wechat/service.cjs`,与现有 `uos-compat` 同模式,在 `PuppetWeChat` 可用后打原型补丁:

```js
const originalParse = PuppetWeChat.prototype.messageRawPayloadParser;
PuppetWeChat.prototype.messageRawPayloadParser = async function (rawPayload) {
  const payload = await originalParse.call(this, rawPayload);
  // MMDisplayTime 是显示分组锚点,同一会话窗口内消息共用;CreateTime 才是逐条发送时间。
  const created = Number(rawPayload && rawPayload.CreateTime);
  if (payload && Number.isFinite(created) && created > 0) payload.timestamp = created;
  return payload;
};
```

语义说明:防重放检查关心"消息发出时间 vs 请求发出开始",`CreateTime` 由发送方手机时钟给出,是该语义下可得的最优字段;未来时间戳不做钳制,与现状一致。

### 2. Python 侧:`<br/>` 归一化

`gateway/wechat.py` `_message` 中取到 `body` 后:

```python
body = re.sub(r'<br\s*/?>', '\n', body)
```

覆盖所有下游(回复正文、自由收件、诊断),避免正文里残留字面 `<br/>`(2026-10-05 的 live_inbox 已出现过)。

### 3. 存储侧:归属成功后剥离引用块

`gateway/store.py` `add_wechat_message`:编号/裸文本扫描仍在**全文**上进行(保证引用归属),但 `len(matches) == 1` 且落库时,若检测到引用分隔线且其后有非空正文,则只保留用户输入部分:

```python
_QUOTE_DIVIDER = re.compile(r'(?:^|\n)- (?:- ){10,}(?:\n|$)')
```

- 命中且尾部非空 → 正文 = 尾部(再照旧剥 `[AC:id]`,通常已不在尾部);
- 未命中或尾部为空 → 保持全文(例如用户只发了引用没有输入)。
- 未归属的消息(诊断/自由收件)保留全文,不剥离。

### 4. 测试(`tests/test_channels.py`,沿用现有 `self.incoming` 辅助)

- 引用回复:正文为 `「…[AC:id]…」\n- - - …\n用户正文` → 归属到 id,存档正文等于用户正文;
- 引用 + 单一 waiting 裸回:引用块无编号 → 裸回兜底归属,正文仍剥离引用块;
- 引用块编号与用户输入编号冲突 → 拒绝(现有 tokens 歧义路径,补回归);
- `<br/>` 归一化:若在 `_message` 层实现,补一个直接对该辅助逻辑的单测或通过现有结构可测的最小路径。

### 5. 文档(`docs/wechat.md` "回复和故障语义")

补记:微信"引用"回复按被引用原文中的请求编号归属;存档正文只保留用户输入;消息时间戳以逐条 `CreateTime` 为准(此前误用显示分组时间,导致引用回复被防重放检查误拒)。

## 验证

1. `docker compose build agentcall-wechat && docker compose up -d`(会话卷保留,无需重新扫码);
2. 重跑实验:单一 ask → 手机长按引用 → 只输入纯文本 → 应在记录页看到及时回复、正文仅为所输文字;
3. 并发两 ask,引用第二条回复 → 应精确归属第二条;
4. 查库确认 `replies` 正文无 `「」/ <br/> / 分隔线` 残留,`received_at` 逐条递增不再共用锚点。

## 范围外 / 备注

- `reference_id → transport_message_id` 匹配代码保留不删:外部 1.x puppet(发送可返回消息 ID)仍可能走该路径;本地 web 协议下无数据来源。
- 不改防重放检查本身的时间语义,只修时间戳来源。
- 容器内今日实验遗留的 3 个 `waiting` ask 会按各自超时自然结束,无需处理。
