# agentCall

给本机 Agent 使用的微信／邮箱网关：微信优先，微信不可用时使用邮箱。发送任务完成通知，或发送问题并等待用户回复。配置、请求、错误、回复和路由选择持久保存在 SQLite 中。

## 高效模式：沿用当前会话

在配置页打开「高效模式」并保存，导出并更新 Agent skill。启用后，Agent 以「步骤」为单位把面向用户的进度、问题和结果整合成一条消息通过网关发送，并收取文件传输助手里的私聊文字。用户无需等到 Agent 提问才可发送消息。每条主动消息保存在「记录 → 微信收件」，先显示「等待 Agent 读取」，纳入会话并明确确认后显示「Agent 已读取」；已读取不代表任务执行完成。配置、未确认消息和读取状态在容器重启后保留。

本模式沿用当前 Codex／Claude Code 会话，不启动独立 Agent。安装器写入的 PostToolUse 收件钩子在每个工具调用后自动检查收件并把新消息注入 Agent 上下文（Codex 需在会话中信任该钩子），效果接近 harness 原生的消息引导；钩子不可用时 Agent 在步骤边界手动检查。任务完成后默认询问是否有下一步指示（短等待约 60 秒），用户确认需要后继续等待具体指示（约 1200 秒），超时或用户表示不需要则发送结束通知并结束回合；用户事先声明「完成后结束对话」时直接结束。已结束的会话不会被 skill 自动唤醒，下一回合开始时先查收件箱。当前仅支持文件传输助手（自己发给自己）的私聊文字，不包含图片、语音和群聊。微信不可用时发送仍使用邮箱备份；接收等待微信恢复。详见 [高效模式 API 与语义](docs/efficient-mode.md)。

## 微信配置与路由

一键启动会运行 agentCall 网关（单容器）。打开前端「配置 → 微信」，启用并保存微信配置，点击登录，在自己的微信中扫描本地显示的二维码。登录后自动把文件传输助手绑定为唯一对话，无需选择联系人。先运行「检查微信连接」，再选择「微信」发送对话测试，在手机微信的文件传输助手里回复测试消息即可验证双向通信。

微信通道通过内嵌的文件传输助手网页协议收发消息（见下），接收循环以约 25–40 秒一轮的 synccheck 保持会话活跃；创建通知和实际发送前都会核对登录账号。默认微信优先；未登录、掉线或账号变化未重新登录时，改用配置好的邮箱，并在记录中保存回退原因。邮箱可以独立配置，也可仅使用微信；两者都不可用时保存失败记录。指定微信的连通测试不会回退邮箱冒充成功。

若微信发送已经开始但结果不确定，服务会保存「发送结果未知」，避免重复补发邮件。已经发出的等待请求留在原通道，不因之后离线而重复通知。微信只有一个待答问题时可直接回复；多个问题同时等待时，请长按引用对应消息回复（或带上消息中的 `[AC:请求编号]`）。回复仅接受发送时绑定的微信账号在文件传输助手里发来的文字；旧账号、歧义回复不会作为决策。超时后带编号的回复继续归档，不会重新激活已超时请求。

