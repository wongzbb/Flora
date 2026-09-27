# 配置与命令行

CLI 是 Python API 的薄入口。所有执行仍经过相同校验、预算、事件记录与效果边界。可以用 `flora` 或 `python -m flora`；后者便于确认使用当前虚拟环境的解释器。

## 直接使用命令

```bash
flora setup --model YOUR_MODEL_ID
flora ask "检查项目文档。" --workspace ./my-project
flora chat --workspace ./my-project --session ./my-agent-session
```

setup 只保存模型、endpoint 和凭据变量名称。直接使用命令的完整参数：

| 命令/参数 | 用途 |
| --- | --- |
| `setup --model MODEL` | 必需模型 ID；可选 --base-url、--api-key-env 或 --no-api-key |
| `ask TASK` | 执行一轮自然语言任务 |
| `chat` | 交互输入任务；/help、/status、/resume、/quit |
| `--model MODEL` | 覆盖环境与已保存模型配置 |
| `--base-url URL` | 兼容服务根路径 |
| `--api-key-env NAME` | 凭据环境变量名称，与 --no-api-key 互斥 |
| `--no-api-key` | 显式禁用密钥认证，常用于本地服务 |
| `--workspace DIR` | 选择工作目录并注册文件工具 |
| `--allow-commands` | 另行开放命令执行，要求 workspace |
| `--session DIR` | 持久会话目录；省略时为临时内存会话 |
| `--tools module:factory` | 可选宿主工具工厂；普通 Python 调用不需要它 |
| `--max-output-tokens N` | 每次编译的输出 token 预留上限 |
| `--quiet` | 不向 stderr 显示进度 |
| `--json` | ask 专用；stdout 输出完整 RunResult JSON |

无参数启动时，交互终端进入 chat，非交互输入打印帮助。ask 完成返回退出码 0，未完成或可报告错误返回 2，用户中断返回 130。进度不混入 stdout 的结构化结果。

ask/chat 不接受下面低层 run 的 `--config`，需要细粒度 RuntimeConfig/BudgetLimits 时使用 Python Agent 参数。`setup` 的轻量用户设置文件也不是下面四节配置对象的格式，二者用途不同。

## 低层运行配置

以下内容供自定义运行时与研究实验使用。日常任务不需要手写 bundle 或 adapter。

## 查看默认配置

```bash
python -m flora --version
python -m flora --help
python -m flora config
```

配置文件是严格 JSON，顶层只接受 `runtime`、`budget`、`provider`、`compiler` 四个对象。未知字段不会被静默忽略。完整 provider 与 compiler 参数在模型章节，预算参数在运维章节。

```json
{
  "runtime": {
    "max_steps": 100,
    "max_candidates": 3,
    "max_diagnostic_calls": 3,
    "enable_diagnostics": true,
    "enable_contracts": true
  },
  "budget": {
    "max_tool_calls": 40,
    "max_model_calls": 8,
    "max_input_tokens": 100000,
    "max_output_tokens": 65536,
    "max_wall_seconds": 900
  },
  "provider": {
    "base_url": "https://your-provider.example/v1",
    "model": "YOUR_MODEL_ID",
    "api_key_env": "AGENT_MODEL_API_KEY"
  },
  "compiler": {
    "max_output_tokens": 8192,
    "max_repairs": 1
  }
}
```

配置文件保存环境变量名称，不保存密钥值。CLI 中显式的 `--model`、`--base-url`、`--api-key-env`、`--max-output-tokens` 覆盖相应配置项。没有配置 model 的 authored bundle 运行不会创建模型 provider。

## RuntimeConfig 全部字段

