# 通用 Agent 配置参考

配置是严格 JSON，不允许重复键。顶层只接受 `provider`、`compiler`、`runtime`、`budget`、`general` 五个对象。未知字段不会被静默忽略。

## 最小配置

```json
{
  "provider": {
    "model": "YOUR_MODEL_ID",
    "base_url": "https://your-provider.example/v1",
    "api_key_env": "OPENAI_API_KEY"
  }
}
```

```bash
flora agent "任务文本" --workspace ./workspace --session ./sessions/task --config profile.json
```

显式 `flora agent ... --session DIR` 脚本入口的模型选择顺序为命令行参数、配置中的明确值、`FLORA_*` 环境变量、已保存的 `flora setup` 设置，再到默认服务地址。模型 ID 没有可用值时会报错。已有会话直接使用其保存的完整配置；不通过修改环境中的模型名称暗中切换。

## provider

沿用内核的 Chat Completions 兼容 Provider，详见「模型编译器与 Provider」。常用字段如下：

| 字段 | 含义 |
| --- | --- |
| `model` | 服务真实支持的模型 ID |
| `base_url` | 通常以 `/v1` 结尾的接口前缀 |
| `api_key_env` | 密钥环境变量名；未提供进程内 session_key 时，null 表示无认证服务 |
| `timeout` | 连接／读取无数据的超时；交互启动默认 120 秒，直接 Provider 默认 60 秒 |
| `total_timeout` | 响应读取的整体时间上限，默认 600 秒；不会因 keep-alive 无限延长 |
| `stream` | 通用应用默认 true；显式 false 使用普通 JSON 响应 |
| `max_tokens_parameter` | 服务支持的输出上限字段，通常为 `max_tokens` |
| `request_options` | 服务特定的 JSON 模式、推理设置等附加参数 |
| `allow_insecure_http` | 是否明确允许非 HTTPS 服务 |

示例中永远不要填密钥值。脚本服务只支持 HTTP 时，需要明确配置 `allow_insecure_http: true`；交互输入 HTTP 地址时会显示传输提示并设置该项。这不会自动开放网页工具的私有网络访问；模型端点与通用网页工具具有不同授权边界。

通用应用会自动请求 `response_format: {"type":"json_object"}`，流式请求同时请求用量统计。显式 `request_options.response_format` 优先；不会覆盖用户指定的模型推理或温度参数。仅当 HTTP 400／422 明确指出自动添加的字段不受支持时，才在有限恢复额度内移除那个字段重试；显式设置的 JSON／stream_options 不会被静默删掉。接口忽略 stream 并返回普通 JSON 时也可读取。

为较慢的服务建立新会话时，可使用以下 profile：

```json
{
  "provider": {"timeout": 180, "total_timeout": 600, "stream": true},
  "compiler": {"max_output_tokens": 16000}
}
```

这是超时与输出额度示例，不是所有模型都必须使用的设置。启动时仍输入地址、密钥、模型。已有会话保持原 profile 和已消费预算；不要修改其指纹来换参数。

## compiler 与 runtime

通用入口默认每次编译最多输出 12,000 tokens，最多进行一次格式修复；其它编译器和运行时字段沿用内核默认值。一次修复也是实际模型调用，占用同一份预算。

每次编译另有最多两次传输恢复机会，格式修复共享这两次机会。因此默认一次编译最多发起四次模型请求，且显式设置的预算可以更早阻止后续请求。每次 POST 都独立预留输出额度、记录调用和已知／未知用量；provider 内没有隐藏的网络重试。暂时性错误的等待通常为 1、2 秒，数字 Retry-After 最多等待 30 秒。工具操作不进入这个重试循环。

需要更多推理输出的服务可显式增加 `compiler.max_output_tokens`，如果还显式设置了累计 `budget.max_output_tokens`，需保证它足以容纳下一次完整预留。不要通过关闭语法校验来接受截断程序或不完整 JSON。

`runtime` 可以设置内核已有的诊断、合约、步骤数和资源上限。通用应用没有关闭双重控制或合约功能来伪造更高成功率。完整默认配置与字段见「配置与命令行完整参考」。

## budget

通用 Agent 新会话默认不设置累计预算上限，主 agent 与子 agent 均如此。JSON `null` 表示真正不限，`0` 表示零额度；没有用一个很大的数字冒充无限，也不写入非标准 JSON 的 Infinity。

| 字段 | 新建通用会话的默认值 | 范围 |
| --- | --- | --- |
| `max_model_calls` | null | 累计模型请求次数，包括格式修复和传输恢复 |
| `max_tool_calls` | null | 累计工具调用次数 |
| `max_input_tokens` | null | 累计已报告输入 tokens |
| `max_output_tokens` | null | 累计输出 tokens 与未知用量预留 |
| `max_wall_seconds` | null | 会话累计打开时长的限制 |

这一默认策略仅用于 GeneralAgent、`flora` 和 `flora agent`。底层 `Agent` / `BudgetLimits` 以及 Coding Agent 的默认值不变。单次编译的输出上限、请求超时、有限修复和重试、VM 执行步数、工具权限、子 agent 并发与数量上限继续生效。

