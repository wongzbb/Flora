# MCP、浏览器与外部系统

## MCP：接入业务工具

Flora 使用官方 MCP Python SDK 的 1.x 接口，依赖约束为 `mcp>=1.30,<2`。服务程序由你安装和配置，Flora 不自动下载或执行陌生服务。每个服务都需要明确的工具白名单。

stdio 示例：

```json
{
  "general": {
    "mcp": {
      "inventory": {
        "transport": "stdio",
        "command": "/absolute/path/to/python",
        "args": ["/absolute/path/to/inventory_server.py"],
        "cwd": "/absolute/path/to/server-directory",
        "env": {"INVENTORY_TOKEN": "FLORA_INVENTORY_TOKEN"},
        "tools": ["lookup_item", "list_stock"],
        "timeout": 30
      }
    }
  }
}
```

`command` 是可执行文件，`args` 是参数数组，不经过 shell。`env` 的键是子进程接收的变量名，值是宿主进程现有环境变量的名称。比如上例会把宿主 `FLORA_INVENTORY_TOKEN` 的值注入子进程 `INVENTORY_TOKEN`，不会把密钥值写入配置或模型工具描述。SDK 还提供正常启动进程所需的最小系统环境。

Streamable HTTP 示例：

```json
{
  "general": {
    "mcp": {
      "inventory": {
        "transport": "http",
        "url": "https://tools.example.org/mcp",
        "headers_env": {"Authorization": "FLORA_MCP_AUTH"},
        "tools": ["lookup_item"],
        "timeout": 30
      }
    }
  }
}
```

宿主的 `FLORA_MCP_AUTH` 应包含完整头值，例如 `Bearer ...`。HTTP MCP 是明确配置的服务授权，不使用网页搜索的通用公网目标规则。普通 HTTP 需要额外写 `allow_insecure_http: true`。端点不接受 URL 用户名、密码、查询串或 fragment；认证放在环境变量映射中。

仓库提供可直接运行的 `examples/general/inventory_server.py` 和示例库存 CSV。将 `command` 指向安装了 MCP 依赖的 Python，`args` 指向该文件的绝对路径，白名单设为 `lookup_item` 与 `list_stock`，即可让 Agent 查询 FL-101 等示例商品。它是可用的集成示例，不连接任何真实库存系统。

## MCP 工具如何出现在模型面前

连接初始化时读取工具目录并检查白名单。未知或重复工具、重复分页游标、过大的目录/Schema 会使初始化失败。每个服务最多授予 64 个工具。

Flora 内部名称类似 `mcp_inventory_0`，描述中包含服务原始工具名和原始 JSON Schema。参数封装为：

```json
{"arguments": {"item_id": "A123"}}
```

原始 Schema 由 `jsonschema` 完整验证，不会删除 `oneOf`、`anyOf` 或本地引用来假装兼容。指向外部 URL/文件的 Schema 引用被拒绝，避免验证参数时隐式读取额外资源。

结果包含 `source_id`、服务的 `isError` 标志，以及适合窗口大小的结果。大结果放入来源库并返回可分页读取的引用。MCP 图片等内容保留在原始协议结果中，但当前编译器是文本接口；返回图片字节不等于模型已经看到了图像。嵌入的资源链接也不会自动触发额外下载。

服务返回 `isError` 是已观察到的错误，可以由程序处理；调用后超时、断线或结果无法持久化则成为“结果未知”。连接会停止接受新的调用，不自动重发。恢复前应核对外部事实并按内核的已知结果解析流程处理。

MCP 的异步连接与关闭由同一个专用工作线程/异步任务拥有，避免在不同事件循环之间移动会话。关闭应用会关闭连接及其管理的子进程。重新连接不证明远端业务状态保持不变；旧的远端会话 ID、资源句柄等仍可能失效。

## 显式 HTTP 服务

适合固定的 JSON/文本 API，无需编写 MCP 服务：

