# 微信接入与可验证边界

agentCall 2.2 起微信通道内嵌文件传输助手网页协议实现（单容器），前端和本机 API 共用 10086，现有邮箱数据和访问令牌原地复用。

## 实现依据

- [wx-filehelper-api](https://github.com/CjackHwang/wx-filehelper-api)：基于微信「文件传输助手」网页通道的轻量协议实现。本项目 vendor 其协议核心 `WeChatHelperBot`（`gateway/wxbot/direct_bot.py`，commit `cb52d3a`，来源与本地改动清单见 `gateway/wxbot/README.md`），直接在网关进程内驱动，不使用其 FastAPI 服务层。
- 协议端点：`jslogin`（扫码 uuid）→ `login`（408 等待／201 已扫／200 授权）→ `webwxnewloginpage`（取 `skey/sid/uin/pass_ticket`）→ `webwxinit` → `synccheck` 长轮询 + `webwxsync` 收取 → `webwxsendmsg` 发送。入口主机默认 `szfilehelper.weixin.qq.com`，发送目标恒为 `filehelper`（自己发给自己），因此没有联系人与群聊。
- vendored 副本带六组标记为 `# agentCall patch:` 的本地修改：消息携带原始 `CreateTime`（防重放需要真实逐条时间戳）；引用回复（refermsg appmsg）扁平化为「引用原文 + `- ` 分割线 + 自己的话」的文本形态并透出被引用消息 ID；HTTP trace 默认关闭（默认开启会向只读容器的工作目录写盘且落盘消息正文）；移除会打印含 `pass_ticket`/`skey` URL 的异常文本的 `print`；`state_path` 参数与 `reset_session()`（被踢后凭据残留会让扫码路径永久不可达）；`send_text` 返回服务器 MsgID。
- 依赖仅 `httpx`；登录二维码由网关用本地 `qrcode` 库把登录 URL 渲染为内联 SVG，不交给第三方二维码网站。账号是否允许网页文件传输助手登录由微信决定，非官方客户端始终存在账号风险；同一账号应只保持一个会话，其他网页端同时登录会互相顶替。

## 容器和数据

单容器 `agentcall`（Python 3.12）提供 HTTP、SQLite、SMTP/IMAP 和内嵌微信协议客户端，全部状态在 `emailcall-data` 卷：会话凭据（skey/sid/uin/pass_ticket/synckey/cookies）持久化于 `/data/wxbot/state.json`（目录 0700，文件 0600），容器重启加载会话免重新扫码；微信撤销会话时 synccheck 返回下线，网关清空内存凭据回到扫码界面。不再有 Node/Chromium sidecar、gRPC 与服务令牌。

## API 增量

原 `/api/notify`、`/api/ask`、`/api/requests/{id}` 保持兼容。创建请求可选 `channel: auto|wechat|email`，默认 auto；指定通道用于逐通道测试，失败不会悄悄换通道。服务端在创建和发送前核对微信登录账号，不能依赖 Agent 或前端缓存的登录状态。

`GET /api/channels` 可供 Agent 调用，返回 `selected_channel`、`available`、`wechat` 状态、`email.configured` 和 `fallback_reason`；不返回二维码。

以下管理接口仅允许本机前端会话和 CSRF 验证，Agent API 令牌不能操作登录或更改配置：

| 接口 | 功能 |
| --- | --- |
| `GET /api/wechat` | `{config,status}`；`?probe=1` 实时检查 |
| `PUT /api/wechat` | 保存启用状态（`{enabled}`） |
| `POST /api/wechat/login` | 删除已存会话并重新开始扫码 |
| `POST /api/wechat/logout` | 停用当前微信连接与自动恢复（保留会话文件） |
| `POST /api/wechat/test` | 实时检查登录与绑定，保存诊断记录 |

配置字段：`enabled`、`target_contact_id`（恒为 `filehelper`）、`target_contact_name`（恒为「文件传输助手」）、`account_id`（登录时自动绑定）。从旧版升级时，遗留的 `mode/service_endpoint/service_token` 与旧联系人绑定会在首次读取时清洗为空，等待重新扫码自动重绑；存在排队、发送中请求或微信等待回复时拒绝改变微信配置，已经发出的邮箱等待请求不阻止微信配置。「断开并停用微信」停用连接并跨重启保持停用，但保留会话文件，重新启用时可恢复，不等同于在微信端撤销登录授权；「扫码登录」按钮则删除会话文件强制全新扫码。

记录新增 `channel`、`requested_channel`、`recipient_label`、`target_contact_id`、`wechat_account_id`、`transport_message_id`（微信服务器 MsgID）、`fallback_reason`。历史无通道字段的记录解释为 email。微信回复按登录账号独立校验，不把微信 ID 伪装成邮箱地址。

## 回复和故障语义

启用[高效模式](efficient-mode.md)后，绑定账号在文件传输助手里自由发送的文字也保存在收件箱与历史中，由当前 Agent 会话在步骤边界领取并确认。问题的原有匹配和超时规则继续生效；未匹配消息不会自动授权等待中的决策。

请求标记使用 `[AC:请求编号]`，邮件继续识别原 `[EC:请求编号]`。仅有一个来自绑定账号的活动问题时，可用普通文本直接回答；并发问题或最近 30 天仍存在未回答的超时／发送结果未知问题时，必须带请求编号（或引用对应消息），避免上一项任务的回复被用于下一项任务。这一歧义检查不受 300 条候选记录上限影响，明确编号和引用优先匹配。无法归属的消息保存为诊断，未作为决策。消息接收时间必须不早于该请求开始发送，阻止排队期间的旧消息重放。文件传输助手自身的系统通知、图片、文件与语音不进入用户决策。

微信「引用」回复按扁平文本处理：web 协议可能以纯文本（内嵌被引用原文与 `- ` 分割线）送达，vendored 层也会把 refermsg appmsg 形态归一为同样的文本（被引用消息的服务器 ID 作为 `reference_id` 参与关联）。引用原文中的请求编号即为归属依据，引用编号与手写编号冲突时不猜测。归属成功后仅保存分割线之后的用户输入部分，`<br/>` 换行在入口归一为普通换行；未归属消息保留全文。消息时间戳逐条取 `CreateTime`；显示分组时间（MMDisplayTime）会让同一会话窗口内的回复共享首个锚点，被防重放检查误拒。

发送的微信消息由「Agent 名 · Request/Notice」头部、主题、正文与 `[AC:编号]` 脚注组成；ask 脚注另含北京时间（UTC+8）的「回复截止：今天/明天 HH:MM:SS」，更晚的期限显示完整日期，缺失时以记录页面为准。发送时同账号还有其他等待中的 ask，会追加一行「当前有多条消息等待回复，请使用引用回复」；通知类消息只有编号脚注。请求编号为北京时间 `YYYYMMDDHHmmss`（同秒冲突顺延一秒），邮件标记同步使用，历史 uuid 编号继续可匹配。

请求状态以一行微信消息尽力反馈给用户：回复按时到达发出「{Agent 名} 已读」（语义为回复到达，不代表 Agent 真实已读）；超时发出「{Agent 名} 不再等待你的回复」；超时后的回复到达补发「回复已记录（已逾期）」；无法归属且当时确有等待中问题的回复提示引用或带编号重发。发送失败不经微信反馈（彼时通道自身不可用，靠邮箱回退与前端呈现）；通知类消息、已回复请求的追加回复与无关闲聊不反馈。反馈消息不带编号、尽力投递，发送失败仅留诊断。

**健康检查语义（与旧版差异）**：旧 gRPC 通道用随机 DING/DONG 事件回环验证双向连接；网页协议没有等价机制。`probe` 为进程内状态（登录标志 + 账号 ID），新鲜度由接收循环的 synccheck 周期界定（约 25–40 秒）：账号被顶掉后，最坏一个周期内 `available` 仍可能短暂为真，随后的发送预检会再次核对账号，发送中的网络异常一律归类「发送结果不确定」，不会自动改发邮箱。健康状态失效允许未发送的新请求回退邮箱；所有诊断和回退理由都有持久记录。发送结果未知后收到带明确编号的回复也会归档，保留原失败状态。自己发出的消息回显（同账号其他设备或服务器回执）通过服务器 MsgID 与短窗口内的精确文本双保险过滤，不会作为回复。

**账号头像**：用户扫码后（登录轮询 201 响应内嵌 `window.userAvatar`）即捕获头像数据，从「待确认」步骤起在二维码区域显示，登录后继续显示，直至登录状态失效（被踢/重置）才清空；从未经历扫码的恢复会话通过 `webwxgeticon` 拉取兜底（每小时至多一次，失败退避 60 秒，仅接受 ≤512 KiB 的图片响应）。头像只经管理界面的 `/api/wechat` 视图返回（`avatar_image` 字段），Agent 可见的 `/api/channels` 不包含它；获取失败不影响登录与消息路由。

扫码是用户在自己的微信客户端完成的授权步骤。本地模拟协议测试（MockTransport 覆盖 jslogin→登录轮询→初始化→收发→引用回复全链路）不代表某个真实账号已经获准登录；首次真实验收需扫码、自动绑定并完成一次 `test --channel wechat` 回复，由 Agent 复述收到的正文。

## 从 Wechaty 版本迁移

- 启动器带 `--remove-orphans`，旧 `agentcall-wechat` 容器与两个专用卷（`agentcall-wechat-data`、`agentcall-wechat-auth`）不再使用，可手动 `docker volume rm` 清理；`emailcall-data` 原样保留。
- 微信需重新扫码一次：旧 Chromium 会话（MemoryCard）与新 state.json 不兼容。升级前处于等待状态的旧微信请求按超时处理，反馈便签因账号绑定不符被抑制，历史记录保留。
- 旧高效模式收件箱中未确认条目的 scope 含旧联系人 ID，新 scope（账号 + filehelper）下不再可见；对应历史记录仍可在记录页查看。
