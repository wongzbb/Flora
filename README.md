# Flora · 芙洛拉

**Act to get things done—and to find things out.**

Flora 是在终端里直接使用的通用 Agent：研究网页、处理文档和表格、接入外部工具，并交付文件与报告。像素形象、花形 Logo 和紫／薄荷绿配色延续项目总览。

在任何工作目录运行 `flora`，输入 Base URL、API Key 和 Model，即可开始。工作目录取当前路径，会话 ID 自动生成；密钥隐藏输入，只在当前进程中使用。

新建通用会话的主 agent 和子 agent 默认不限制累计模型调用、工具调用、输入／输出 tokens 和会话总时长，仍持续记录用量。启动时显示 `Budget: unlimited`。单次模型输出、请求超时、有限重试和并发保护仍有效；可以通过 profile 显式设置需要的预算上限。

Python 3.11+ · Linux / macOS / Windows WSL · Apache-2.0

## 安装一次，到处启动

安装 [pipx](https://pipx.pypa.io/stable/installation/) 后：

```bash
git clone --branch general-agent https://github.com/wongzbb/Flora.git
cd Flora
pipx install '.[general,mcp]'
pipx ensurepath
```

PATH 首次更新后打开一个新终端。在要处理的项目或文件夹里启动：

```bash
flora
```

按照提示输入原始值，不加引号。API Key 不回显、不写入配置、对话或环境变量。若服务支持 `/models`，会列出所有已返回候选，输入编号或完整模型 ID；服务不支持列表时可以手动输入。

终端应用不需要浏览器。需要网页自动化工具时，可安装 `.[general,mcp,browser]` 并为对应环境安装 Playwright Chromium，详见手册。

## 常用操作

```bash
flora                          # 当前文件夹，自动创建会话
flora -C /path/to/project       # 指定工作目录
flora --resume                 # 选择当前文件夹的历史会话
flora --resume SESSION_ID      # 直接打开指定会话
flora --config /path/profile.json
```

每次启动都会提示模型连接信息，恢复会话也一样。恢复使用原会话的地址、模型、工具和预算；API Key 可以更新。打开历史会话后可以继续对话；未完成的任务通过 `/resume` 明确续跑。换模型或能力配置时启动新会话。已保存会话保留创建时的预算配置；如果恢复后显示有限额度，`/new` 会采用当前默认值，原消费记录不会被清除。

在对话里输入任务，Enter 发送，Alt+Enter 换行，Tab 补全命令：

| 命令 | 功能 |
| --- | --- |
| `/attach PATH` | 将工作目录内的文件附到下一条任务，路径可含空格 |
| `/agents` | 查看真实子 agent 的任务、ID、状态 |
| `/agent ID` | 阅读子 agent 的实际结果 |
| `/resume-agent ID` | 显式继续中断的子任务 |
| `/history`、`/status` | 查看保留的对话或当前状态、预算 |
| `/log [CURSOR]` | 分页查看实际请求提示词、模型输出、工具参数和结果 |
| `/sources`、`/artifacts` | 查看来源与输出文件 |
| `/new`、`/exit` | 新建会话或保存退出 |

运行中 Ctrl+C 请求在下一个执行边界暂停，正在进行的请求会先返回或超时。提示符处 Ctrl+D 退出。详细命令和恢复规则见[终端手册](https://github.com/wongzbb/Flora/blob/docs/docs/30-terminal.md)。

运行时直接展示模型实际返回的响应与工具交互，并标出主 agent 和子 agent。模型支持流式输出时，内容随返回更新；程序通过严格校验后才执行。完整请求提示词可在 `/log 0` 中查看，`/log` 默认显示最近记录。

通用入口默认请求 JSON 输出，对暂时性连接错误、限流和部分服务端错误进行计量明确的有限恢复。认证失败、错误模型地址等会给出具体原因。断流产生的残缺程序不会执行；工具结果未知时不会自动重做。详见[故障处理](https://github.com/wongzbb/Flora/blob/docs/docs/28-general-operations.md)。

## 能力

| 能力 | 内容 |
| --- | --- |
| 网页研究 | 正文与来源留存；DuckDuckGo、Brave、Tavily、SearXNG |
| 文档与表格 | PDF、DOCX、XLSX、CSV、文本；精确数值过滤和分组统计 |
| 报告交付 | 来源引用检查、Markdown、DOCX/PDF/XLSX、文件哈希 |
| 多 agent | 主 agent 可委派独立只读子任务，默认最多 3 个并行；分别保存程序、效果日志、预算与结果 |
| 外部系统 | MCP stdio / Streamable HTTP；明确授权的 HTTP 服务和方法 |
| 浏览器工具 | Playwright 观察、点击、输入、截图，需配置域名与动作授权 |
| 工作指南 | 指定目录中的 Markdown skills，固定内容指纹 |
| 连续工作 | 按目录保存会话、跨轮历史、暂停和恢复 |
| 编码任务 | `flora code` 在独立 Git worktree 中验证并生成补丁 |

子 agent 默认负责读取、研究和分析；写文件及外部修改由主 agent 集中执行。每个子 agent 都运行 Flora 内核，独立于内核内部的候选程序。默认每个会话最多创建 8 个子 agent，预算单独计量；具体上限和权限见手册。

## 执行内核

模型的方案会被编译成可检查的程序。行动既能推进任务，也能帮助区分不同方案；真实工具结果和消费者检查用于综合局部合约，再决定如何继续。独立子 agent 同样使用这套机制，保留各自的轨迹与未知结果。

工具权限和路径检查不是操作系统沙箱。只有显式启用 `--allow-commands` 才向主 agent 提供命令执行；MCP 和浏览器按配置启用。扫描件 OCR、音视频理解和模型视觉输入可通过额外服务接入。

## 文档与代码

- [快速开始](https://github.com/wongzbb/Flora/blob/docs/docs/24-general-agent.md) · [终端操作手册](https://github.com/wongzbb/Flora/blob/docs/docs/30-terminal.md)
- [完整手册与 Python API](https://github.com/wongzbb/Flora/tree/docs)，`manual.html` 可下载离线阅读。
- [终端验证记录](reports/TERMINAL_VALIDATION.md) · [通用能力验证记录](reports/GENERAL_VALIDATION.md)
- [内核基线](https://github.com/wongzbb/Flora/tree/core) · [Coding Agent](https://github.com/wongzbb/Flora/tree/coding-agent) · [项目总览](https://github.com/wongzbb/Flora/tree/overview)

`src/flora/terminal/` 负责终端；`general/` 组合应用工具和子任务；`agent/`、`engine/`、`language/`、`checks/`、`state/` 负责执行内核；`integrations/` 负责模型与工具接入。完整说明文档独立放在 `docs` 分支。
