# 接入工具与业务系统

工具是模型生成程序与宿主系统之间唯一受控的效果入口。IR 不能自行注册工具、导入模块或调用 Python 对象方法。普通使用时把函数交给 Agent 即可自动注册；本章介绍需要精确 schema、异步超时或不透明对象时的底层 `ToolSpec`。模型只看到名称、说明与参数 schema。

## 注册一个同步工具

```python
from flora.tools import ToolRegistry, ToolSpec

catalog = {"A": {"title": "alpha"}, "B": {"title": "beta"}}

def lookup(item_id: str):
    if item_id not in catalog:
        raise KeyError(item_id)
    return dict(catalog[item_id])

tools = ToolRegistry([
    ToolSpec(
        name="lookup",
        handler=lookup,
        description="Read an item by its exact catalog identifier.",
        input_schema={
            "type": "object",
            "properties": {"item_id": {"type": "string", "minLength": 1}},
            "required": ["item_id"],
            "additionalProperties": False,
        },
    )
])
assert tools.names == ("lookup",)
assert tools.call({"tool": "lookup", "args": {"item_id": "B"}}) == {"title": "beta"}
```

`ToolRegistry.call()` 是低层宿主接口，上面的调用用于直接验证适配器。完整任务必须经 `EffectExecutor` 或 `Runtime` 执行，才能获得预算计数、事件日志、历史锚点与中断处理。

## ToolSpec 参数

| 参数 | 默认值 | 作用 |
| --- | --- | --- |
| `name` | 必填 | 工具唯一名称，最长 128 字符，首字符为字母或下划线 |
| `handler` | 必填 | 可调用的宿主函数 |
| `description` | 空字符串 | 提供给编译模型的工具说明 |
| `input_schema` | `None` | 已支持子集内的参数约束 |
| `timeout_seconds` | `None` | 仅用于 async 工具的运行时等待上限 |
| `opaque_parameters` | 空 tuple | 允许接收已认证原对象的顶层参数名 |

重名工具注册会失败。参数检查包括 JSON 可表示性、schema、宿主函数签名；未知工具不能通过模型 bundle 的校验，也不能在执行器中被调用。

## 支持的 schema 子集

支持 `type`、`properties`、`required`、`additionalProperties`、`items`、`enum`、`minimum`、`maximum`、`minLength`、`maxLength`、`minItems`、`maxItems`，以及仅作说明的 `description`、`title`、`default`。

这不是完整 JSON Schema 引擎。未支持的约束关键字会拒绝执行，而不是静默忽略。`default` 是文档元数据，不会自动补充缺失参数。要填写默认值，应在处理函数或显式程序里完成。

`type` 可以是单个类型名，或允许类型的数组。布尔值不会被接受为 integer 或 number。对象建议明确设置 `additionalProperties: false`，使拼写错误成为早期可见的校验失败。

## 异步工具与超时

```python
import asyncio
from flora.tools import ToolRegistry, ToolSpec

async def bounded_lookup(item_id: str):
    await asyncio.sleep(0)
    return {"id": item_id, "found": True}

tools = ToolRegistry([
    ToolSpec("lookup", bounded_lookup, timeout_seconds=2.0)
])
assert tools.call({"tool": "lookup", "args": {"item_id": "A"}})["found"]
```

同步运行时可以调用 async 工具；注册为同步函数却返回 awaitable 会被拒绝。如果应用已经运行在 asyncio loop 中，把整个同步运行放入 `asyncio.to_thread(...)`，不要在同一个 event loop 中嵌套 `asyncio.run()`。

同步处理函数必须自行设置 HTTP、数据库或其他 I/O 的 timeout。给同步函数设置 `timeout_seconds` 会失败。解释器的 fuel 和预算时间检查无法抢占卡死的同步宿主调用。

工具 timeout 不等于远端动作被撤销。例如提交写请求后等待超时，服务端仍可能完成写入；此时效果属于 `interrupted_unknown`，后续动作被阻止，不能自动再次提交。

## 返回值与错误

优先返回严格 JSON：字符串、有限数、布尔值、null、列表、字符串键字典。不要用对象的字符串描述替代真实值，以免把显示摘要误当成可重放数据。