| 字段 | 默认值 | 作用 |
| --- | --- | --- |
| `max_steps` | 200 | 运行时推进循环上限 |
| `max_candidates` | 3 | 活跃正常候选与 bundle 正常程序数量上限 |
| `max_diagnostics` | 2 | 单 bundle 诊断程序上限 |
| `max_diagnostic_calls` | 3 | 每任务独立插入诊断次数上限 |
| `pure_fuel` | 50,000 | 每段正常纯执行与局部检查的燃料 |
| `diagnostic_fuel` | 10,000 | 诊断假想续行的燃料 |
| `max_value_bytes` | 1,048,576 | VM 值与寄存器数据大小上限 |
| `max_tool_output_bytes` | 4,194,304 | 工具返回的忠实观察大小上限 |
| `max_contexts_per_program` | 64 | 单候选保留的消费者上下文上限 |
| `max_context_bytes` | 1,048,576 | 保留消费者上下文集合的总编码上限 |
| `max_contract_bytes` | 1,048,576 | 保留合约样本集合的总编码上限 |
| `max_reports` | 256 | 运行报告保留数量 |
| `max_reports_bytes` | 262,144 | 报告集合总编码上限 |
| `max_compile_cycles` | 30 | 剩余程序编译周期上限 |
| `enable_diagnostics` | true | 是否参与诊断评分和选择 |
| `enable_contracts` | true | 是否捕获消费者证据、登记复用变体并自动条件路由 |

编译器本身仍限制最多 3 个正常程序、2 个诊断。Runtime 会把已配置 LLMCompiler 的候选数量上限收紧到运行时限额，不能提高配置越过发布版的 3 / 2 界限。内部 alternative 最多含 16 个语法分支，但展开时仍受活跃候选上限约束；被配额裁剪不算信息收益。

`diagnostic_fuel` 受诊断检查器上限 50,000 约束。合约与重放 fuel 的公共检查 API 上限为 1,000,000。默认参数适合入门；扩大样本、fuel 或字节限额会增加 CPU、内存与持久化成本。

## demo

```bash
python -m flora demo calendar --output calendar-result.json
python -m flora demo pagination --output pages-result.json --trace pages.sqlite3
python -m flora demo calendar --no-diagnostics
python -m flora demo pagination --no-contracts
```

`--output` 保存完整演示结果、trace 与 contracts；标准输出只显示结果与演示 world 的最终观测。`--trace` 必须为新路径，防止把新建的内存演示 world 与旧事件混在一起。

## validate

```bash
python -m flora validate program.json
python -m flora validate bundle.json --bundle
```

只验证格式与静态语义，不执行工具、不请求模型。未接入 adapter 的离线校验无法验证真实业务权限与工具实现。bundle 必须包含有效锚点字段；真正安装时还会与当前 trace 比较。

## run：执行手写 bundle

先创建自己的工具适配模块 `my_tools.py`，导出不带参数的 factory，返回 `ToolRegistry`、`list[ToolSpec]` 或 `AgentEnvBridge`。该 Python 模块必须能由当前环境 import。

```bash
python -m flora run \
  --adapter my_tools:create_tools \
  --bundle bundle.json \
  --task "Read all permitted record IDs." \
  --trace task.sqlite3 \
  --output result.json \
  --trace-output trace.json
```

交付包还附带可直接运行的完整 bundle，不需要先编写 my_tools：

```bash
python -m flora validate examples/calendar.bundle.json --bundle
python -m flora run \
  --adapter flora.examples:calendar_tools \
  --bundle examples/calendar.bundle.json \
  --task "Move A to 30 minutes after the current start of B." \
  --trace calendar-authored.sqlite3 \
  --output calendar-authored-result.json
```

`examples/pagination.bundle.json` 配合 `flora.examples:pagination_tools` 可运行另一个示例。`calendar.program.json` 是读取后修改的诊断程序；`pagination.program.json` 是读取实际 data 字段的解析程序，适合对相应演示轨迹进行重放。

这是宿主能力的显式加载；adapter 参数不能由模型输出控制。普通工具注册表的新任务必须提供 `--task` 或 `--task-file`，二者互斥。工厂返回 AgentEnvBridge 时，可以省略任务文本并使用 `bridge.instructions`；显式任务文本优先。CLI 同时传递 bridge 的真实 completion guard，不会丢掉根会话完成约束。带有已有日志或检查点的路径要求 `--resume`。

