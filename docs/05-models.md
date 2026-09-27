# 连接固定语言模型

模型在系统中承担“编译剩余任务”的角色，输出一个严格 JSON bundle。模型没有直接执行工具的接口。编译器将 bundle 交给 IR、工具名单、输入参数和历史锚点校验，只有通过后运行时才安装候选。

## 使用兼容 HTTP 服务

```python
from flora.budget import Budget, BudgetLimits
from flora.compiler import LLMCompiler
from flora.providers import OpenAICompatibleProvider

budget = Budget(BudgetLimits(max_model_calls=8, max_output_tokens=65536))
provider = OpenAICompatibleProvider(
    base_url="https://your-provider.example/v1",
    model="YOUR_MODEL_ID",
    api_key_env="AGENT_MODEL_API_KEY",
    timeout=60,
)
compiler = LLMCompiler(
    provider,
    max_output_tokens=8192,
    before_call=budget.before_model_call,
    on_usage=budget.record_model_usage,
)
```

上面只创建对象，不发送请求。将该编译器交给 Runtime 后，运行时会通过 `set_accounting()` 绑定自己的同一份预算与持久化回调；Standalone 编译调用则使用你显式设置的回调。将环境变量设为自己的密钥，并替换服务地址和模型 ID 后，才可实际编译。示例不声称任何供应商、模型或额度在当前环境中可用。

provider 向 `{base_url}/chat/completions` 发送一个非流式请求。`base_url` 通常应包含 `/v1`，不要再包含 `/chat/completions`。密钥在每次请求时从指定环境变量读取，错误消息不输出原始 HTTP 错误正文或密钥。

## 兼容服务的差异

默认输出 token 字段为 `max_completion_tokens`。使用只接受旧字段的兼容服务时：

```python
from flora.providers import OpenAICompatibleProvider

provider = OpenAICompatibleProvider(
    base_url="http://127.0.0.1:8000/v1",
    model="local-model",
    api_key_env=None,
    max_tokens_parameter="max_tokens",
)
```

loopback HTTP 可用于本地服务；远程 HTTP 默认拒绝。需要经过明确部署评估才能设置 `allow_insecure_http=True`。URL 不允许嵌入用户名、密码、query 或 fragment。HTTP 重定向被拒绝，以避免认证头转发到其他目的地。

`request_options` 可以包含目标模型支持的额外参数，例如某些服务的温度设置。本项目不会默认添加模型不一定支持的温度、JSON mode 或 reasoning 参数。传输关键字段、模型工具调用字段和输出预算字段不能通过该字典覆盖。

## Provider 协议

自定义 provider 只需实现以下方法：

```python
from flora.providers import ModelResponse

class MyProvider:
    def complete(self, messages, *, max_tokens, **kwargs):
        # 在这里调用你现有的模型服务，并返回实际文本与用量。
        response = self.backend(messages, max_tokens=max_tokens, **kwargs)
        return ModelResponse(
            text=response.text,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            request_id=response.request_id,
        )
```

这是适配器模板，`backend` 需要由宿主实现。usage 不可得时应传 `None`，不能估造 token 数。不得在 provider 内隐藏自动重试，否则编译器的一次调用可能被计算成多次请求。

模型返回的 tool calls、function calls、拒绝字段或非文本内容不能当作编译程序。只有一条文本 choice 被接受。重复 JSON key、NaN、Infinity、代码围栏和额外解释都不能绕过严格 bundle 解析。

## Bundle 的固定结构

每个正常程序项恰好包含 `id`、`program`、`inputs`；入口 inputs 必须准确匹配 IR 参数。候选与诊断 ID 在 bundle 内全局唯一，以 ASCII 字母开头，随后可用字母、数字、下划线、点、斜线或连字符，最长 64 字符。`incumbent` 必须指向正常候选。

顶层必需 `programs`、`incumbent`、`diagnostics`、`expected_epoch`、`expected_digest`，可选 `revisions`。修订最多三个，每个已存在目标最多一项；包含 id、target_candidate、program、migration、mode。目标来自实际 previous_programs，迁移入口只接受 context，不能访问任何工具。完整 schema 在 `schemas/compiler-bundle.schema.json`。

## 编译上下文

`CompilerContext` 包含 `task`、`tools`、`epoch`、`trace_digest`、`receipts`、`memory`、`reports`、`previous_programs` 和 `remaining_budget`。其中 task 与工具定义不被静默截断。

模型视图中的回执附有原始 `trace_index`。当上下文过大时，编译器先移除较旧回执，再移除报告、旧程序和 memory，并在 `visibility` 中说明遗漏。遗漏不等于空数据；模型仍可生成 `read_receipt` 程序来读取运行时允许的实际记录。

`max_context_bytes` 限制编译上下文 JSON 的字节数，不包含固定 system prompt；完整 HTTP 请求还受到 provider 的 `max_request_bytes` 限制。

若完整任务与工具定义本身已超过字节上限，编译失败，不会把它们悄悄裁掉后继续执行。

## 格式修复与预算

`max_repairs` 只允许 0 或 1。第一次响应的 JSON、IR 或 bundle 校验失败时，编译器可以发送一次有界的错误报告和前次输出片段，请模型生成完整修正结果。修复使用相同历史锚点，并且是一笔正常计费的模型调用。

网络错误不等于格式错误，不触发格式修复。预算回调失败也不被修复循环吞掉。过期锚点拒绝安装，不由编译器擅自改写为当前锚点。

## 离线编译器

`ScriptedCompiler` 接收 bundle 列表或一个以 `CompilerContext` 为参数的 callable。它用于确定性示例、集成和复现实验，不运行模型推理。返回内容仍通过完整校验，不能借此绕过工具与历史锚点约束。

```python
from flora.compiler import ScriptedCompiler

# bundle_factory 必须由应用提供，并使用传入的真实上下文锚点。
# compiler = ScriptedCompiler(bundle_factory)
```

## 模型参数完整参考

| 类别 | 参数 | 默认值 |
| --- | --- | --- |
| provider | `base_url` | `https://api.openai.com/v1` |
| provider | `model` | 必填 |
| provider | `api_key_env` | `OPENAI_API_KEY` |
| provider | `timeout` | 60 秒 |
| provider | `max_response_bytes` | 4 MiB |
| provider | `max_request_bytes` | 2 MiB |
| provider | `max_tokens_parameter` | `max_completion_tokens` |
| provider | `allow_insecure_http` | false |
| provider | `request_options` | 空对象 |
| compiler | `max_programs` | 3，范围 1–3 |
| compiler | `max_diagnostics` | 2，范围 0–2 |
| compiler | `max_repairs` | 1，允许 0 或 1 |
| compiler | `max_output_tokens` | 8192 |
| compiler | `max_output_bytes` | 2 MiB |
| compiler | `max_context_bytes` | 256 KiB |
| compiler | `max_visible_receipts` | 64 |
| compiler | `before_call`、`on_usage` | 无；生产使用应接入共享预算 |
