# 模型配置与常见接入

Flora 自身不提供模型。你需要可访问的 Chat Completions 兼容服务，或用 Python 自定义 provider 接入已有后端。模型负责理解任务、生成内部程序，并在获得真实工具结果后按需再次思考；执行、预算和持久化由运行时管理。

## CLI 配置的优先级

从高到低为：命令行显式参数、`--config` 中显式配置的字段、`FLORA_*` 环境变量、`setup` 保存的设置、默认 endpoint 与凭据变量名称。模型 ID 必须由前面某种方式提供。

## DeepSeek 接入起点

Flora 提供 `configs/deepseek.json`，同时适用于 `ask`、`chat` 和低层 `run`。复制后按你的服务修改 base_url、model、api_key_env；文件不包含密钥。默认 URL 是官方服务，实际验收使用的是用户提供的兼容网关，不是官方端点验收。模型名称与参数是否可用以你的服务为准。

```bash
flora ask "读取 order.json，计算总价并写入 summary.json" --config configs/deepseek.json --workspace ./my-project
flora chat --config configs/deepseek.json --workspace ./my-project --session ./my-agent-session
```

密钥放在配置指定的环境变量中。若你的服务明确使用远程 HTTP，在自己的配置副本 provider 下显式设置 `allow_insecure_http: true`；不要把密钥写进配置。恢复同一会话时保持配置、工具和权限一致。

配置启用模型推理，显式选择 `reasoning_effort: low`、JSON 输出和每次 8192 token 上限。没有关闭 Flora 的诊断调度或合约检查。不要混淆模型供应商的 thinking 参数和 Flora 的双重控制：前者控制模型输出方式，后者决定真实工具行动与信息获取。

通用 provider 不自动猜测这些参数；换供应商时使用它实际支持的设置。仅关闭 thinking 曾在验收中产生字段错误，不能把“不截断”当作任务通过。即使格式合法，任务正确性仍需业务验收。

Python 也可读取同一配置，保持行为一致：

```python network
import json
from pathlib import Path
from flora import Agent

profile = json.loads(Path("configs/deepseek.json").read_text())
connection = dict(profile["provider"])
model = connection.pop("model")
with Agent(model=model, provider_options=connection,
           compiler_options=profile["compiler"], config=profile["runtime"],
           budget_limits=profile["budget"], workspace="./my-project") as agent:
    result = agent.run("读取 order.json，计算总价并写入 summary.json")
    print(result.to_dict())
```

```bash
flora setup --model YOUR_MODEL_ID \
  --base-url https://your-provider.example/v1 \
  --api-key-env MY_MODEL_KEY
flora ask "解释 Python 字典和列表的区别。"
```

| 设置 | 环境变量 | CLI 参数 |
| --- | --- | --- |
| 模型 ID | `FLORA_MODEL` | `--model` |
| 服务 URL | `FLORA_BASE_URL` | `--base-url` |
| 密钥所在环境变量的名称 | `FLORA_API_KEY_ENV` | `--api-key-env` |

注意第三行存放的是名称，例如 `MY_MODEL_KEY`。密钥值仍应放在真正的 `MY_MODEL_KEY` 环境变量里，而不是把密钥直接传给 `--api-key-env`。

默认保存位置为 `~/.config/flora/settings.json`。设置 `XDG_CONFIG_HOME` 后使用其下的 `flora/settings.json`；设置 `FLORA_CONFIG_HOME` 后使用该目录的 `settings.json`，优先级更高。文件只接受 `model`、`base_url`、`api_key_env`，不保存密钥。`--no-api-key` 可将认证变量设为 null，用于不要求密钥的服务；它与 `--api-key-env` 互斥。`setup` 验证配置格式，不会为了检查模型名称就自动发起一次收费请求。

## 无认证的本地服务

已启动兼容本地服务时，可以直接运行：

```bash
flora ask "给出一句欢迎语。" --model local-model \
  --base-url http://127.0.0.1:8000/v1 --no-api-key
```

`setup` 与 `chat` 也支持 `--no-api-key`。显式该参数会覆盖环境或已保存的认证变量设置。

## Python 配置

高层 Python API 读取 `FLORA_MODEL`、`FLORA_BASE_URL` 和 `FLORA_API_KEY_ENV`，不要求先运行 CLI setup。需要明确指定时：

