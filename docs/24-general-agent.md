# Flora 通用 Agent：从任务到交付

Flora General Agent 把网页、文件、表格和外部服务接到同一个执行内核。你描述任务，它观察现有信息、执行工具、保存证据并交付结果。日常使用不需要编写内部程序。

这部分对应 `general-agent` 分支。独立内核在 `core`，编码应用在 `coding-agent`；当前分支同时保留编码入口。完整手册留在 `docs` 分支。

## 安装

需要 Python 3.11 或更高版本，以及 Linux、macOS 或 Windows WSL。文件工具依赖 POSIX 的描述符相对访问和不跟随符号链接语义，不支持直接在原生 Windows Python 上降低安全要求运行。

```bash
git clone --branch general-agent https://github.com/wongzbb/Flora.git
cd Flora
python -m venv .venv
source .venv/bin/activate
python -m pip install '.[general,mcp,browser]'
```

如需复现 Python 3.12 验证环境中的依赖版本，安装时追加 `-c constraints/tested-py312.txt`。这份约束记录实际使用版本，不替代依赖安全更新策略。

可按需要安装依赖：`.[general]` 提供 PDF、Word 和电子表格；`.[mcp]` 提供 MCP SDK 与完整 JSON Schema 验证；`.[browser]` 提供 Playwright。执行内核本身仍只依赖标准库。

使用网页自动化时，另外安装浏览器：

```bash
python -m playwright install chromium
```

Linux 缺少浏览器系统库时，按 Playwright 官方安装说明配置宿主系统。Flora 不会静默执行 sudo 或安装系统包。使用 Web 聊天界面本身不需要 Playwright；它由 Python 本地 HTTP 服务提供。

## 配置模型

```bash
export OPENAI_API_KEY="你的密钥"
flora setup --model YOUR_MODEL_ID --base-url https://your-provider.example/v1
```

`YOUR_MODEL_ID` 和示例地址必须替换为模型服务实际支持的值。密钥值放在环境变量中，配置文件只记录变量名。`flora setup` 与内核的直接使用入口共用非敏感设置。

需要设置输出长度、推理模式或预算时，复制并编辑 `configs/general.json` 或 `configs/general-deepseek.json`。DeepSeek 示例中的 `thinking`、`reasoning_effort`、`response_format` 并非所有兼容服务都支持；根据服务实际接口调整。对于输出 IR 的任务，推理模式可能明显影响格式可靠性。验证报告给出实际测试配置，不代表任意兼容模型都表现相同。

## 打开界面

```bash
mkdir -p workspace
flora serve --workspace ./workspace --session ./sessions/research
```

终端会打印 `http://127.0.0.1:端口/#token=...`。在同一台机器的浏览器中打开完整链接。链接中的随机令牌只用于这个本地服务，前端会将它保存在当前浏览器标签会话中并从地址栏移除。服务重新启动后，使用新打印的链接。

1. 在输入框描述目标，例如“比较两份方案，列出成本与缺失信息，生成建议书”。
2. 点击 Attach 上传文件；每个文件最多 8 MiB。上传会创建新的工作目录文件，不覆盖同名原件。
3. 点击 Run task。Activity 展示实际执行事件。
4. Sources 展示真实读取的网页、附件和外部结果；点击可分页查看保存的内容。
5. Artifacts 展示已登记的报告与导出文件，可直接下载。
6. 如果任务停在可继续状态，点击 Resume。Pause 会在下一次外部行动被选中、尚未派发时暂停；正在运行的调用会先返回或超时。

Sources 窗口显示的是某次读取留下的内容，不会自动刷新外部网页。Artifacts 会重新比较文件哈希；“文件变化或不可用”意味着当前文件与发布记录不一致。

## 在终端完成同一任务

```bash
cp examples/general/sales.csv workspace/sales.csv
flora agent "读取 sales.csv，按地区汇总收入，用来源引用标注数字，生成 analysis.md 并导出 analysis.docx。" \
  --workspace ./workspace --session ./sessions/sales
```

指定配置：

```bash
flora agent "阅读工作目录中的附件并写比较报告。" \
  --workspace ./workspace --session ./sessions/comparison \
  --config configs/general.json
```

省略任务文本进入交互模式：

```bash
flora agent --workspace ./workspace --session ./sessions/research
```

交互命令为 `/status`、`/resume`、`/sources`、`/artifacts`、`/exit`。`/sources` 展示来源首页，完整分页可通过 Web UI 或 Python 调用查看。

## 恢复与状态

```bash
flora agent --session ./sessions/research --status
flora agent --session ./sessions/research --resume
```

`--status` 只读取最后持久化的快照，不连接模型、MCP 或浏览器。进程正在工作时，快照可能落后于尚未完成的调用。

重新打开已有会话时会加载保存的配置。修改模型、工具、技能内容、预算或工作目录后，应创建新的会话目录。旧会话的实际执行历史、已消耗额度和未知结果不会被配置变化抹掉。

## 下一步阅读

- 「文档、表格与报告」：支持格式、引用、精确统计与导出。
- 「通用 Agent 配置」：每个配置字段与完整样例。
- 「MCP、浏览器与外部系统」：扩展能力与生命周期。
- 「通用 Agent 运维与恢复」：状态、预算、未知结果与部署。
- 「GeneralAgent API」：Python 与本地 HTTP 接口。