完整 bundle 字段为 `programs`、`incumbent`、`diagnostics`、`expected_epoch`、`expected_digest`，以及可选 `revisions`。新空 trace 的 digest 是 64 个零；更推荐从 runtime 或 trace 对象动态读取，避免手工猜测历史状态。

## run：使用固定模型编译任务

```bash
python -m flora run \
  --adapter my_tools:create_tools \
  --task-file task.txt \
  --config settings.json \
  --trace live.sqlite3 \
  --output live-result.json
```

也可用 CLI 显式设置 provider 的几个常用参数：

```bash
python -m flora run \
  --adapter my_tools:create_tools \
  --task "Complete the permitted task." \
  --model YOUR_MODEL_ID \
  --base-url https://your-provider.example/v1 \
  --api-key-env AGENT_MODEL_API_KEY \
  --max-output-tokens 8192
```

这会实际调用已配置模型，可能计费。服务地址、模型 ID、密钥权限和工具适配器需要由部署者配置。本版本交付验证没有把文档模板当作已完成的真实模型测试。

## run：恢复现有任务

```bash
python -m flora run \
  --adapter my_tools:create_tools \
  --trace task.sqlite3 \
  --resume \
  --config settings.json
```

恢复排斥新 `--task`、`--task-file` 和 `--bundle`，这些非法组合在加载 adapter 工厂之前检查。原任务、运行时配置、预算及程序来自检查点。provider/compiler 可重新建立连接，但必须使用同一任务允许的模型和能力。adapter 必须连接原来的外部系统状态；创建一个新的内存 world 不构成环境恢复。工厂返回 AgentEnvBridge 时，其 completion guard 也用于恢复原检查点。

## inspect

```bash
python -m flora inspect task.sqlite3
python -m flora inspect trace.json --full
python -m flora inspect calendar-result.json
```

支持 SQLite、trace export JSON 或包含 trace 的演示 JSON。默认输出哈希校验结果、epoch、digest、事件 ID、工具名、状态和是否存在检查点。`--full` 输出完整内容，可能包含业务数据。SQLite 检查使用只读连接。

## replay

```bash
python -m flora replay program.json trace.json --inputs inputs.json --output replay-result.json
python -m flora replay program.json task.sqlite3 --memory initial-memory.json
```

没有 adapter 参数，因此不能执行工具。`--memory` 必须是该历史开始时可见的初始 memory。记录不匹配时返回 diverged，记录不足返回 exhausted，结果未知返回 interrupted_unknown 或 unknown；这些都是有效分析结果，需要读取 JSON 的 status。

## contracts

```bash
python -m flora contracts calendar-result.json
python -m flora contracts contracts.json --program program.json --relation DEFINED_PREFIX
```

读取并重新计算保存的合约证据。未指定 program 时输出通过、失败、未知数量；指定 program 后按具体程序 digest 拟合 guard。混合关系必须明确选择 relation。检查不请求模型或业务工具，但会消耗纯计算资源。

## resolve

```bash
python -m flora resolve task.sqlite3 0 \
  --outcome confirmed-outcome.json \
  --reason "Confirmed using the service audit record identified in the incident log."
```

`--outcome` 是已核实的状态信封文件，而不是字符串内联 JSON。例如 returned 需要 status 与 value。只允许解决已有 pending 或 interrupted_unknown；不会再次调用原工具，也不会把任务回滚。reason 必须说明真实核实依据，不能填写期待结果来继续运行。

## 退出码

CLI 解析或执行错误返回 2，并把简短 JSON 错误写到 stderr。正常完成与纯分析命令通常返回 0。run/demo 的 budget_exhausted、needs_program、incomplete、interrupted_unknown 返回 2。

replay 即使输出 diverged 或 exhausted 也可以返回 0，因为分析操作本身成功；自动化脚本必须同时检查 JSON status，不能仅依赖进程退出码判断任务成功。
