# Python 快速开始

业务应用通常只需要 `Agent`、`invoke` 和普通 Python 函数。本章所有 `python network` 示例都会调用你配置的模型。先设置 `FLORA_MODEL` 与模型凭据；也可以在 `Agent(model="实际模型ID")` 中显式传入模型。

## 最少代码：一次调用

```python network
from flora import invoke

answer = invoke("用三句话解释为什么修改文件前应先读取它。")
print(answer)
```

`invoke` 创建一次临时 agent，完成后返回最终值并关闭本次会话资源。没有工具时，模型只能利用任务内容、传入数据与自己的知识完成任务。它不会因此拥有文件、网络检索或业务访问能力。

## 把普通函数交给 agent

```python network
from flora import Agent

orders = {
    "A123": {"status": "shipped", "carrier": "example-carrier"},
    "B456": {"status": "processing", "carrier": None},
}

def lookup_order(order_id: str) -> dict:
    """按准确的订单编号查询状态；找不到时返回 found=false。"""
    item = orders.get(order_id)
    return {"order_id": order_id, "found": item is not None, "item": item}

with Agent(tools=[lookup_order]) as agent:
    print(agent.ask("查询 A123 订单的状态，用中文解释结果。"))
```

函数名称变成工具名称，docstring 变成模型可见说明，参数类型和默认值用于生成参数约束。真实执行时调用的是这个函数。示例中的字典只是业务数据源；agent 的任务规划仍来自实际模型，没有预写最终回答。

工具应清楚表达任务原本拥有的能力。读数据库、发请求、提交订单等授权仍由函数和原业务系统负责。自动注册省去接口样板，不会替你的业务完成鉴权。

## 传入结构化数据

```python network
from flora import Agent

with Agent() as agent:
    result = agent.run(
        "根据 data 中的库存和需求判断哪些项目缺货，返回 JSON 对象。",
        data={
            "stock": {"paper": 3, "pens": 12},
            "requested": {"paper": 5, "pens": 4},
        },
    )
    if result.status == "completed":
        print(result.value)
    else:
        print(result.status, result.reason)
```

`data` 必须是严格 JSON 可表示的字符串键字典，内部保存为 `memory["data"]`。可以嵌套列表、字典、字符串、有限数值、布尔值与 null。不能把 DataFrame、数据库连接、任意类实例、NaN、文件句柄或 Python callable 作为 data 值传入；应先投影成真实 JSON 数据，或通过已注册工具访问原对象。

每一轮显式传入自己的 data。已完成历史保留任务和结果，不能把它当成自动保留全部输入对象与工具回执的承诺。

## ask 与 run 的区别

| 方法 | 成功时 | 未完成时 | 适用场景 |
| --- | --- | --- | --- |
| `agent.ask(task, data=...)` | 直接返回最终 `value` | 抛出 `AgentRunError`，其 `.result` 保留结果 | 脚本、应用中的便捷调用 |
| `agent.run(task, data=...)` | 返回 `RunResult` | 返回带状态和原因的 `RunResult` | 需要分状态处理的服务 |
| `invoke(task, data=..., **Agent参数)` | 返回最终 `value` | 与 ask 相同 | 一次性任务 |
| `agent.resume()` | 返回继续执行后的 `RunResult` | 保留具体停止状态 | 继续同一轮任务 |

```python network
from flora import Agent, AgentRunError

with Agent() as agent:
    try:
        answer = agent.ask("给出一个简短的数据库备份检查清单。")
    except AgentRunError as exc:
        print("尚未完成：", exc.result.status, exc.result.reason)
    else:
        print(answer)
```

构造参数错误、配置错误或工具签名错误仍会直接抛出相应异常；`AgentRunError` 不是把全部宿主错误包装成任务结果的通用容器。

## 多轮任务

```python network
from flora import Agent

with Agent(instructions="回答使用中文；缺少事实时明确说明。") as agent:
    print(agent.ask("为一个三人的开源项目列出发布前检查项。"))
    print(agent.ask("把刚才的内容压缩成按优先级排列的五项。"))
```

同一 Agent 会为后续任务提供保留的已完成任务与结果，并共享预算。它不会在每轮重新赠送一份模型或工具额度。新的轮次读取当前真实工具状态；前一轮已经提交的外部动作不会因新一轮而撤销。

