# 安装 EmailCall skill

此目录是从本机 EmailCall 网页导出的技能包，`config.json` 含 API 令牌，请勿公开分享或提交代码库。

在解压后的上级目录选择一条命令（需要 Python 3）：

```sh
python3 emailcall/install.py codex
python3 emailcall/install.py claude
python3 emailcall/install.py both
```

安装器复制技能并写入可识别的全局规则区块，要求任务完成时发送邮件、需要用户选择时发送问题并等待。保留原有规则；首次修改创建 `.emailcall-backup` 备份。**安装完成后，必须开启新的 Agent 会话**。

确认 EmailCall 容器正在运行，并在 [本机配置页](http://127.0.0.1:10086/frontend/) 保存邮箱和目标地址、通过邮箱连接测试。随后告诉 Agent：「使用 EmailCall 进行连通性测试，等待并复述我的邮件回复。」收到邮件后直接回复，Agent 能复述内容即测试成功。

在网页轮换 API 令牌后，需要重新导出并安装。停用可直接告诉 Agent；完整卸载使用 `python3 emailcall/install.py both --uninstall`，它会移除技能和该规则区块，保留其余规则。仅复制技能文件不能保证自动触发，建议使用安装器。

详细调用与超时处理见 [SKILL.md](SKILL.md)。
