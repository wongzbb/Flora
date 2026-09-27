# Flora · 芙洛拉

把自然语言任务、模型和工具交给 Agent，直接运行。Flora 在内部生成可检查的程序，用双重控制选择任务行动与信息获取，并通过可综合合约检查消费者在已知输入上的行为。

Python 3.11+ · 运行时仅标准库 · Apache-2.0

## 安装

当前分支维护 Coding Agent；独立的内核基线位于 [core 分支](https://github.com/wongzbb/Flora/tree/core)，说明文档位于 [docs 分支](https://github.com/wongzbb/Flora/tree/docs)。

```bash
git clone --branch coding-agent https://github.com/wongzbb/Flora.git
cd Flora
python -m venv .venv
source .venv/bin/activate
python -m pip install .
```

Windows PowerShell 使用 `.venv\Scripts\Activate.ps1`。发行包名为 `flora-lang`，导入名与命令名为 `flora`；请安装本仓库，不要安装同名的其他项目。

## 命令行使用

```bash
export OPENAI_API_KEY="你的模型服务密钥"
flora setup --model YOUR_MODEL_ID --base-url https://your-provider.example/v1
flora ask "查看目录，说明这个项目如何启动。" --workspace ./my-project
flora chat --workspace ./my-project --session ./my-agent-session
```

密钥从环境变量读取，不写入配置。使用 DeepSeek 时，按服务实际支持的模型和地址调整 `configs/deepseek.json`，并通过 `--config configs/deepseek.json` 传入。内置文件工具需要 POSIX 环境，Windows 请使用 WSL；执行命令需显式增加 `--allow-commands`。

## Coding Agent

在独立 Git worktree 中完成编码任务、执行你指定的测试并导出补丁。目标仓库需要已有提交且工作区干净；会话目录放在仓库外。

```bash
flora code "修复空输入时的错误，并运行现有测试。" \
  --repo ./my-project --session ./flora-job \
  --test "python -B -m unittest discover -s tests"

flora code --session ./flora-job --status
flora code --session ./flora-job --diff
```

修改保存在 `flora-job/worktree`，补丁位于 `flora-job/changes.patch`。只有固定测试命令通过、且测试后代码未变化，才允许完成；原仓库不会自动被修改或提交。测试在宿主环境运行，worktree 不是安全沙箱。更多用法、恢复和 Python API 见[编码指南](https://github.com/wongzbb/Flora/blob/docs/docs/23-coding-agent.md)。

## Python 使用

```python
from flora import Agent

def lookup_order(order_id: str) -> dict:
    """查询订单当前状态。"""
    return {"order_id": order_id, "status": "shipped"}

with Agent(model="YOUR_MODEL_ID", tools=[lookup_order]) as agent:
    print(agent.ask("查询订单 A123 的状态。"))
```

工具可以是普通 Python 函数。`agent.run()` 返回包含状态、预算和报告的完整结果；`agent.ask()` 返回完成值，未完成时抛出带结果的异常。

## 项目结构

| 目录 | 职责 |
| --- | --- |
| `src/flora/coding/` | 编码任务、独立 worktree、测试证据与补丁 |
| `src/flora/agent/` | Agent 与任务接口 |
| `src/flora/engine/` | 执行、调度、效果与预算 |
| `src/flora/language/` | 程序编译、IR 与虚拟机 |
| `src/flora/checks/` | 合约、诊断与经验复用 |
| `src/flora/integrations/` | 模型、工具与工作目录接入 |
| `src/flora/state/` | 会话、轨迹与不透明对象 |
| `src/flora/interface/` | CLI、交互控制台与设置 |
| `src/flora/support/` | 错误、资源边界与值处理 |
| `src/flora/examples/` | 内置离线示例 |
| `configs/`、`schemas/`、`examples/` | 配置、格式定义与示例程序 |
| `reports/` | 验证与实验记录 |

## 文档与验证

[项目总览页面与像素 Logo](https://github.com/wongzbb/Flora/tree/overview)连接内核、Coding Agent、文档和研究入口。

[完整文档在 docs 分支](https://github.com/wongzbb/Flora/tree/docs)。下载其中的 [manual.html](https://github.com/wongzbb/Flora/blob/docs/manual.html)，用浏览器离线打开。手册包含入门、工具接入、模型配置、会话恢复、API 和内部机制。

`flora demo calendar` 可离线检查安装。公开仓库任务的逐项通过与失败见 [编码评测](reports/REPOSITORY_EVALUATION.md)。验证范围见 [验证报告](reports/VALIDATION.md) 和 [真实 API 验收](reports/LIVE_VALIDATION.md)。合约提供局部执行证据，不证明任意任务正确；模型表现取决于任务、模型和预算配置。
