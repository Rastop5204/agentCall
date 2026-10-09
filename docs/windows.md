# Windows 适配说明

本项目最初基于 macOS 开发（启动器 `.command`、`python3`、LaunchAgents、`open` 命令等）。本文档记录 Windows 11 原生环境（非 WSL）的适配内容、使用方式与排查要点。适配于 2026-10-09 完成并在本机验证通过。

## 总览

| macOS 原实现 | Windows 适配 | 状态 |
| --- | --- | --- |
| `启动 agentCall.command` / `停止 agentCall.command` | `启动 agentCall.cmd` / `停止 agentCall.cmd`（调用 `scripts/start.ps1` / `scripts/stop.ps1`） | ✅ 新增 |
| `python3` 命令 | `python`（并主动探测 Store 占位程序，详见下文） | ✅ 已适配 |
| LaunchAgents 浏览器助手（`local.agentcall.browser`） | 「启动」文件夹快捷方式 + `pythonw` 静默运行 `watch-windows.py` | ✅ 新增 |
| `scripts/watch.py`（`fcntl` 锁 + `/usr/bin/open`） | `scripts/watch-windows.py`（`msvcrt` 锁 + `webbrowser`）；`watch.py` 本身改为跨平台可导入 | ✅ 新增 |
| skill 安装器钩子命令 `python3 '...'`（POSIX 单引号） | `subprocess.list2cmdline` 生成 cmd 兼容命令，使用安装时的 `sys.executable` 绝对路径 | ✅ 已修复 |
| CLI 输出编码 | `agentcall.py` 在 Windows 上强制 stdout/stderr UTF-8 | ✅ 已修复 |
| `gateway/store.py` 模块级 `import fcntl` | 守卫导入 + `msvcrt` 锁，本地开发模式 `python -m gateway` 可运行 | ✅ 已修复 |
| 测试套件（默认编码、python3、chmod 断言） | 显式 UTF-8、`encoding='utf-8'` 子进程、POSIX 条件断言；196 测试全过 | ✅ 已适配 |

核心网关服务运行在 Docker 容器内（Linux 容器，`python:3.12-slim`），本身与宿主系统无关，无需适配。适配的全部是**宿主侧**组件：启动器、浏览器助手、skill 安装器与 skill CLI。

## 前置条件

1. **Docker Desktop for Windows**：https://www.docker.com/products/docker-desktop/
2. **Python 3（宿主侧）**：skill CLI、安装器、启动器的辅助功能都依赖它。推荐用户级安装，无需管理员权限：

   ```powershell
   winget install --id Python.Python.3.12 --scope user
   ```

   > **重要**：Windows 自带的 `python3.exe`（位于 `%LOCALAPPDATA%\Microsoft\WindowsApps\`）在未从商店安装 Python 时只是**打开 Microsoft Store 的占位程序**，不能运行任何脚本。本项目所有 Windows 命令一律使用 `python`（或 `py -3`）。

## 日常使用（Windows）

| 操作 | 方法 |
| --- | --- |
| 启动服务 | 双击项目根目录的 **启动 agentCall.cmd**（等价于 macOS 的 `启动 agentCall.command`） |
| 停止服务 | 双击 **停止 agentCall.cmd**，或在 Docker Desktop 停止 `agentcall` 项目组 |
| 终端启动 | `powershell -NoProfile -ExecutionPolicy RemoteSigned -File scripts\start.ps1` |
| 打开前端 | http://127.0.0.1:10086/frontend/ （服务就绪后启动器会自动打开） |
| 安装 skill | `python agentcall\install.py claude`（在解压导出包后运行） |
| skill CLI | `python "<skill-dir>\scripts\agentcall.py" mode` 等，详见 SKILL.md |

启动器行为与 macOS 版一致：自动启动 Docker Desktop（最多等待 120 秒）、`docker compose build`、迁移旧 `emailcall` 容器（如存在）、`docker compose up --remove-orphans -d`、轮询 `/api/health` 直到就绪（最多 60 秒）、安装浏览器助手并打开前端。

## 浏览器助手（Windows 版）

对应 macOS 的 LaunchAgents 常驻助手，Windows 版安装到：

- 快捷方式：`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\agentCall browser watcher.lnk`（当前用户「启动」文件夹，登录后自动运行）
- 脚本副本：`%LOCALAPPDATA%\agentCall\watch-windows.py`
- 运行方式：`pythonw.exe`（无控制台窗口），每 3 秒检查 `/api/health`，仅当出现**新的服务实例**（`instance_id` 变化）时打开一次前端

不需要管理员权限、不读取邮件或令牌。移除方式（等价于 macOS 的卸载命令）：

```powershell
python scripts\install-watcher.py --uninstall
```

`scripts/install-watcher.py` 现已按平台分支：macOS 走 LaunchAgents，Windows 走启动文件夹快捷方式，其他系统提示不支持。

## 具体适配点（代码变更）

### 1. `skills/agentcall/install.py`：钩子命令的平台化

原实现固定生成 `python3 '路径' inbox-hook`，在 Windows 有两个致命问题：`python3` 通常只是 Store 占位程序；`shlex.quote` 的 POSIX 单引号在 cmd.exe 中不是引用符，整条命令会解析错误。

适配后：

```python
if sys.platform == "win32":
    command = subprocess.list2cmdline([sys.executable, script, "inbox-hook"])
