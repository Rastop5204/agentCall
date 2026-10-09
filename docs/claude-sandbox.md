# Claude Code 沙盒与权限批准配置说明

agentCall 的 skill CLI 运行在 Claude Code 会话内，需要访问 `http://127.0.0.1:10086`（本机网关）。本文档记录为让这些脚本**免审批、免沙盒拦截**运行所做的配置，及背后的机制与调整方法。配置于 2026-10-09 完成，依据 code.claude.com 官方文档（Sandboxing / Permissions / Settings Reference）核对。

## 结论先行

在 **原生 Windows 11** 的 Claude Code 中：

1. **命令本来就不进沙盒**。官方文档明确：沙盒运行于 macOS、Linux 和 WSL2；原生 Windows 上 Claude Code 直接以非沙盒方式执行命令。因此本机不存在「沙盒阻断 localhost」的问题。
2. 真正会拦住 agentCall 的是**权限批准流程**：Agent 每次运行 `python ...agentcall.py ...` 都要请求批准；在自动批准模式下若无法交互确认，命令就无法执行——这与「服务明明在本机正常运行却用不了」的现象一致。
3. 因此本机配置的核心是 `permissions.allow` 规则；`sandbox.excludedCommands` 一并写入，用于将来在 **WSL2/macOS/Linux**（沙盒生效的平台）运行同一配置时，CLI 可豁免沙盒直连宿主 localhost。

## 已写入的配置（`C:\Users\<用户>\.claude\settings.json`）

```json
{
  "permissions": {
    "allow": [
      "Bash(python *agentcall.py*)",
      "Bash(python3 *agentcall.py*)",
      "PowerShell(python *agentcall.py*)",
      "PowerShell(python3 *agentcall.py*)"
    ]
  },
  "sandbox": {
    "excludedCommands": [
      "python *agentcall.py*",
      "python3 *agentcall.py*"
    ]
  }
}
```

保存于**用户级** settings.json，对所有项目生效，不受工作区信任对话框影响。

## 逐条解释

### `permissions.allow`

- 规则按 **Claude 实际写出的命令文本逐字匹配**（不区分就不匹配）：`Bash(python *agentcall.py*)` 匹配任何「以 `python ` 开头、且包含 `agentcall.py`」的命令，覆盖绝对路径、正反斜杠、带引号等各种拼写，以及全部子命令（`notify` / `ask` / `test` / `status` / `mode` / `inbox` / `ack`）与参数。
- 同时写 `Bash(...)` 与 `PowerShell(...)`：Windows 上 Agent 可能通过任一工具执行命令，二者规则形态相同。
- 同时写 `python` 与 `python3` 前缀：Windows 用 `python`；`python3` 变体为 WSL2/遵循项目原始文档的调用兜底。
- 附带效果：当命令在沙盒平台（WSL2 等）首次沙盒执行失败、Agent 以非沙盒方式重试时，**同一条 allow 规则自动批准该重试**，无需再次确认。
- 若希望更严格，可改为精确前缀（注意 JSON 转义反斜杠、且引号是字面字符）：
  `"Bash(python C:\\Users\\10100\\.claude\\skills\\agentcall\\scripts\\agentcall.py *)"`。
  代价是漏掉引号/斜杠变体时会重新弹出审批。

### `sandbox.excludedCommands`

- 每条使用与 `Bash(...)` 规则内容相同的语法。命中的调用**整体绕过沙盒**（无文件系统限制、无网络代理），直连 `127.0.0.1:10086`。
- 在原生 Windows 上此键**无实际作用**（沙盒本身不启用）；它是为 WSL2 等平台准备的：沙盒环境中每个命令的 `localhost` 是私有的，直连宿主端口只有豁免才能到达——这是官方文档对本场景给出的标准解法（`allowedDomains`/`NO_PROXY` 对直连 localhost 无效）。
- 注意：含 `cd`、重定向、`$(...)`、子 shell、`&&` 链接的复合命令即使命中模式也**保持沙盒**。因此 Agent 调用 CLI 时应保持单条直呼（不带前缀 cd/重定向），这同时也是 skill 文档的既有要求。
- 豁免≠放行：`excludedCommands` 只是把命令移出沙盒，仍走常规权限流程——所以必须与上面的 `permissions.allow` 成对出现才能「免沙盒 + 免审批」。

### 收件钩子（PostToolUse）不需要任何沙盒配置

安装器写入 `~/.claude/settings.json` 的 `hooks.PostToolUse` 钩子（`...python.exe ...agentcall.py inbox-hook`）由 Claude Code 直接启动：**钩子在沙盒之外运行**，拥有完整本机访问权限，不适用 `excludedCommands`/`allowedDomains`。它的生效条件是配置存在于 settings.json（已由安装器写入）且会话加载了 hooks——**需要开启新的 Claude Code 会话**。

## 验证方式

会话中直接运行（注意保持单条直呼）：

```powershell
python "C:\Users\<用户>\.claude\skills\agentcall\scripts\agentcall.py" mode
```

预期：立即输出 `channels_checked` 与 `efficient_mode` 两行 JSON，无审批弹窗、无沙盒拦截。本机已于 2026-10-09 验证通过（微信通道 `available: true`）。

## 回滚 / 调整

- 删除上述 `permissions.allow` 与 `sandbox` 两个区块即可恢复默认审批行为；收件钩子用 `python <skill-dir>\install.py claude --uninstall` 一并移除。
- 只想临时绕过：在命令首次触发审批时选择「总是允许」，Claude Code 会把等价的前缀规则写入项目级 `.claude/settings.local.json`。
- 严格沙盒模式下（`sandbox.allowUnsandboxedCommands: false`）Claude Code 会忽略非沙盒重试参数——若启用该模式，`excludedCommands` 是唯一出路，请保留它。