当上一轮未完成时，先调用 `resume()` 继续它，或关闭并明确开始新的独立会话；不能通过一句新任务悄悄覆盖尚未解决的真实效果。

## 观察进度

```python network
from flora import Agent

def show_event(event: dict) -> None:
    print(event.get("kind"), event.get("epoch"))

with Agent(on_event=show_event) as agent:
    result = agent.run("列出四个适合小团队的发布检查项。")
    print(result.status)
```

常见进度类别包括 `model_call_started`、`action_selected`、`replan_requested` 和 `checkpoint_restored`。`on_event` 接收 JSON 可表示的事件字典；`kind` 标识事件类别，具体事件可带 `epoch` 和报告细节。回调供显示、记录和观测使用，不是中止工具或批准动作的接口。回调异常与任务执行隔离，不应依赖抛异常来改变 agent 决策。使用业务日志时，应按自己的访问控制保护任务数据。

## Agent 构造参数

| 参数 | 默认行为与用途 |
| --- | --- |
| `model` | 未传时读 `FLORA_MODEL`；自定义 provider 时可以省略 |
| `provider` | 默认创建兼容 HTTP provider；可传已实现 `complete()` 的 provider |
| `tools` | 普通函数列表、ToolSpec 列表、别名映射或 ToolRegistry；默认无业务工具 |
| `workspace` | 可选的现有工作目录；启用标准文件工具 |
| `allow_commands` | false；启用命令工具时还需要 workspace |
| `session_dir` | 未传时会话只在内存；传目录时保存会话元数据和每轮 SQLite |
| `instructions` | 所有轮次使用的宿主任务指导文本 |
| `on_event` | 可选事件回调 |
| `config` | RuntimeConfig，约束每轮执行与内部资源 |
| `budget_limits` | BudgetLimits，共享会话预算 |
| `provider_options` | provider 配置字典，例如 base_url、api_key_env、timeout |
| `compiler_options` | 编译器配置字典，例如 max_output_tokens、max_repairs |
| `completion_guard` | 可选宿主完成条件；不是隐藏评分接口 |

`Agent` 是同步接口；同一实例或同一持久会话不能并发运行。需要服务并发时，为各个独立会话分别创建实例和状态目录。完整签名与底层数据结构见后面的 API 参考。

## 工具名称、类型与默认值

装饰器是可选的。需要显式名称或说明时：

```python
from flora import tool, make_registry

@tool(name="find_inventory", description="按商品编号读取库存数量。")
def inventory(sku: str, warehouse: str = "main") -> dict:
    return {"sku": sku, "warehouse": warehouse, "available": 7}

registry = make_registry([inventory])
assert registry.names == ("find_inventory",)
assert registry.call({"tool": "find_inventory", "args": {"sku": "P1"}})["available"] == 7
```

`@tool` 返回一个 ToolSpec，而不是保留可任意直接调用的函数包装器。如果只想保留原函数对象，直接把普通函数传入 Agent；需要别名可使用 `tools={"find_inventory": inventory_function}`。

支持推断 `str`、`int`、`float`、`bool`、`list[T]`、`dict[str, T]`、`Optional[T]`、基础类型 union 和 `Literal`。缺少类型或暂不支持的类型注解不会触发任意代码求值，对应参数接受 JSON；需要更强约束时用 `@tool(input_schema=...)` 显式给出支持的 schema。

是否必填取决于 Python 默认值。`x: str | None` 允许 null，但没有 `= None` 时仍是必填。默认值由真实函数应用，不会复制进公开工具 schema，以免意外暴露宿主默认凭据或其他敏感值。

必填的位置专用参数与 `*args` 不适合 JSON 关键字调用，自动注册会拒绝。可选位置专用参数只能使用它自己的默认值。`**kwargs` 需要显式对象 schema。工具重名、无效名称以及 schema 与签名不匹配会在接入时被拒绝。

同步和 `async def` 工具均可通过注册表使用。Agent 本身仍是同步接口；异步应用应将整次调用放入工作线程。同步工具须自行设置其网络或数据库超时；装饰器的 `timeout_seconds` 仅适用于 async 工具，不能抢占卡死的同步函数。