else:
    command = "python3 " + shlex.quote(script) + " inbox-hook"
```

Windows 使用**安装时实际运行的解释器绝对路径**（`sys.executable`），不依赖 PATH；`list2cmdline` 生成 cmd 兼容的双引号引用（路径无空格时不加引号）。已安装机器上的实际钩子命令（位于 `~/.claude/settings.json`）：

```
C:\Users\<用户>\AppData\Local\Programs\Python\Python312\python.exe C:\Users\<用户>\.claude\skills\agentcall\scripts\agentcall.py inbox-hook
```

### 2. `skills/agentcall/scripts/agentcall.py`：控制台编码

Windows 管道输出默认使用本地代码页（中文系统为 cp936/GBK）。JSON Lines 里的中文与用户回复中的 emoji 会乱码，甚至触发 `UnicodeEncodeError` 使 CLI 以退出码 2 失败。适配：在 Windows 上把 stdout/stderr 重配置为 UTF-8（`errors="replace"`）。

### 3. `skills/agentcall/SKILL.md` / `README.md`：跨平台调用说明

命令示例补充「Windows 用 `python`（若不可用则 `py -3`）」及 PowerShell 形式示例。**Agent 在 Windows 上调用 CLI 时应使用 `python` 而不是 `python3`。**

> 前端导出的 skill ZIP 来自容器内 `skills/agentcall` 模板。源码修复后需 `docker compose build` 重建镜像，之后的导出才会带上修复（本次适配已重建并验证）。

### 4. `scripts/start.ps1` / `scripts/stop.ps1`：Windows 启动器

从 `start.sh` / `stop.sh` 移植，包含若干 Windows 特有的健壮性处理：

- **Python 探测**：`python`/`py` 逐个用 `-c "import sys"` 实测可用性（过滤 Store 占位程序），再按常见安装路径兜底（`%LOCALAPPDATA%\Programs\Python\Python3xx\`、`%ProgramFiles%\Python3xx\`）。找不到时降级为提示并跳过辅助功能，核心 Docker 流程不受影响。
- **`&&` 分隔符**：Windows PowerShell 5.1 不支持 `&&`，docker 就绪判断整体包在 `cmd /c "docker info >nul 2>&1 && echo yes"` 内执行；同时避免 PS 5.1 把 docker 的 stderr 包装成 ErrorRecord。
- **HttpClient 探活**：健康检查用 `System.Net.Http.HttpClient`（`UseProxy = $false`，与 macOS 版 `ProxyHandler({})` 语义一致，避免系统代理劫持环回请求）。PS 5.1 需先 `Add-Type -AssemblyName System.Net.Http`。
- **管道断管**：迁移脚本输出先完整捕获再取首行。直接 `... | Select-Object -First 1` 会提前关闭 Python 的 stdout 管道，让正常退出的进程也返回非零退出码（实测 -1）。
- **自动启动 Docker Desktop**：`Start-Process 'C:\Program Files\Docker\Docker\Docker Desktop.exe'` 等价于 macOS 的 `open -a Docker`。
- PowerShell 脚本保存为 **UTF-8 with BOM**（PS 5.1 无 BOM 时按 ANSI 解析，中文会乱码）。`.cmd` 文件内容仅 ASCII。

### 5. `scripts/watch-windows.py` + `install-watcher.py` Windows 分支

`fcntl`（Unix 文件锁）→ `msvcrt.locking`；`/usr/bin/open` → `webbrowser.open`；LaunchAgents → 启动文件夹快捷方式（`WScript.Shell` COM 经 PowerShell 创建，避免 pywin32 依赖）。

### 6. `gateway/store.py`：跨平台进程锁

`import fcntl` 原是模块级无条件导入，导致 **Windows 上整个 `gateway` 包无法导入**——README 文档的本地开发模式 `AGENTCALL_DATA_DIR=./data python -m gateway` 在 Windows 完全无法运行，7 个测试模块也因此加载失败。适配：`fcntl` 改为 `try/except` 导入，Windows 回退 `msvcrt.locking(LK_NBLCK)` 实现同样的「同数据目录单实例」语义；Linux 容器内行为不变。

### 7. `scripts/watch.py`：可导入与可测试

同上做 `fcntl` 守卫导入 + `_flock_blocking` 锁适配（POSIX 走 `flock`，Windows 走 `msvcrt`），使纯逻辑测试可在 Windows 运行；macOS 部署路径不变。

### 8. 测试套件 Windows 适配（`tests/test_skill.py`）

- 读取含中文的规则/状态文件时显式 `encoding="utf-8"`（macOS 默认编码即 UTF-8 所以原先无感；Windows 默认 GBK 会 `UnicodeDecodeError`）。
- 子进程调用 CLI 的用例显式 `encoding='utf-8'`（CLI 已强制 UTF-8 输出，父进程必须用同编码解码，否则读线程崩溃、`stdout` 变 `None`）。
- POSIX 权限位断言（`st_mode & 0o777 == 0o600`）加 `os.name == "posix"` 条件——Windows 的 `chmod` 只映射只读位。
- 宿主 Python 需安装 `requirements.txt`（`httpx`、`qrcode`）：安装后启用完整微信协议集成测试；不装时 `test_wxbot` 注入的空桩模块会让 `direct_bot.py` 的类型注解在类定义期求值失败（该行为与平台无关，macOS 裸环境同样需要 httpx）。

## 验证记录（2026-10-09，本机）

- **单元测试**：`python -m unittest discover -s tests` → **196 个测试全部通过**（5 个按设计跳过）。适配前仅能收集 71 个测试、含 12 个错误 1 个失败（fcntl 导入链、GBK 解码、python3 子进程、chmod 断言）。宿主已安装 `requirements.txt` 依赖。
- `python .../agentcall.py mode`：`channels_checked` + `efficient_mode` 事件正常，微信通道 `available: true`，中文 JSON 输出无乱码。
- `启动 agentCall.cmd` 全链路：Docker 检查 → 构建 → 迁移检查 → `compose up`（容器重建，微信会话凭数据卷保留）→ 健康检查 → 浏览器助手安装 → 打开前端，退出码 0。
- `停止 agentCall.cmd`：容器正常停止；再次启动后配置与微信登录状态保留。
- 修复版 `install.py claude`：写入 `~/.claude/CLAUDE.md` 通知规则区块与 `~/.claude/settings.json` PostToolUse 收件钩子（使用绝对路径 python.exe，cmd.exe 可直接执行）。
- 镜像已用适配后的源码重建两次（skills 模板与 gateway 修复均已进入容器），前端再次导出的 skill ZIP 自动包含全部 Windows 修复。

## 已知限制

- Windows 上 `python3` 依旧是 Store 占位程序；所有文档与 skill 说明已改为 `python`。若用户为 Store 版 Python 启用了真实别名，两者等价。
- 浏览器助手依赖「启动」文件夹（登录时启动）；Docker Desktop 在登录前自启的场景下，首次开机会晚于容器就绪，助手随后仍会打开前端（每实例仅一次）。
- `verify-docker.py`、`tests/*.cjs`（Node 可选检查）未逐一在 Windows 验证；核心路径（网关容器、skill、启动器、测试）均已实测。
- Claude Code 沙盒／权限批准相关配置见 [claude-sandbox.md](claude-sandbox.md)。