```python network
from flora import Agent

with Agent(
    model="YOUR_MODEL_ID",
    provider_options={
        "base_url": "https://your-provider.example/v1",
        "api_key_env": "MY_MODEL_KEY",
        "timeout": 60,
    },
    compiler_options={"max_output_tokens": 8192, "max_repairs": 1},
) as agent:
    print(agent.ask("给出一个简短的日志排错流程。"))
```

`max_output_tokens` 是每次模型响应的预留上限，不是整轮任务预算。它应与 BudgetLimits 的会话总输出额度匹配；剩余额度不足以覆盖一次完整预留时，不会先发请求再假装未超预算。

## 兼容旧 token 字段的服务

默认发送 `max_completion_tokens`。某些服务要求 `max_tokens`，可显式配置：

```python network
from flora import Agent

with Agent(
    model="local-model",
    provider_options={
        "base_url": "http://127.0.0.1:8000/v1",
        "api_key_env": None,
        "max_tokens_parameter": "max_tokens",
    },
) as agent:
    print(agent.ask("输出一句欢迎语。"))
```

此示例需要你已经启动实际兼容服务；包不会自动下载或启动模型。Loopback HTTP 用于本地接入，远程 endpoint 默认要求 HTTPS。CLI 的轻量 setup 不暴露每个 provider 高级选项，需要这些控制时使用 --config 或 Python API。

## 截断与诊断

`model_call_finished.output_diagnostics` 记录 finish_reason、text_bytes，以及供应商提供时的 reasoning_bytes、reasoning_tokens；缺失字段表示未知，不能当作零。它不记录推理正文。总 output_tokens 仍按供应商 completion_tokens 计费口径记账，不重复加上 reasoning_tokens。

`finish_reason=length` 的结果永不执行，即使尾部看起来可补全。最多一次受原预算约束的紧凑重新生成；没有静默增加 token 配额。JSON 语法错误提供行列位置供修复，工具副作用不会在编译修复过程中执行。持续失败时保留会话与真实 receipt，明确返回未完成。

## 额外供应商参数

用 `provider_options["request_options"]` 传递模型实际支持的额外参数。不要假定所有服务支持同一 temperature、JSON mode 或 reasoning 参数。传输关键字段和预算字段不能由额外参数覆盖。

HTTP 响应必须是非流式、单 choice 的文本。默认不支持供应商原生工具调用、多模态内容和另一种响应协议；需要时实现自定义 provider，再交给 `Agent(provider=...)`。完整协议见后面的模型编译器参考。

## 模型怎样继续任务

工具返回之后，有些纯计算可以直接执行，例如读取 JSON 中的已知字段。需要再次理解新观察时，内部程序可以请求重新规划，运行时把实际状态与观察交给模型，再继续剩余任务。它不是要求模型在第一次请求中预知后续工具返回。

这一过程使用原有工具、真实轨迹和剩余预算，不会得到环境隐藏状态、评分、可回滚副本或免费模型调用。所有诊断、重新规划和格式修复都与任务推进共享额度。

## 常见故障

| 情况 | 优先检查 |
| --- | --- |
| `No model configured` | 是否运行 setup、设置 FLORA_MODEL，或显式传 model |
| 找不到密钥 | api_key_env 指定的变量是否存在；值是否传到了实际运行进程 |
| HTTP 401/403 | 服务商密钥和模型权限 |
| HTTP 404 | base_url 与模型 ID；不要重复追加 chat/completions |
| HTTP 400 | token 字段、request_options 和模型支持的请求格式 |
| 网络超时 | endpoint、代理、服务负载、timeout；不要假定服务端没有处理请求 |
| 模型返回无法编译 | 模型是否具备足够指令遵循和程序生成能力；查看受限修复报告 |
| 输出预算预留不足 | 每次输出上限与会话总剩余额度是否匹配 |

错误信息不回显原始 HTTP 错误正文或密钥。网络失败没有隐含自动重试；格式错误最多一次受预算约束的修复。发布包验证可用本地 HTTP fixture 检查协议与完整执行链，但这种测试不能证明某个真实模型的任务表现，实际验证记录见代码分支的 reports/VALIDATION.md。