```json
{
  "general": {
    "services": {
      "catalog": {
        "base_url": "https://api.example.org/catalog/",
        "methods": ["GET"],
        "headers_env": {"Authorization": "FLORA_CATALOG_AUTH"}
      }
    }
  }
}
```

模型可以通过 `agent_capabilities` 发现服务名和方法，然后调用：

```json
{"service": "catalog", "path": "items/A123", "method": "GET"}
```

`path` 必须保持在配置的 base_url 下。模型不能传任意认证头，也不能自行扩大方法权限。需要创建或修改对象时，由宿主明确授予 POST/PUT/PATCH/DELETE，并可传 JSON 对象 body。每个动作仅派发一次；服务幂等键与外部事务仍由服务本身负责。

已知 HTTP 非 2xx 响应会作为观测返回 `http_status`，不伪装成业务成功。动作发出后没有拿到可持久化的结果，会停止在未知状态。不要因为客户端超时就认定外部写入一定没发生。

## 浏览器

浏览器工具使用独立 Playwright Chromium context，不借用用户个人浏览器的登录态。配置至少需要域名列表：

```json
{
  "general": {
    "browser": {
      "allowed_hosts": ["app.example.org", "static.example.org"],
      "allow_private": false,
      "allow_actions": false,
      "headless": true,
      "timeout": 25
    }
  }
}
```

页面所需的图片、脚本和样式域名也需列入白名单。浏览器启动时固定已解析的主机地址，逐请求检查目标，阻止未授权请求、Service Workers 和 WebSocket。默认不授予点击和输入，不接受下载到任意路径，不提供模型可调用的任意 JavaScript 执行工具。

| 工具 | 用途 |
| --- | --- |
| `browser_open(url)` | 打开白名单 URL，返回正文、来源、临时 tab ID 与控件引用 |
| `browser_snapshot(tab)` | 读取当前渲染页面，重新发放控件引用 |
| `browser_click(ref)` | 单次点击；需 `allow_actions: true` |
| `browser_fill(ref, value)` | 单次填写；需 `allow_actions: true` |
| `browser_screenshot(tab, path)` | 创建新的视口 PNG 文件；模型得到文件回执，不是视觉输入 |
| `browser_close(tab)` | 关闭标签页并作废引用 |

最多保留 8 个标签页，快照文字最多 2 MiB，最多列出前 200 个控件，并标明是否截断。每次快照或动作会作废上一组控件引用，防止把旧序号误用于已经变化的页面。元素已经脱离 DOM 时会要求重新观察。

点击/输入回执只确认该动作已派发；下一次 snapshot 才观察其结果。动作超时会保留未知状态，不通过重试同一次点击掩盖不确定性。

浏览器进程关闭后，标签页、控件引用和登录状态不会自动复原。截图文件可以交给用户查看；当前模型编译器不会自动把截图作为图像输入。需要视觉或认证浏览器服务时，可以通过明确授权的 MCP 提供。

浏览器工具不是隔离多租户的安全沙箱。部署验证需在有完整 Chromium 支持的目标环境完成；本仓库验证报告会区分适配器检查与真实浏览器运行。

## Skills：明确安装的工作指南

```json
{"general": {"skills": ["/absolute/path/to/my-skills"]}}
```

指定目录下的 Markdown 文件会组成固定目录。最多 100 个文件，每个最多 64 KiB；读取拒绝符号链接。`list_skills` 列出名称、摘要和哈希，`read_skill` 读取正文。

内容在会话创建/重新打开时固定。修改指南后旧会话会拒绝静默继续，需要新建会话。指南是任务说明，不赋予新的网络、命令或业务权限，也不会执行其中的安装命令。仓库自带 `examples/skills/research.md` 作为可用指南示例。

## 第三方组件

应用代码基于 Flora 的现有内核独立实现，没有移植 Hermes 或 Deep Agents 的源码。MCP SDK、Playwright、文档解析器作为正常依赖安装，保留各自发行包中的许可证。独立实现应用功能不意味着可以删除实际使用的第三方组件许可。
