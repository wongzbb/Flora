# GeneralAgent API

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
| `session_key` | 可选进程内 API Key；兼容 provider 使用，不保存到 profile 或环境变量 |
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

## 子 agent 接口

在 profile 的 general.subagents 中启用后，主 agent 获得 `spawn_agent`、`agent_status`、`wait_agents`、`read_agent`、`resume_agent` 工具。它们通过正常内核效果边界调用，每个子任务建立独立的内核会话。

`on_event` 还会收到 `subagent_spawned`、`subagent_status`、`subagent_event`。子事件包含 agent_id 与 name，subagent_event 的 event 字段是该子内核的真实事件。界面不能把 candidate ID 当作独立 agent ID。

使用默认兼容 provider 时，各子 agent 建立独立传输对象。自定义 provider 若启用并行子 agent，宿主提供的 complete 实现必须支持并发调用；该自定义对象会被共享。

宿主可读取 `agent.delegation.agent_status()` 获取子任务列表；不要在模型执行过程中从旁路同时发起修改性宿主调用。完成条件会阻止主 agent 在仍有 queued/running 子任务时提前结束。子任务失败不会被伪装成成功；应检查每项 status 和实际 result。

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
| `terminal/cli.py`、`terminal/ui.py` | 交互命令行、任务执行视图 |
| `terminal/sessions.py`、`terminal/connection.py` | 目录会话索引与凭据引导 |
| `general/delegation.py` | 独立只读子 agent 的并行、预算与恢复 |
| `general/cli.py` | 显式 session 的脚本入口 |

新增业务工具可以直接复用 `flora.Agent` 的普通函数或 ToolSpec 接口，并沿用同一内核；如果扩展 GeneralAgent 工具集，应同时更新会话身份、声明的 Schema、生命周期、超时和未知结果语义。不要只往界面加按钮而绕过效果日志。
