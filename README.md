# Flora · 芙洛拉

**把任务、模型和工具交给 agent，直接运行。** 提供普通 Python 函数或工作目录即可；候选程序、双重控制调度和可综合合约在内部运行。

Python 3.11+ · 核心运行时仅标准库 · Apache-2.0 · 版本 0.1.0

## 命令行开始

本项目与其他同名软件独立；发行包名为 `flora-lang`，Python import 和命令名为 `flora`。请安装本次交付的 wheel，`pip install flora` 不是本项目的安装命令。

解压发布 ZIP 后，为本项目单独创建虚拟环境，再安装自带 wheel；不要复用已安装其他同名 `flora` 模块的环境：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --no-index dist/flora_lang-0.1.0-py3-none-any.whl
export OPENAI_API_KEY="你的模型服务密钥"
flora setup --model YOUR_MODEL_ID
flora ask "查看目录中的文件，说明这个项目怎么启动。" --workspace ./my-project
```

替换模型 ID、密钥和实际项目路径；上述 `--workspace` 示例适用于 Linux/macOS，Windows 使用 WSL。兼容服务可在 `setup` 时设置 `--base-url https://your-provider.example/v1` 和 `--api-key-env MY_MODEL_KEY`；设置只保存环境变量名称，不保存密钥值。Windows PowerShell 使用 `.venv\Scripts\Activate.ps1` 和 `$env:OPENAI_API_KEY="..."`。

连续交互与持久会话：

```bash
flora chat --workspace ./my-project --session ./my-agent-session
```

内置工作目录工具要求 POSIX 文件操作：Linux/macOS 可直接使用，Windows 请在 WSL 中运行这些工作目录命令，或在 Python 中传入自己的文件工具。文件工具随包提供；执行命令需要另外增加 `--allow-commands`。工作目录工具不等于操作系统隔离沙箱。

## Python 开始

设置 `FLORA_MODEL` 和模型密钥后，直接传入普通函数：

```python
from flora import Agent

def lookup_order(order_id: str) -> dict:
    """查询一个订单的当前状态。"""
    return {"order_id": order_id, "status": "shipped"}

with Agent(tools=[lookup_order]) as agent:
    answer = agent.ask("查询订单 A123 的状态，用中文回复。")
    print(answer)
```

这段代码会调用实际模型。函数签名与说明自动转换为工具描述，不需要手写 IR 或 adapter。`agent.run(...)` 返回带状态、预算与报告的完整结果；`agent.ask(...)` 返回完成值，未完成时抛出带结果的 `AgentRunError`。

## 文档与验证

使用 DeepSeek 兼容服务时，先按服务地址和模型 ID 调整 `configs/deepseek.json`，然后运行 `flora ask "你的任务" --config configs/deepseek.json --workspace ./my-project`。该配置显式控制推理强度和输出预算，同时启用诊断与合约。密钥只从环境变量读取；详细接入与故障诊断见手册「模型配置与常见接入」。

0.1.0 修复真实 API 测试暴露的问题，真实任务验收与保留的失败记录见 [LIVE_VALIDATION.md](LIVE_VALIDATION.md)。测试仅覆盖列出的任务与配置，不是任意任务正确性或生产认证。

打开 **[manual.html](manual.html)**：先看 CLI、Python 函数、工作目录与会话教程，内部语言、合约、诊断和研究实验放在后面。单文件可离线阅读，支持搜索、复制、主题切换和打印。

暂不连接模型时，`flora demo calendar` 可检查安装；这是确定性离线演示，不能代替真实模型评测。实际验证范围见 [VALIDATION.md](VALIDATION.md)。测试源码独立保管，按交付要求不进入 ZIP。

内部实现独立于 JAZ runtime 和 `realize`，不训练模型权重，不要求外部世界回滚或隐藏评分。模型任务可靠性、官方 benchmark 表现和生产环境承载能力需要相应实验验证。

从旧版 OpenHarness 迁移时，请先阅读手册最后的「从 OpenHarness 迁移」：新环境变量和设置目录需要显式切换；历史存储标识保持不变。
