# 安装与五分钟开始

Flora 的日常入口是 `flora`。安装一次后，在要处理的文件夹里运行，不需要每次指定目录、命名会话或激活项目环境。需要 Python 3.11+、Linux／macOS／Windows WSL，以及可访问的 Chat Completions 兼容模型服务。

## 安装到命令行

先按 [pipx 官方说明](https://pipx.pypa.io/stable/installation/) 安装 pipx，再执行：

```bash
git clone --branch general-agent https://github.com/wongzbb/Flora.git
cd Flora
pipx install '.[general,mcp]'
pipx ensurepath
```

PATH 首次更新后打开新终端。发行包名是 `flora-lang`，命令名与 import 名是 `flora`；不要用 `pip install flora` 安装其他同名软件。

## 启动与对话

进入你要处理的目录：

```bash
cd /path/to/my-project
flora
```

按提示输入 Base URL、API Key、Model，值两端不加引号。密钥隐藏输入且不保存。若获取到模型候选，输入序号即可，也可以直接输入完整 ID。

然后描述任务，例如“查看目录中的文件，告诉我这个项目如何安装和启动”。Enter 发送，Alt+Enter 换行，Ctrl+D 退出。工作目录默认为启动位置；子会话和执行记录自动保存在用户状态目录。

## 继续之前的工作

```bash
flora --resume
flora --resume SESSION_ID
flora -C /path/to/project --resume
```

记录按工作目录区分。选中会话并重新输入模型连接后，可以继续对话；未完成的任务用 `/resume` 明确续跑。更多操作见「Flora 通用 Agent」与「终端、会话与子 agent」。

## 可选依赖与浏览器工具

| 安装项 | 能力 |
| --- | --- |
| `.` | 内核、终端、基础文件与网页工具 |
| `.[general]` | 另含 PDF、DOCX、XLSX 读取和导出 |
| `.[general,mcp]` | 另含 MCP 外部工具接入 |
| `.[general,mcp,browser]` | 另含 Playwright 浏览器适配器 |

浏览器适配器需要为所用 Python 环境安装 Chromium。可以通过 `pipx list` 查看虚拟环境位置，再运行该环境中的 `bin/playwright install chromium`。还需在 profile 中配置允许访问的域名及是否授权点击和输入。终端聊天无需浏览器。

## 源码开发环境

开发者也可使用虚拟环境：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[general,mcp]'
flora
```

这种安装方式需要激活对应虚拟环境，或直接运行其中的 `bin/flora`。日常不想处理环境切换时使用前面的 pipx 安装。

## 离线检查

```bash
flora --version
flora --help
flora demo calendar
flora demo pagination
```

这些操作不请求密钥、不连接真实模型。离线 demo 运行真实解释器和效果日志，不能代替真实模型任务效果评估。

## Python 和脚本入口

`flora ask`、`flora chat` 提供较低层的直接工具入口；`flora agent TASK --session DIR ...` 用于显式管理会话路径的脚本；`flora code` 提供独立 worktree 编码流程。它们各自的参数和授权方式见后面的 API 与使用指南。日常终端操作直接运行 `flora`。

## 目录与分支

| 位置 | 内容 |
| --- | --- |
| `general-agent` 分支 | 可直接使用的终端应用与通用能力 |
| `core` 分支 | 独立执行内核基线 |
| `coding-agent` 分支 | 独立编码应用 |
| `overview` 分支 | 项目总览及视觉设计 |
| `docs` 分支 | 手册源码、生成脚本、单文件 HTML 手册 |

测试代码不包含在交付 ZIP 中。验证结果见应用分支的 reports 目录。测试结果明确区分确定性传输夹具、真实模型请求和未验证的目标环境能力。