预算可按需显式设置；未写出的字段仍然不限。下面只限制主 agent 的模型请求和每个子 agent 的模型请求，不设置其它累计限制：

```json
{
  "budget": {"max_model_calls": 100},
  "general": {
    "subagents": {
      "enabled": true,
      "budget": {"max_model_calls": 20}
    }
  }
}
```

`budget` 与 `general.subagents.budget` 都接受上述五个字段。主任务和每个子任务分别记录、分别执行明确设置的额度；不会把父额度误称为所有子任务的合计费用硬上限。

不限额度时仍正常记录所有消费。输出 tokens 在调用前预留，服务无法报告使用量时保守计量并增加 unknown_usage_calls；中断进程后的未结算预留也不退款。输入 tokens 的精确统计依赖服务返回，不声称具有供应商无关的精确货币计费能力。

会话保存完整的已解析预算配置；恢复保持它和累计消费。有限额的已保存会话仍有限额，不因当前默认值不同而被静默放宽。启动区域和 `/status` 会显示当前模式；要使用默认不限的新对话，在终端输入 `/new` 或重新运行不带 `--resume` 的 `flora`。自带显式额度的 profile 会继续生效。

默认不设时长上限，因此等待输入不会触发会话预算耗尽。如果用户显式设置 `max_wall_seconds`，它计算会话进程打开的时间，包括交互等待；进程关闭后的离线时间不计入。请求自身的 timeout 与 total_timeout 是独立的运行保护。

## general

| 字段 | 默认值 | 含义 |
| --- | --- | --- |
| `network` | 公网访问、25 秒、4 MiB、5 次重定向 | 网页与 HTTP 服务的网络边界 |
| `search` | DuckDuckGo HTML | 搜索服务选择 |
| `services` | 空 | 明确授权的 HTTP 服务 |
| `mcp` | 空 | MCP 服务与工具白名单，最多 8 个服务 |
| `browser` | 未启用 | 浏览器域名和动作权限 |
| `skills` | 空数组 | Markdown 指南目录的路径 |
| `allow_commands` | false | 是否暴露宿主命令执行 |
| `require_report` | false | 完成前是否要求本任务存在当前有效的登记成果 |
| `instructions` | 空字符串 | 最多 32000 字符的应用工作说明 |
| `subagents` | API 默认关闭，终端默认开启 | 独立只读子 agent 的并行、数量和可选 budget；累计预算默认不限 |
| `storage_bytes` | 268435456 | 来源库配额，16 MiB 至 1 GiB |

`require_report` 是文件交付检查，不是任务正确性 oracle。它只要求本轮至少一个通过报告/导出工具登记的成果，且当前文件仍匹配登记哈希。复杂任务可通过 Python 自定义应用层增加自己的验收规则，但不能假装能验证未知外部事实。

## network

```json
{
  "general": {
    "network": {
      "allowed_hosts": ["docs.example.org", "api.example.org"],
      "allow_private": false,
      "timeout": 25,
      "max_bytes": 4194304,
      "max_redirects": 5
    }
  }
}
```

`allowed_hosts` 是精确域名列表，不支持通配符。空列表允许任意通过公网地址检查的 HTTP(S) 主机。重定向仍必须满足相同白名单。

超时允许 1–120 秒；响应/请求体限制允许 1 KiB–16 MiB；重定向上限允许 0–10。跨来源跳转会移除 Authorization/Cookie。修改型 HTTP 方法不自动跟随重定向，也不自动重试。

此客户端直接连接已验证 IP，不自动继承 `HTTP_PROXY` / `HTTPS_PROXY`。需要代理的部署应提供明确的网络出口，或使用经过授权的 MCP 接入；不要把跳过地址检查当成透明代理支持。

## 搜索配置

```json
{"general": {"search": {"provider": "brave", "api_key_env": "BRAVE_API_KEY"}}}
```

```json
{"general": {"search": {"provider": "tavily", "api_key_env": "TAVILY_API_KEY"}}}
```

```json
{"general": {"search": {"provider": "searxng", "base_url": "https://search.example.org"}}}
```

搜索还支持 `duckduckgo` 和 `disabled`。SearXNG 实例需要开启 JSON 输出。Brave/Tavily 的 `base_url` 如有设置，应为该搜索操作的完整 endpoint；SearXNG 的 `base_url` 是实例前缀。没有相应账号或密钥时，不会伪造搜索结果。

## 常用命令

```bash
flora
flora -C /path/to/workspace
flora --resume
flora --resume SESSION_ID
flora --config /absolute/path/profile.json
```

交互启动每次都提示 Base URL、API Key 和 Model。新会话的提示输入覆盖 profile 中的 model 和 base_url；其余高级 provider 参数保留。会话恢复使用原配置，并验证输入地址和模型匹配。密钥只在内存中传给模型传输对象。

脚本自动化可继续使用显式 session 参数：

```bash
flora agent TASK --workspace DIR --session DIR --config profile.json --json
flora agent --session DIR --resume --json
flora agent --session DIR --status
```

脚本入口从 profile 指定的环境变量读取密钥。`--status` 不连接模型。日常自动会话和脚本管理会话的区别见「终端、会话与子 agent」。
