# Flora 通用 Agent：在终端里开始

进入你想处理的文件夹，运行 `flora`。Flora 使用这个文件夹作为工作目录，自动创建会话，然后引导你连接模型。像素形象、花形 Logo、紫色与薄荷绿沿用 Flora overview。

## 安装一次

需要 Python 3.11+，以及 Linux、macOS 或 Windows WSL。Windows 用户在 WSL 中安装和启动；文件工具依赖 POSIX 路径与描述符语义。原生 Windows Python 不是当前支持的运行环境。

安装 [pipx](https://pipx.pypa.io/stable/installation/) 后：

```bash
git clone --branch general-agent https://github.com/wongzbb/Flora.git
cd Flora
pipx install '.[general,mcp]'
pipx ensurepath
```

如果 PATH 刚刚更新，打开一个新终端。之后无需激活虚拟环境，也无需进入 Flora 的源码文件夹。

`general` 提供 PDF、Word 和电子表格依赖；`mcp` 提供外部工具接入。终端显示所需的 Rich 和 prompt-toolkit 会随基本安装提供。需要浏览器工具时可安装 `.[general,mcp,browser]`，再通过 `pipx list` 查看虚拟环境位置，运行该环境内的 `bin/playwright install chromium`。这样安装的 Chromium 与实际使用的 Playwright 版本一致。终端本身不需要 Chromium。

## 启动

在自己的项目或文件夹里执行：

```bash
flora
```

屏幕依次提示以下三项。直接输入值，不加单引号或双引号：

| 提示 | 输入 |
| --- | --- |
| Base URL | 兼容 Chat Completions 的 API 地址，例如 `https://your-provider.example/v1` |
| API Key | 你的密钥；输入时隐藏，不写入会话文件或环境变量 |
| Model | 已列出的候选编号，或完整模型 ID |

若 Base URL 只有域名和端口，终端会补上 `/v1`；已有自定义路径则保留。不要填写 `/chat/completions` 请求地址。Flora 使用你输入的地址获取 `/models`，支持标准 `data[].id` 列表及 `has_more/last_id` 分页。获取失败不阻止启动，可以手填模型。候选列表只证明服务列出了模型，不保证账户有使用权限或模型能正确生成程序。

每次启动都会提示三项信息，恢复历史时也一样。不从上一次启动自动读取密钥。远程 HTTP 会发送未加密凭据，界面会提示该连接属性。

## 输入任务

例如：

```text
读取 sales.csv，按地区汇总收入，生成带来源引用的 analysis.md，并导出 analysis.docx。
```

按 Enter 发送。Alt+Enter 换行；支持多行粘贴、历史输入与 Tab 命令补全。运行时显示实际模型调用、工具执行，以及已创建子 agent 的状态。

文件已在工作目录时，可以直接提及文件名，也可以输入：

```text
/attach project brief.docx
```

路径不用额外引号，可以包含空格。它会作为下一条任务的文件路径提示，不会立即上传全部内容；agent 根据任务调用读取工具。输出文件直接生成在工作目录，`/artifacts` 可以查看其路径与哈希状态。

## 指定目录与恢复

```bash
flora -C /path/to/project
flora --workspace /path/to/project
flora --resume
flora --resume SESSION_ID
flora -C /path/to/project --resume SESSION_ID
```

`--resume` 列出选定工作目录的历史记录，包含标题、ID、轮数和状态。输入序号或 ID 打开。`--resume ID` 直接打开，支持不歧义且至少四位的 ID 前缀。

打开后展示最近两轮已完成对话；可以继续输入。若存在未完成任务，界面会提示，输入 `/resume` 明确续跑。已经结算的工具效果不会重新派发，预算不会重置。恢复需要保持原模型、API 地址、工具和技能配置；API Key 可以更新。更换模型或能力时，用不带 `--resume` 的 `flora` 创建新会话。

## 常用命令

| 命令 | 功能 |
| --- | --- |
| `/help` | 查看操作说明 |
| `/status` | 当前任务、状态、会话位置及预算 |
| `/agents` | 独立子 agent 的任务和状态 |
| `/agent ID` | 阅读子 agent 的结果，必要时按提示分页 |
| `/history` | 查看保留的历史对话 |
| `/sources` | 查看保存的来源 |
| `/artifacts` | 查看生成的文件 |
| `/new` | 当前目录创建新会话，沿用本次进程已输入的连接 |
| `/exit` | 保存并退出 |

提示符处 Ctrl+D 也会退出；Ctrl+C 清空正在输入的内容。任务运行时 Ctrl+C 请求暂停，已派发的调用会先返回或超时。

## 更多配置

```bash
flora --config /absolute/path/profile.json
flora --allow-commands
flora --no-subagents
flora --plain
```

JSON profile 用于 MCP、浏览器、搜索、预算和模型高级参数。终端仍会提示 Base URL、API Key、Model。`--allow-commands` 为主 agent 启用本地命令执行；子 agent 不继承这项权限。`--plain` 适用于低能力终端或日志记录。

日常使用详见「终端、会话与子 agent」；工具细节见「文档、表格与报告」及「MCP、浏览器与外部系统」；程序化调用见「GeneralAgent API」。
