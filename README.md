# Flora · 芙洛拉

**Act to get things done—and to find things out.**

Flora 是可直接使用的通用 Agent：处理网页研究、文档、表格和外部工具，并交付带来源记录的报告。支持终端、本地 Web 界面和 Python API。

内核把模型的方案编译为可检查的程序。行动既推进任务，也可以帮助区分不同方案；真实工具结果用于检查消费者行为、综合局部合约，再决定如何继续。

Python 3.11+ · Linux / macOS / Windows WSL · Apache-2.0

## 安装并启动

```bash
git clone --branch general-agent https://github.com/wongzbb/Flora.git
cd Flora
python -m venv .venv
source .venv/bin/activate
python -m pip install '.[general,mcp,browser]'
python -m playwright install chromium

export OPENAI_API_KEY="你的模型服务密钥"
flora setup --model YOUR_MODEL_ID --base-url https://your-provider.example/v1
mkdir -p workspace
flora serve --workspace ./workspace --session ./sessions/research
```

打开终端打印的本地链接，即可输入任务、上传附件、查看执行过程并下载报告。浏览器自动化是可选工具，需在配置中明确启用和指定允许访问的域名；启动 Web 界面本身不需要安装 Chromium。发行包名为 `flora-lang`，请从本仓库安装。

## 直接交给它任务

```bash
flora agent "读取 sales.csv，按地区汇总收入，生成带来源引用的分析报告，并导出 Word。" \
  --workspace ./workspace --session ./sessions/sales

flora agent --session ./sessions/sales --status
flora agent --session ./sessions/sales --resume
```

省略任务文本进入交互终端。已有会话会保存配置、预算、真实执行记录和来源；恢复不会重置预算，也不会自动重发结果未知的操作。模型密钥只从环境变量读取。

需要详细模型设置时，编辑 `configs/general.json`；DeepSeek 兼容服务可参考 `configs/general-deepseek.json`，再加 `--config`。模型 ID、地址和参数必须符合你实际使用的服务。

## 已接入的能力

| 能力 | 用法 |
| --- | --- |
| 网页研究 | 网页正文、链接和原始内容留存；DuckDuckGo、Brave、Tavily、SearXNG 搜索 |
| 文档与表格 | PDF、DOCX、XLSX、CSV、UTF-8 文本；精确数值过滤与分组统计 |
| 报告交付 | 引用检查、Markdown 报告、DOCX/PDF/XLSX 导出、文件哈希校验 |
| 外部系统 | MCP stdio / Streamable HTTP；显式授权的 HTTP 服务和方法 |
| 浏览器 | Playwright 页面观察、点击、输入、截图；域名和动作授权 |
| 工作指南 | 从指定目录加载并固定内容的 Markdown skills |
| 连续工作 | 持久会话、来源分页、跨轮历史、暂停与恢复、执行预算 |
| 编码任务 | 保留 `flora code`，可在独立 worktree 中验证并生成补丁 |

扫描件 OCR、音视频理解、模型视觉输入不在内置文档解析器范围内，可接入相应 MCP 服务。Flora 的工具权限与路径检查不是操作系统沙箱；开启命令或 stdio MCP 时，应使用专门的工作环境。

## Python API

```python
from flora.general import GeneralAgent

with GeneralAgent(
    workspace="./workspace",
    session_dir="./sessions/research",
    profile={"provider": {
        "model": "YOUR_MODEL_ID",
        "base_url": "https://your-provider.example/v1",
        "api_key_env": "OPENAI_API_KEY",
    }},
) as agent:
    result = agent.run("比较两个附件，生成带引用的建议书。")
    print(result["status"], result.get("value"))
    print(result["artifacts"])
```

## 文档、代码与验证

- [通用 Agent 入门](https://github.com/wongzbb/Flora/blob/docs/docs/24-general-agent.md)
- [配置、工具、恢复与 API 完整手册](https://github.com/wongzbb/Flora/tree/docs)；其中 `manual.html` 可下载离线阅读。
- [本分支验证记录](reports/GENERAL_VALIDATION.md)
- [内核基线](https://github.com/wongzbb/Flora/tree/core)、[Coding Agent](https://github.com/wongzbb/Flora/tree/coding-agent)、[项目总览](https://github.com/wongzbb/Flora/tree/overview)

`src/flora/general/` 管理通用应用与接口；`agent/`、`engine/`、`language/`、`checks/`、`state/` 负责执行内核；`integrations/` 负责模型和工具边界。说明文档集中在 `docs` 分支。第三方组件作为正常依赖安装，其许可证随各自发行包保留；本应用没有移植 Hermes 或 Deep Agents 源码。
