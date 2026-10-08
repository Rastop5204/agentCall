# 安装 agentCall skill

此目录是从本机 agentCall 网页导出的技能包，`config.json` 含 API 令牌，请勿公开分享或提交代码库。

在解压后的上级目录选择一条命令（需要 Python 3）：

```sh
python3 agentcall/install.py codex
python3 agentcall/install.py claude
python3 agentcall/install.py both
```

安装器复制技能、写入可识别的全局规则区块（任务完成与长任务各阶段通知、需要用户选择时提问并等待），并安装 PostToolUse 收件钩子（Claude Code 到 `~/.claude/settings.json`，Codex 到 `~/.codex/hooks.json`），高效模式下自动向 Agent 注入微信新消息；Codex 需要在会话中信任该钩子。保留原有规则与配置；首次修改各文件创建 `.agentcall-backup` 备份。**安装完成后，必须开启新的 Agent 会话**。

确认 agentCall 容器正在运行，并在 [本机配置页](http://127.0.0.1:10086/frontend/) 配置微信扫码登录或邮箱备份，并通过对应渠道连接测试。随后告诉 Agent：「使用 agentCall 进行连通性测试，等待并复述我的回复。」收到消息后按提示回复，Agent 能复述内容即测试成功。

在网页轮换 API 令牌后，需要重新导出并安装。停用可直接告诉 Agent；完整卸载使用 `python3 agentcall/install.py both --uninstall`，它会移除技能、规则区块和收件钩子，保留其余规则与配置。仅复制技能文件不能保证自动触发，建议使用安装器。

详细调用与超时处理见 [SKILL.md](SKILL.md)。

升级会将旧 EmailCall 自动通知规则替换为 agentCall，保留其他规则及 API 令牌。包内 `scripts/emailcall.py` 是新客户端的兼容入口；已安装的旧 EmailCall 客户端仍可调用相同 API。
