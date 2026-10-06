# 微信接入与可验证边界

agentCall 2.0 保持前端和本机 API 共用 10086。新增微信优先通道，现有邮箱数据和访问令牌原地复用。

## 实现依据

- [python-wechaty 官方仓库](https://github.com/wechaty/python-wechaty)：Python SDK 通过 Puppet Service 接入消息平台，并非独立的微信登录实现。
- [Python Wechaty 发行包](https://pypi.org/project/wechaty/0.10.7/)：固定 SDK 版本，使用 Python 3.10 兼容其 dataclass 定义。
- [wechaty-puppet-service](https://github.com/wechaty/puppet-service)：使用与 Python SDK 兼容的 gRPC 服务协议。
- [wechaty-puppet-wechat](https://github.com/wechaty/puppet-wechat)：Web 微信登录与 Chromium 驱动。账号是否允许 Web 登录由微信决定。
- [官方 UOS 文章](https://wechaty.js.org/2021/04/13/wechaty-uos-web/)及[后续修复 #206](https://github.com/wechaty/puppet-wechat/pull/206)：普通网页版失败不能单独证明 UOS 不可用。官方文章也明确不保证长期可用。

本地 Node 服务始终启用 UOS：保留兼容 Python SDK 的 0.x gRPC 依赖，将 `wechaty-puppet-wechat 1.18.4` 的公开 `extspam` 参数和 `lang=zh_CN&target=t` 入口移植到 `uos-compat.cjs`。原 0.28.1 已启用 2021 年的旧 UOS 参数，因此只添加 `uos: true` 没有效果。新拦截器替换旧拦截器，仅修改指定 HTTPS 微信登录地址；实际进入登录请求时输出不含凭据的版本标记。未关闭 TLS 校验。外部 Puppet 的登录实现由其服务商控制。

为避免旧版 Python SDK 默认初始化过程打印令牌、执行 ICMP 检查和新开无关 HTTP 端口，本项目仅适配其初始化边界，仍使用官方 Puppet、消息和联系人模型。锁定的依赖与真实 gRPC 回环测试共同验证协议兼容。Node bridge 针对当前登录页的持续轮询，将初始导航和恢复会话后的就绪事件从 load 改为 DOMContentLoaded；保留原有 Angular 就绪检查和 TLS 校验，避免二维码被无限加载的轮询阻挡。登录二维码仅在本机生成和展示，不交给第三方二维码网站。

## 容器和数据

网关容器 agentcall 使用 Python 3.10，提供 HTTP、SQLite、SMTP/IMAP 和 Python Wechaty 客户端。agentcall-wechat 使用 Node/Chromium 执行 Web 微信登录，只在 Docker 项目网络内提供 gRPC，不发布宿主端口。

只有网关挂载原邮箱数据库卷；WeChat 服务只读独立的内部认证卷，并持有独立的会话卷，不能读取邮箱密码或 API 令牌。本地 gRPC 明文仅限绑定的内部容器地址并进行令牌验证；配置外部服务时必须使用 TLS 校验。服务令牌不由 API 回显，二维码不写入历史记录。容器重启会尝试恢复已保存会话；如果微信已撤销该会话，需要重新扫码。

## API 增量

原 `/api/notify`、`/api/ask`、`/api/requests/{id}` 保持兼容。创建请求可选 `channel: auto|wechat|email`，默认 auto；指定通道用于逐通道测试，失败不会悄悄换通道。服务端在创建和发送前主动核对微信连接，不能依赖 Agent 或前端缓存的登录状态。

`GET /api/channels` 可供 Agent 调用，返回 `selected_channel`、`available`、`wechat` 状态、`email.configured` 和 `fallback_reason`；不返回二维码或服务令牌。

以下管理接口仅允许本机前端会话和 CSRF 验证，Agent API 令牌不能操作登录、查看联系人或更改目标：

| 接口 | 功能 |
| --- | --- |
| `GET /api/wechat` | `{config,status}`；`?probe=1` 实时检查 |
| `PUT /api/wechat` | 保存启用状态、本地／外部模式和目标联系人 |
| `POST /api/wechat/login` | 启动或重新开始扫码 |
| `POST /api/wechat/logout` | 停用当前微信连接与自动恢复 |
| `GET /api/wechat/contacts?q=...` | 搜索当前账号的已有联系人，最多 100 条 |
| `GET /api/wechat/avatar` | 当前登录账号的头像数据；获取失败返回空图，不影响登录 |
| `POST /api/wechat/test` | 实时检查服务、登录和目标联系人，保存诊断记录 |

配置字段：`enabled`、`mode`（local/external）、`service_endpoint`、`service_token`、`target_contact_id`。目标名称和登录账号 ID 由服务端验证后保存。回传仅含 `service_token_set`，不含令牌；空令牌在服务地址不变时保留旧值。显式选定或重选联系人必须在线验证，防止客户端提交任意联系人 ID。存在排队、发送中请求或微信等待回复时拒绝改变微信配置，已经发出的邮箱等待请求不阻止微信配置；「断开并停用微信」仍可立即停用连接并跨重启保持停用。该操作保留会话，不等同于在微信端撤销登录授权。

记录新增 `channel`、`requested_channel`、`recipient_label`、`target_contact_id`、`wechat_account_id`、`transport_message_id`、`fallback_reason`。历史无通道字段的记录解释为 email。微信回复独立校验账号和联系人，不把微信 ID 伪装成邮箱地址。

## 回复和故障语义

启用[高效模式](efficient-mode.md)后，目标联系人自由发送的文字也保存在收件箱与历史中，由当前 Agent 会话在步骤边界领取并确认。问题的原有匹配和超时规则继续生效；未匹配消息不会自动授权等待中的决策。

请求标记使用 `[AC:请求编号]`，邮件继续识别原 `[EC:请求编号]`。仅有一个来自指定账号／联系人的活动问题时，可用普通文本直接回答；并发问题或最近 30 天仍存在未回答的超时／发送结果未知问题时，必须带请求编号，避免上一项任务的回复被用于下一项任务。这一歧义检查不受 300 条候选记录上限影响，明确编号和引用优先匹配。无法归属的目标联系人消息保存为诊断，未作为决策。消息接收时间必须不早于该请求开始发送，阻止排队期间的旧消息重放。群消息、自身消息与非文本消息不进入用户决策。明确关联的迟到回复保存但不复活超时状态。

微信「引用」回复按 web 协议送达的扁平文本处理：被引用原文完整嵌入回复正文，其中的请求编号即为归属依据，引用编号与手写编号冲突时不猜测。归属成功后仅保存分隔线之后的用户输入部分，`<br/>` 换行在入口归一为普通换行；未归属消息保留全文。消息时间戳逐条取 `CreateTime`；此前的显示分组时间（MMDisplayTime）会让同一会话窗口内的回复共享首个锚点，被防重放检查误拒。

发送的微信消息由「Agent 名 · Request/Notice」头部、主题、正文与 `[AC:编号]` 脚注组成；ask 脚注另含北京时间（UTC+8）的「回复截止：今天/明天 HH:MM:SS」，更晚的期限显示完整日期，缺失时以记录页面为准。发送时同账号同联系人还有其他等待中的 ask，会追加一行「当前有多条消息等待回复，请使用引用回复」；通知类消息只有编号脚注。请求编号为北京时间 `YYYYMMDDHHmmss`（同秒冲突顺延一秒），邮件标记同步使用，历史 uuid 编号继续可匹配。

请求状态以一行微信消息尽力反馈给用户：回复按时到达发出「{Agent 名} 已读」（语义为回复到达，不代表 Agent 真实已读）；超时发出「{Agent 名} 不再等待你的回复」；超时后的回复到达补发「回复已记录（已逾期）」；无法归属且当时确有等待中问题的回复提示引用或带编号重发。发送失败不经微信反馈（彼时通道自身不可用，靠邮箱回退与前端呈现）；通知类消息、已回复请求的追加回复与无关闲聊不反馈。反馈消息不带编号、尽力投递，发送失败仅留诊断。

通道选择检查采用随机 DING/DONG 事件回环，同时确认登录账号，从而同时检查 RPC 和收件事件流。健康状态失效允许未发送的新请求回退邮箱。发送开始后的超时或异常属于结果不确定，不自动重发到另一个通道；Agent 应检查记录并依据原任务权限处理。所有诊断和回退理由都有持久记录。发送结果未知后收到带明确编号的回复也会归档，保留原失败状态。

扫码是用户在自己的微信客户端完成的授权步骤。本地模拟协议测试不代表某个真实账号已经获准 Web 微信登录；首次真实验收需扫码、选择联系人并完成一次 `test --channel wechat` 回复，由 Agent 复述收到的正文。

登录后二维码区域显示当前账号头像，无法取得或解码时回退为「微信已登录」。头像仅通过管理会话与 CSRF 验证后的本机接口读取；服务器保留 Cookie，使用校验证书的 HTTPS 从固定微信头像端点读取，禁止重定向并限制为 512 KiB 的常见位图。图片在内存中按账号缓存，不写入请求记录，退出或切换账号时前端清除旧图。头像失败不会影响通道选择。