普通异常被记录为 `raised`，包含类型名和有界消息；IR 的续行可以检查状态。`TimeoutError`、中断和无法忠实保存的结果需要使用未知效果处理。错误消息来自可信适配器，但可能含业务数据；日志访问控制和脱敏属于宿主部署职责。

不可持久化的宿主对象应通过明确的 JSON 投影适配，或使用本版本支持的不透明句柄路径。句柄不是可检查的语义值，也不保证重启后仍有效；持久化与重放限制见对应章节。

## 设计工具接口的实践

将授权、访问控制、参数范围和审计放在处理函数或原有业务网关中。工具说明帮助模型理解，不构成权限检查。让模型传入 `authorized=true` 不能代替鉴权。

读写语义应在名称与说明中明确，但运行时不会因工具名包含 `read` 就认为它可随意重复。分页、读取消息队列和游标查询都可能改变状态。

如果外部 API 原本支持幂等键，可以在适配器中使用，并将真实键记录到事件参数或可审计元数据。Flora 不凭空给外部 API 增加 exactly-once 能力。

不要让模型生成的任意字符串成为 shell 命令或 SQL。若业务确实提供这类能力，应通过原业务的最小权限、参数化执行和隔离策略控制，并纳入任务原有权限边界。

## 非 JSON 对象：进程本地不透明句柄

有些既有工具返回数据库游标、SDK 对象或其他宿主对象。为了不要求工具额外提供一个虚构的可序列化世界，Flora 支持进程本地 `OpaqueStore`。

普通 JSON 仍按值复制。不可 JSON 表示的子对象会由真实效果执行路径编码成句柄；句柄只引用原对象，不复制对象、不调用 `repr()`、不枚举属性、不 pickle，也不是对象状态快照。

接收原对象的工具必须由宿主显式声明 `opaque_parameters`：

```python
from flora.opaque import OpaqueStore, is_opaque
from flora.tools import ToolRegistry, ToolSpec

class LocalCursor:
    def __init__(self):
        self.position = 0

cursor = LocalCursor()
store = OpaqueStore(max_handles=16)

def consume(handle):
    handle.position += 1
    return {"position": handle.position}

tools = ToolRegistry([
    ToolSpec("consume", consume, opaque_parameters=("handle",))
], opaque_store=store)

carrier = store.encode_result(cursor)
assert is_opaque(carrier)
assert tools.call({"tool": "consume", "args": {"handle": carrier}}) == {"position": 1}
assert cursor.position == 1
assert store.stats()["persistence"] == "process_local"
```

这是低层适配器教程。实际任务仍应通过 Runtime，让对象来源、真实工具事件和预算在同一链路中处理。工具描述会公开哪些参数接受已取得的句柄；模型不能自行增加这些授权位置。

只有显式授权的顶层完整 carrier 才能还原为原对象。未授权参数、嵌套 carrier、未知 token、不同 store 的 carrier、过期或被撤销的 carrier 都拒绝。普通 JSON 在同一个参数位置仍受原 schema 约束。

如果原工具返回一个恰好含保留 key `__openharness_opaque__` 的普通字典，编码器会把这个原字典整体当作不透明数据，避免业务内容伪造句柄。token 由 store 生成；手工构造相似 JSON 不能获得对象权限。

### 生命周期与资源

`OpaqueStore(max_handles=1024, ttl_seconds=None)` 默认最多保存 1024 个强引用。TTL 从首次注册开始计算，访问不会自动续期。`revoke(carrier)` 撤销一个引用，`clear()` 清除全部引用，`stats()` 只返回数量和配置。

句柄数限制不等于对象占用内存上限。一个数据库连接或大对象可能持有大量宿主资源，适配器仍需负责关闭、释放与账户配额。

同 token 只表示对象身份；它可能已被上一次工具调用修改。因此，不能把 token 相等推导为状态相等。合约谓词对句柄内容、元数据和涉及它的相等检查返回 UNKNOWN；SAME_BOUNDARY 与 SOURCE_FIDELITY 比较结果涉及句柄时也返回 UNKNOWN。普通 JSON 外壳中与句柄无关的已知字段仍可检查。

### 重启之后

SQLite 保存的是 carrier 引用，不保存宿主对象。进程或 store 丢失后，不能从日志重建原对象。需要通过原任务实际开放的工具重新取得对象，并产生新的真实事件，或者停止运行；本包不会自动重获、自动重试或偷偷恢复对象状态。
