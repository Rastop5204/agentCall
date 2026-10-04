# 微信接入与可验证边界

agentCall 2.0 保持前端和本机 API 共用 10086。新增微信优先通道，现有邮箱数据和访问令牌原地复用。

## 实现依据

- [python-wechaty 官方仓库](https://github.com/wechaty/python-wechaty)：Python SDK 通过 Puppet Service 接入消息平台，并非独立的微信登录实现。
- [Python Wechaty 发行包](https://pypi.org/project/wechaty/0.10.7/)：固定 SDK 版本，使用 Python 3.10 兼容其 dataclass 定义。
- [wechaty-puppet-service](https://github.com/wechaty/puppet-service)：使用与 Python SDK 兼容的 gRPC 服务协议。
- [wechaty-puppet-wechat](https://github.com/wechaty/puppet-wechat)：Web 微信登录与 Chromium 驱动。账号是否允许 Web 登录由微信决定。

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
| `POST /api/wechat/test` | 实时检查服务、登录和目标联系人，保存诊断记录 |

配置字段：`enabled`、`mode`（local/external）、`service_endpoint`、`service_token`、`target_contact_id`。目标名称和登录账号 ID 由服务端验证后保存。回传仅含 `service_token_set`，不含令牌；空令牌在服务地址不变时保留旧值。显式选定或重选联系人必须在线验证，防止客户端提交任意联系人 ID。存在排队、发送中请求或微信等待回复时拒绝改变微信配置，已经发出的邮箱等待请求不阻止微信配置；「断开并停用微信」仍可立即停用连接并跨重启保持停用。该操作保留会话，不等同于在微信端撤销登录授权。

记录新增 `channel`、`requested_channel`、`recipient_label`、`target_contact_id`、`wechat_account_id`、`transport_message_id`、`fallback_reason`。历史无通道字段的记录解释为 email。微信回复独立校验账号和联系人，不把微信 ID 伪装成邮箱地址。

## 回复和故障语义

请求标记使用 `[AC:请求编号]`，邮件继续识别原 `[EC:请求编号]`。仅有一个来自指定账号／联系人的活动问题时，可用普通文本直接回答；并发问题或最近 30 天仍存在未回答的超时／发送结果未知问题时，必须带请求编号，避免上一项任务的回复被用于下一项任务。这一歧义检查不受 300 条候选记录上限影响，明确编号和引用优先匹配。无法归属的目标联系人消息保存为诊断，未作为决策。消息接收时间必须不早于该请求开始发送，阻止排队期间的旧消息重放。群消息、自身消息与非文本消息不进入用户决策。明确关联的迟到回复保存但不复活超时状态。

通道选择检查采用随机 DING/DONG 事件回环，同时确认登录账号，从而同时检查 RPC 和收件事件流。健康状态失效允许未发送的新请求回退邮箱。发送开始后的超时或异常属于结果不确定，不自动重发到另一个通道；Agent 应检查记录并依据原任务权限处理。所有诊断和回退理由都有持久记录。发送结果未知后收到带明确编号的回复也会归档，保留原失败状态。

扫码是用户在自己的微信客户端完成的授权步骤。本地模拟协议测试不代表某个真实账号已经获准 Web 微信登录；首次真实验收需扫码、选择联系人并完成一次 `test --channel wechat` 回复，由 Agent 复述收到的正文。