**登录条件**：微信通道内嵌 [wx-filehelper-api](https://github.com/CjackHwang/wx-filehelper-api) 的协议核心（本地 `gateway/wxbot/`，直接实现文件传输助手网页版协议，仅依赖 httpx），不再使用 Wechaty/Node/Chromium。会话凭据保存在数据卷的 `/data/wxbot/state.json`，容器重启免重新扫码；每个微信账号同时只应有一个会话（避免其他网页端同时登录互相顶替）。非官方客户端始终存在账号风险，被拒绝或失效时邮箱继续可用。详见 [微信接入说明](docs/wechat.md)。

## 从 EmailCall 升级

升级保留原 `emailcall-data` 数据卷、`emailcall.sqlite3`、邮箱配置、记录和 API 令牌。这些内部存储名刻意保留，避免创建空库。启动器会识别并停用旧 `emailcall` 容器，再启动新的 `agentcall` 项目组；不会删除旧容器或数据卷。旧 API 与已经安装的 EmailCall skill 仍可调用，新导出包和安装器名称为 `agentcall`。安装新技能会替换全局通知规则中的旧受管理区块，不改动其他规则。

Docker Desktop 中使用 **agentcall 项目组**的启动／停止按钮，或新的「启动 agentCall.command／停止 agentCall.command」。微信登录会话保存在 `emailcall-data` 卷的 `/data/wxbot/state.json`。「断开并停用微信」会停止自动恢复但保留有效会话，不等同于撤销微信端授权；重新登录换号后自动重新绑定。

**从 Wechaty 版本（≤2.1.0）升级**：旧版双容器（Node/Chromium sidecar）会在启动器运行 `--remove-orphans` 时自动退役；微信需重新扫码一次（旧会话格式不兼容），升级后可手动删除不再使用的 `agentcall-wechat-data`、`agentcall-wechat-auth` 卷。升级前未答复的旧微信请求将按超时处理，其历史记录保留。

前端与 API 共用 **10086** 端口，前端地址为 [http://127.0.0.1:10086/frontend/](http://127.0.0.1:10086/frontend/)。前端提供「配置」「记录」两个页面、明暗主题、动效及减少动态效果支持。邮箱与 HTTP 核心使用 Python 标准库，前端为原生 HTML/CSS/JavaScript；微信通道在网关进程内实现（httpx），不需要 Node、Chromium、Redis 或外部数据库。

## 一键启动

macOS 需先安装 [Docker Desktop](https://www.docker.com/products/docker-desktop/) 和 Python 3。已有 Xcode Command Line Tools 通常提供 Python；没有时可运行 `xcode-select --install`。

1. 双击 **启动 agentCall.command**。启动器会启动 Docker Desktop、构建并启动容器，然后打开前端。
2. 在「配置」扫码登录微信（登录后自动绑定文件传输助手）；也可保存服务邮箱、SMTP/IMAP 授权密码和目标邮箱作为备用。运行对应通道的连接检查。
3. 导出 Agent skill，解压后运行下方安装命令。在新的 Agent 会话中要求「使用 agentCall 做连通性测试」。
4. 在实际收到消息的微信或目标邮箱回复测试。Agent 复述你的回复即完成真实收发验证。

双击 **停止 agentCall.command** 或在 Docker Desktop 停止 `agentcall` 项目组即可关闭服务。再次启动时原配置和记录仍在。

首次运行启动器还会安装一个**当前用户的浏览器助手**：它每 3 秒检查本机服务，仅当新的服务实例启动时打开一次前端。因此之后直接在 Docker Desktop 开启容器也会自动打开网页。它不需要管理员权限、不读取邮件或令牌，安装位置为 `~/Library/LaunchAgents/local.agentcall.browser.plist` 和 `~/Library/Application Support/agentCall/`。容器本身无法操作 macOS 浏览器，需要这个宿主助手；首次直接运行 Docker 命令时请手动打开网址或先运行一次启动器。

移除自动打开功能：

```sh
python3 scripts/install-watcher.py --uninstall
```

终端 / Linux 部署：

```sh
docker compose up --build -d
docker compose logs --tail=100
docker compose stop
```

Linux 可运行 `bash scripts/start.sh` 启动并通过 `xdg-open` 打开前端；后续仅切换容器时自动打开浏览器的常驻助手目前支持 macOS。端口被占用时先停止占用 10086 的程序。

## 邮箱配置

服务邮箱负责 SMTP 发信和 IMAP 收取回复；目标邮箱是用户实际使用的收件地址。邮箱面板顶部的「启用邮箱」开关可临时停用邮箱备用：停用后保留全部凭据，但不参与自动路由、不执行发送回退，也暂停 IMAP 收件轮询（例如备用邮箱持续认证失败时可以停用以免刷诊断记录）；重新启用后立即恢复。可使用同一邮箱，但推荐两个地址便于区分通知和回复。仅接收与请求匹配、发件人地址等于目标邮箱的回复；抄送者和自动回复不用于决策。

| 服务商 | SMTP | IMAP | 凭据与准备 |
| --- | --- | --- | --- |
| iCloud | `smtp.mail.me.com:587` STARTTLS | `imap.mail.me.com:993` TLS | Apple 账户开启双重认证后生成 App 专用密码 |
| Gmail | `smtp.gmail.com:465` TLS | `imap.gmail.com:993` TLS | 通常需两步验证及应用专用密码；部分组织政策不允许此方式 |
| 163 | `smtp.163.com:465` TLS | `imap.163.com:993` TLS | 邮箱设置开启 SMTP/IMAP，使用客户端授权码 |
| QQ | `smtp.qq.com:465` TLS | `imap.qq.com:993` TLS | 邮箱设置开启 SMTP/IMAP，使用授权码 |
| 自定义 | 按服务商填写 | 按服务商填写 | 只支持 TLS 或 STARTTLS；当前不提供 OAuth 登录 |

用户名一般填写完整邮箱地址。配置页提供预设并允许调整主机、端口、加密方式、IMAP 文件夹及轮询间隔。保存时密码留空会保留同一账户原密码；切换账户需要重新提供密码。连接测试仅验证已保存配置，结果和失败原因也会记录。

回复延迟通常为一个 IMAP 轮询周期（默认 10 秒，可设 10–300 秒），新发出询问和到达回复期限时会额外唤醒收件检查。判断回复是否按时以邮件服务器接收时间为准。截止后最多额外用 30 秒核对已到达邮箱的回复，因此最终超时状态可能稍晚出现；用户回复期限本身不会延长。保留迟到回复，但不会把已超时请求重新变为授权。测试失败时在「记录」查看中文错误解释和排查建议。

## 安装 Agent skill

在配置页导出 ZIP 并解压，可得到 `agentcall/` 目录。导出包含当前 API 令牌，请把它当作本机凭据保存，不要提交到代码仓库。

```sh
# 在解压目录中，为一个或两个 Agent 安装：
python3 agentcall/install.py codex
python3 agentcall/install.py claude
python3 agentcall/install.py both
```

Codex 安装到 `${CODEX_HOME:-~/.codex}/skills/agentcall`，Claude Code 安装到 `~/.claude/skills/agentcall`。安装器同时在 Codex `AGENTS.md` / Claude Code `CLAUDE.md` 中维护一个通知规则区块，要求每项任务完成时通知、长任务各阶段发送进度、需要用户选择时发送询问并等待。安装器还会写入 PostToolUse 收件钩子（Claude Code 到 `~/.claude/settings.json`，Codex 到 `~/.codex/hooks.json`），高效模式下把微信新消息自动注入 Agent 上下文；Codex 需要在会话中信任该钩子后它才生效。现有规则与配置保留，首次修改各文件另存 `.agentcall-backup` 备份。**安装后开启新的 Agent 会话**。仅手动复制 skill 不能保证每次任务都自动触发；全局规则使支持该机制的 Agent 获得持续指令，最终执行仍取决于 Agent 对规则的支持及更高优先级的指令。

其他 Agent 可以手动复制 `agentcall/` 至其技能目录，并在它的全局规则中添加相同的自动通知要求。开发和容器启动均不会自动修改你的 Agent 配置。

卸载技能、通知规则和收件钩子（不影响其他全局规则与配置）：

```sh
python3 agentcall/install.py both --uninstall
```

也可以直接告诉 Agent「停用 agentCall」。若在网页轮换 API 令牌，旧技能会立即失效，需重新导出并安装。

技能 CLI 无额外 Python 依赖，示例：

```sh
python3 agentcall/scripts/agentcall.py notify --subject '任务已完成' --body '已完成修改并通过验证。'
python3 agentcall/scripts/agentcall.py ask --subject '请选择方案' --body 'A 保持当前行为；B 启用新行为。建议 A，超时后保持当前行为。' --timeout 300
python3 agentcall/scripts/agentcall.py test --timeout 300
python3 agentcall/scripts/agentcall.py status REQUEST_ID --wait-seconds 300
```

每条输出是独立 JSON。创建时立即输出幂等键和请求 ID，Agent 可在中断后恢复等待。退出码 `0` = 已发送/已回复，`2` = 失败，`3` = 服务端超时，`4` = 仍在处理或等待中断。后两者必须区分：本地命令结束不等于服务器已超时。`--no-wait` 立即返回，`--wait-seconds` 控制本地等待，`--body-file` 可安全传入多行正文。一次请求最多重试 3 次并复用幂等键，避免因网络响应丢失重复发信。

## 本机 API

所有 Agent 请求携带 `Authorization: Bearer <导出包 config.json 中的 token>`。服务只公开到宿主环回地址，浏览器管理接口另有同源和会话 CSRF 校验。没有跨域访问支持。

| 方法与路径 | 用途 |
| --- | --- |
| `GET /api/health` | 公开的健康状态、实例 ID、是否已配置 |
| `POST /api/notify` | 仅提醒，异步返回 `202` 和持久请求记录 |
| `POST /api/ask` | 提醒并等待回复，异步返回 `202` 和记录 |
| `GET /api/requests/{id}?wait=25` | 查询或最长 25 秒长轮询原请求 |
| `GET /api/efficient-mode` | 读取模式、待收件数与会话租约状态 |
| `PUT /api/efficient-mode` | 仅前端设置模式，Agent 无权更改 |
| `GET /api/inbox?limit=50` | 查看未确认微信收件，不标记已读 |
| `POST /api/inbox/claim` | 会话领取／长轮询收件，最长 25 秒 |
| `POST /api/inbox/ack` | 确认消息已纳入会话，不代表已执行 |

创建请求的 JSON：

```json
{
  "subject": "需要你确认",
  "body": "问题、选项、推荐方案和超时后的处理计划",
  "agent_name": "Codex",
  "timeout_seconds": 300
}
```

`timeout_seconds` 只用于 `/api/ask`，默认 300 秒，范围 30–86400。建议每次创建设置 `Idempotency-Key`（8–200 字符）；同键同内容返回原记录，同键不同内容返回 `409`。邮件发出前的配置或参数失败也留存记录；身份验证失败及无法解析的传输请求不创建邮件记录。

记录状态为 `queued → sending → sent`（提醒），或 `queued → sending → waiting → replied / timed_out`（询问）；失败进入 `failed`。服务只负责发送、持久等待、记录回复与超时，**不会替 Agent 作出业务决策**。Agent 超时后按原有授权与任务风险决定继续或暂停，未回复不等于同意危险或不可逆操作。

详细接口、字段和内部模块契约见 [docs/contract.md](docs/contract.md)。

## 数据、安全与恢复

- 配置、API 令牌、请求、回复保存在固定 Docker 数据卷 `emailcall-data`。`stop/start`、容器重建及 `docker compose down` 保留数据；**不要运行 `docker compose down -v` 或删除该数据卷**，这会删除持久数据。
- 配置包含邮箱授权密码；采用本机文件权限保护，未实现额外静态加密。Docker 数据卷和导出 skill 均属于本机信任边界，拥有本机账户或 Docker 权限的程序可以读取它们。建议开启 FileVault 并使用可独立撤销的邮箱授权码。
- 容器以非 root 身份运行，根文件系统只读。只映射 `127.0.0.1:10086`；不要把端口改为 `0.0.0.0` 或转发到公网。Web API 校验 Host/Origin，Agent 令牌仅允许访问所需功能。
- SMTP 接收成功不保证邮件最终投递或已读。如果进程在 SMTP 发送期间崩溃，重启后会记录投递状态不确定的失败，避免盲目重发；查看邮箱和记录后再决定是否创建新请求。
- 回复按邮件引用头或请求标记关联，核对目标发件人，忽略自动回复，不修改邮箱已读标志。发件人地址核对不等于端到端身份认证；高风险操作仍应按原任务要求核实授权。
- 回复时请确认邮件客户端的「发件人」仍是配置中的目标邮箱。通过其他账号代发或转发后回复的邮件会显示为「回复未采纳」，记录中保留正文、来源及原因；它们不会结束 Agent 的等待。即使服务商的中文主题搜索漏检，服务也会按日期读取邮件头并在本机核对关联。
- 邮箱原文会作为用户数据呈现。Agent 不应把回复中的越权指令当成系统规则；界面以文本显示内容，避免邮件 HTML 执行。

备份时先停止容器，再使用 Docker Desktop 的数据卷导出功能备份 `emailcall-data`；恢复时导入同名数据卷。升级代码后重新运行启动器或 `docker compose up --build -d`。

## 本地开发与验证

```sh
python3 -m unittest discover -s tests -v
node tests/test_frontend.cjs       # 可选开发检查：前端请求丢响应后的幂等重试
python3 scripts/verify-docker.py  # 使用独立临时容器与卷验证重启、重建持久性
AGENTCALL_DATA_DIR=./data python3 -m gateway
```

本地直接运行默认只监听 `127.0.0.1:10086`。不要与容器同时占用同一端口。项目不内置演示邮箱、真实凭据或伪造生产记录。自动化测试包含本机真实 TLS SMTP/IMAP 协议回环（测试时使用 OpenSSL 生成临时证书），以及 CLI 到回复的完整链路；真实邮箱连通性仍需保存你自己的账户后通过 `test` 完成。微信协议的核心逻辑测试在纯标准库环境即可运行；安装 `httpx` 后会额外启用模拟微信服务器的完整协议集成测试。Node 只用于可选开发测试，运行应用不需要 Node。

实现选择与验收结果见 [docs/verification.md](docs/verification.md)。
