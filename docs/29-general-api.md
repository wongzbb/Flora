# GeneralAgent API 与本地 HTTP

## 构造 GeneralAgent

```python network
from pathlib import Path
from flora.general import GeneralAgent

Path("workspace").mkdir(exist_ok=True)
profile = {
    "provider": {
        "model": "YOUR_MODEL_ID",
        "base_url": "https://your-provider.example/v1",
        "api_key_env": "OPENAI_API_KEY",
    },
    "general": {"require_report": True},
}
with GeneralAgent(workspace="workspace", session_dir="sessions/research", profile=profile) as agent:
    result = agent.run("阅读附件，生成带来源引用的 report.md。")
    if result["status"] == "completed":
        print(result["value"])
        print(result["artifacts"])
    else:
        print(result["status"], result.get("reason"))
```

| 参数 | 说明 |
| --- | --- |
| `session_dir` | 必填；持久会话目录 |
| `workspace` | 新会话必填；必须已存在；已有会话可省略 |
| `profile` | 五个配置对象；已有会话省略时使用已保存配置 |
| `provider` | 可选自定义 Provider；实现内核 complete 协议，不与 provider 配置混用 |
| `on_event` | 接收实际运行事件的回调；普通观察者异常不改变工具结果 |

GeneralAgent 管理来源库、技能目录、MCP/浏览器生命周期与应用交付检查。任务仍调用原来的 `Agent.run()` / `Agent.resume()`，使用同一套编译器、调度器、效果日志、合约与复用库。

它不是额外套了一层独立 ReAct 执行器，也不训练或替换用户选择的模型。候选分歧、信息行动和有限样本上的合约综合仍发生在内核；应用层只提供真实工具和可检查的文件条件。

## 方法与返回值

| 方法 | 返回 / 行为 |
| --- | --- |
| `run(task)` | 新任务，返回字典；已有未完成任务时拒绝覆盖 |
| `resume()` | 继续已有任务，返回相同格式字典 |
| `status()` | 当前本地状态、预算、任务、精确保留历史与最近结果 |
| `request_pause()` | 请求在下一次外部动作选择处暂停 |
| `artifact_status()` | 登记成果与当前文件哈希检查 |
| `agent_capabilities()` | 实际已配置服务、搜索、浏览器、格式与权限 |
| `close()` | 关闭内核句柄、工具连接和存储；有任务正在运行时拒绝直接关闭 |

典型结果：

```json
{
  "status": "completed",
  "value": "报告已生成",
  "reason": "",
  "workspace": "/absolute/path/workspace",
  "session": "/absolute/path/session",
  "task_key": "本轮应用任务标识",
  "artifacts": [
    {"path": "report.md", "sha256": "完整 SHA-256", "sources": ["src-000001"], "current": true, "current_task": true}
  ]
}
```

实际内核运行结果还包含 steps、epoch、trace_digest、budget 等字段。`paused` 是应用层明确暂停状态，不伪造已完成的内核返回值。

若保存配置已经由 CLI 创建，可直接重开：

```python network
from flora.general import GeneralAgent

with GeneralAgent(session_dir="sessions/research") as agent:
    result = agent.resume()
    print(result["status"])
```

只读持久快照：

```python
from flora.general.agent import saved_status
# 传入你已创建的会话目录，不触发模型或外部工具连接。
# snapshot = saved_status("sessions/research")
```

## 来源与事件

`agent.store.list_sources(offset=0, limit=50)` 返回元数据分页。

`agent.store.read_source("src-000001", offset=0, limit=6000)` 返回内容窗口。

`agent.store.events(after=0)` 返回最多 200 条事件，带下一游标和历史裁减标志。事件 ID 单调递增，但它不是内核 trace 的 event_id，不能拿 UI 事件编号去解析效果日志。

普通 Python 用户应优先通过 Agent 执行工具。直接调用 `agent.documents`、`agent.web` 等宿主适配器会绕过内核的效果日志；它们可用于宿主集成或调试，但不要把这种直接调用记录当作模型执行轨迹。

## 本地 HTTP 接口

`flora serve` 使用与 Python API 相同的 GeneralAgent。API 是单用户、本地认证协议，根页面外的请求需要：

```text
Authorization: Bearer <终端链接中的随机令牌>
```

| 方法与路径 | 参数 / 结果 |
| --- | --- |
| `GET /` | 返回应用页面 |
| `GET /api/status` | 当前状态、busy、最近结果和错误 |
| `GET /api/events?after=0` | 活动事件分页 |
| `GET /api/sources?offset=0` | 来源元数据分页 |
| `GET /api/source?id=src-000001&offset=0` | 来源内容窗口 |
| `GET /api/artifacts` | 成果与哈希检查 |
| `GET /api/download?path=report.md` | 下载已经登记的成果 |
| `POST /api/task` | JSON `{"task":"任务"}`；返回 202 表示受理 |
| `POST /api/resume` | JSON `{}`；继续未完成任务 |
| `POST /api/pause` | JSON `{}`；请求下一边界暂停 |
| `POST /api/upload?name=notes.pdf` | 原始文件字节；返回新文件相对路径与哈希 |

上传不是 multipart，需发送准确 Content-Length；最多 8 MiB。一般 JSON 请求最多 256 KiB。不支持 chunked 请求正文。下载的 `Content-Disposition` 使用 UTF-8 文件名。POST 受理不等于业务完成，应轮询 status/events。

API 不提供任意 shell、跨目录下载、凭据读取或扩大配置权限的接口。关闭服务的能力保留在启动该服务的宿主终端。

## 工具 Schema 与实现位置

| 文件 | 职责 |
| --- | --- |
| `general/agent.py` | 会话、工具组合、交付门槛、内核调用 |
| `general/storage.py` | 来源、成果、活动事件与独占锁 |
| `general/network.py` | HTTP 地址检查、DNS 固定、限额与未知结果 |
| `general/web.py` | 搜索、页面提取、受控 HTTP 服务 |
| `general/documents.py` | 文件读取、表格查询、报告与导出 |
| `general/_document_worker.py` | 有资源上限的文档解析子进程 |
| `general/mcp.py` | MCP 生命周期、原始 Schema 验证、工具结果 |
| `general/browser.py` | 浏览器生命周期与临时引用 |
| `general/skills.py` | 明确安装的工作指南 |
| `general/schemas.py` | 将参数上限与结构完整公开给编译器 |
| `general/cli.py`、`general/server.py` | 终端与本地 HTTP 入口 |
| `general/static/app.html` | 无外部前端构建依赖的 Web 应用 |

新增业务工具可以直接复用 `flora.Agent` 的普通函数或 ToolSpec 接口，并沿用同一内核；如果扩展 GeneralAgent 工具集，应同时更新会话身份、声明的 Schema、生命周期、超时和未知结果语义。不要只往界面加按钮而绕过效果日志。
