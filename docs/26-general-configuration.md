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

新会话的模型选择顺序为命令行参数、配置中的明确值、`FLORA_*` 环境变量、已保存的 `flora setup` 设置，再到默认服务地址。模型 ID 没有可用值时会报错。已有会话直接使用其保存的完整配置；不通过修改环境中的模型名称暗中切换。

## provider

沿用内核的 Chat Completions 兼容 Provider，详见「模型编译器与 Provider」。常用字段如下：

| 字段 | 含义 |
| --- | --- |
| `model` | 服务真实支持的模型 ID |
| `base_url` | 通常以 `/v1` 结尾的接口前缀 |
| `api_key_env` | 密钥环境变量名；明确为 null 表示无认证服务 |
| `timeout` | 单次模型 HTTP 请求超时，单位秒 |
| `max_tokens_parameter` | 服务支持的输出上限字段，通常为 `max_tokens` |
| `request_options` | 服务特定的 JSON 模式、推理设置等附加参数 |
| `allow_insecure_http` | 是否明确允许非 HTTPS 服务 |

示例中永远不要填密钥值。服务只支持 HTTP 时，需要明确配置 `allow_insecure_http: true`。这不会自动开放网页工具的私有网络访问；模型端点与通用网页工具具有不同授权边界。

## compiler 与 runtime

通用入口默认每次编译最多输出 12,000 tokens，最多进行一次格式修复；其它编译器和运行时字段沿用内核默认值。一次修复也是实际模型调用，占用同一份预算。

需要更多推理输出的服务可显式增加 `compiler.max_output_tokens`，同时保证 `budget.max_output_tokens` 足以容纳下一次完整预留。不要通过关闭语法校验来接受截断程序或不完整 JSON。

`runtime` 可以设置内核已有的诊断、合约、步骤数和资源上限。通用应用没有关闭双重控制或合约功能来伪造更高成功率。完整默认配置与字段见「配置与命令行完整参考」。

## budget

未显式提供时沿用内核的累计会话额度：

| 字段 | 默认值 |
| --- | --- |
| `max_model_calls` | 30 |
| `max_tool_calls` | 200 |
| `max_input_tokens` | 200000 |
| `max_output_tokens` | 100000 |
| `max_wall_seconds` | 3600 |

额度覆盖同一个持久会话的所有轮次，包括初次编译、格式修复与重编译。恢复不会退款。输出 tokens 在调用前预留；服务无法报告使用量时会保守计费。输入 tokens 的精确统计依赖服务返回的使用量，不声称在任意服务上具有完全准确的调用前额度判断。

墙钟预算包含会话进程保持打开的时间，包括交互等待；进程关闭后的离线时间不计入。长时间使用 Web 界面时，请在新会话创建前设置合适的墙钟上限。预算是执行约束，不是后台自动续费或重置机制。

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

| 命令 | 用途 |
| --- | --- |
| `flora agent TASK --workspace DIR --session DIR` | 新任务 |
| `flora agent --session DIR` | 打开交互终端 |
| `flora agent --session DIR --resume` | 继续尚未完成的任务 |
| `flora agent --session DIR --status` | 离线读取已持久化状态 |
| `flora agent TASK ... --json` | 输出结构化结果 |
| `flora serve --workspace DIR --session DIR --port 8765` | 启动本地 Web 服务 |
| `flora serve --session DIR --port 0` | 使用系统分配的可用本地端口 |

共同参数为 `--config`、`--model`、`--base-url`、`--api-key-env`、`--quiet`。`--resume`、`--status` 与新任务文本互斥。已有会话的配置变更会被拒绝；配置新任务时使用新的会话目录。
