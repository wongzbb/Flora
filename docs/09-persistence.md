# 事件存储、恢复与重放

真实工具调用和程序内部计算必须分开保存。事件日志回答“宿主实际提交了什么、得到了什么”；机器检查点回答“程序准备从哪里继续”。两者的存在不提供环境回滚能力。

## 内存与 SQLite

```python
from flora.trace import MemoryTrace, SQLiteTrace

trace = MemoryTrace()
assert trace.epoch == 0
assert trace.records == []

# 需要持久化时改用真实业务数据目录。
# trace = SQLiteTrace("run.sqlite3")
```

`MemoryTrace` 适用于离线示例、测试和不允许额外文件系统的评估协议。进程退出后数据不保留。

`SQLiteTrace(path)` 使用 SQLite WAL 和 FULL synchronous，保存追加式 journal 与最新检查点。登记效果时使用事务与历史锚点检查，防止两个连接在同一个锚点提交相互冲突的动作。它是本地持久化组件，不是分布式调度数据库。

## 事件的两阶段记录

1. `begin()` 先登记工具名、参数、当前锚点和事件 ID。
2. 宿主执行真正的工具调用。
3. `settle()` 追加实际 outcome。

`records` 是从 journal 派生的当前视图。journal 不覆写旧记录；settlement 与人工 resolution 也是新的追加条目。每个条目包含前一哈希和自身哈希，读取时检查连续性。

哈希链可发现记录缺损和非一致修改，但没有外部签名或可信锚点时，不是防恶意数据库管理员的证明。需要更强审计，应在现有基础设施中保留独立备份和访问控制。

## 状态与处理方法

| 状态 | 已知内容 | 操作建议 |
| --- | --- | --- |
| `pending` | 已登记请求，尚无已保存的结果 | 先核实实际外部结果，禁止自动重发 |
| `returned` | 有忠实保存的成功返回 | 可以恢复匹配续行 |
| `raised` | 有普通工具异常 | 按程序的错误分支处理 |
| `interrupted_unknown` | 可能已有外部效果，结果未知或不可保存 | 阻止新的效果，进行明确核实 |

未知效果不是普通工具失败。读请求也可能改变游标或消费消息，因此不能通过名称推断可以安全重试。

## 手工核实与 resolve

如果你通过原业务提供的查询、审计或管理界面确定某个请求的真实结果，可以将该结果连同来源说明记录为 resolution。该操作不会再次调用工具。

```python
from flora.trace import MemoryTrace

trace = MemoryTrace()
event_id = trace.begin(
    "write_item", {"id": "A"},
    expected_epoch=trace.epoch,
    expected_digest=trace.digest,
)
trace.settle(event_id, {
    "status": "interrupted_unknown",
    "error": {"type": "TimeoutError", "message": "response unavailable"},
})

# 只有外部核实已经完成时，才能填写真实确认结果。
trace.resolve(
    event_id,
    {"status": "returned", "value": {"id": "A", "committed": True}},
    reason="Example only: verified against the service audit record.",
)
assert trace.records[0]["status"] == "returned"
assert trace.records[0]["resolution_reason"]
```

以上是存储接口教程，示例本身没有写入任何业务系统。在真实运行中，不能用 `resolve()` 填一个期望结果来解除阻塞。reason 应说明核实依据；原任务协议不允许额外观察时，不得借人工 resolution 增加隐藏信息。

## 导出与导入

`trace.export()` 返回 `openharness-trace-v1` 对象，包含 journal 和检查点。这个名称是沿用旧版的数据格式标识，不是当前安装包或 import 名；不要对导出数据进行全局名称替换。`MemoryTrace.from_dict(data)` 校验格式与哈希链后恢复内存视图。导出数据可能包含完整任务观察和工具参数，应按业务数据级别保护。

```python
from flora.trace import MemoryTrace
from flora.values import canonical_json

original = MemoryTrace()
exported = original.export()
restored = MemoryTrace.from_dict(exported)
assert restored.digest == original.digest
print(canonical_json(exported))
```

## 历史重放究竟验证什么

重放解释器只计算纯程序。到达外部请求后，必须与当前历史位置的工具及参数完全匹配，才能读入对应的已记录结果。请求不同、记录不足、未决结果或不支持的值都会停止。重放绝不调用真实工具。

历史记录是过去那次事件的输入，不是当前世界的缓存。把昨天 `lookup(A)` 的结果交给今天的真实任务，会改变问题的可见信息，不能由历史重放接口自动完成。

历史请求参数含不透明对象句柄时，重放返回 UNKNOWN，原因是 `opaque_historical_request_is_not_comparable`；全部事件匹配后若最终返回仍含句柄，同样返回 UNKNOWN，原因是 `opaque_return_state_is_not_comparable`。回执包含句柄不会丢掉其周围已知 JSON 字段；只依赖这些已知字段的纯程序仍可重放。

当历史重放通过时，只能说明“这个程序在这一段记录的观察下兼容”，不能说明未发生的分支在真实世界中也会如此。

## 故障恢复原则

恢复时先验证 journal，再读取检查点和预算。若有 pending 或 interrupted_unknown，先解决外部结果；若检查点与日志位置不同，以已发生的真实事件为约束修复程序，绝不删除事件回到旧锚点。

进程死亡时尚未结算的模型请求可能已产生费用。恢复预算不会把未结算 reservation 自动退还。异步取消也不会证明远端模型或工具没有完成。

不透明句柄若仅在原进程存活，跨进程重放应返回未知或要求适配器重新建立真实对象。不要用 `repr()` 值、对象地址或假造内容让恢复“看起来通过”。

## 可运行的历史重放教程

```python
from flora.examples import run_demo, pagination_program
from flora.trace import MemoryTrace
from flora.replay import replay

report = run_demo("pagination")
trace = MemoryTrace.from_dict(report["trace"])
result = replay(pagination_program("data"), {}, trace.records, memory={})
assert result["status"] == "completed"
assert result["matched_records"] == 2
assert result["frontier"]["value"] == ["a", "b", "c"]
assert result["mode"] == "hypothetical"
```

重放没有接收 ToolRegistry，所以无法调用 `next_page()`。`memory` 必须是这段历史开始时可见的 memory，不能传运行结束后的新信息，否则会造成未来观察泄漏。

## 恢复一个已完成任务

```python
from flora.examples import CalendarWorld, calendar_bundle
from flora.runtime import Runtime
from flora.trace import MemoryTrace

world = CalendarWorld()
trace = MemoryTrace()
runtime = Runtime(world.registry(), trace=trace)
first = runtime.run("Move A after B.", bundle=calendar_bundle(trace.epoch, trace.digest))
count = len(world.calls)
restored = Runtime.restore(world.registry(), trace)
second = restored.run()
assert second.status == "completed"
assert second.value == first.value
assert len(world.calls) == count
```

这份示例保留同一个业务 world，只恢复 agent 的运行状态。SQLite 可以跨进程保存 agent 状态，但不会把内存演示世界变成持久化外部系统。真实业务适配器必须连接到正确的原环境。

## 明确的存储上限与完整记录淘汰

默认真实 journal 上限为 7 MiB，运行检查点上限为 7 MiB。`MemoryTrace` 和 `SQLiteTrace` 都接受关键字参数 `max_journal_bytes`、`max_checkpoint_bytes`；二者加 4096 字节元数据余量必须落在值层 16 MiB 总编码界限内。每个分区另有 90,000 JSON 节点、62 层深度上限，为完整 export 保留全局 200,000 节点 / 64 层余量。它们限制规范 UTF-8 JSON 字节与结构，不是宿主 Python 堆内存。

真实 journal 不会因接近上限而删除旧事件。效果执行前按照配置的最大工具返回大小进行保守入场检查；可能在仍有部分空余空间时停止，以保留结算或未知效果记录空间。调小 `max_tool_output_bytes` 必须符合真实工具返回规模，不能靠静默截断结果获得通过。

可再生的消费者上下文、合约样本和运行报告可以整条淘汰。默认上下文集合 1 MiB、合约集合 1 MiB、报告集合 256 KiB；不剪掉单条观察的一半再把它称为原输入，也不把失败或未知重标成通过。

检查点仍过大时，会先压缩可选证据集合，保留必要的当前程序、任务、memory、预算与真实轨迹。若必要状态本身无法保存，返回明确的资源耗尽结果，保留上一个有效检查点，不无限重试同一保存操作。

结果 reports 的 `resource_retention` 条目包含 `contexts_dropped`、`contexts_skipped`、`contract_records_dropped`、`reports_dropped`、`checkpoint_compactions`、`checkpoint_failures`。评测需要报告这些计数；证据被淘汰意味着未来复用可利用的覆盖面缩小，不能假装仍然验证过所有历史。

```python
from flora.trace import MemoryTrace
from flora.runtime import RuntimeConfig

trace = MemoryTrace(
    max_journal_bytes=6 * 1024 * 1024,
    max_checkpoint_bytes=7 * 1024 * 1024,
)
config = RuntimeConfig(
    max_context_bytes=1024 * 1024,
    max_contract_bytes=1024 * 1024,
    max_reports_bytes=256 * 1024,
)
assert trace.export()["limits"]["max_journal_bytes"] == 6 * 1024 * 1024
```

CLI 使用 trace 的默认存储上限；自定义 trace 上限通过 Python API 设置，不往仅有四个顶层对象的 CLI 配置中添加未支持的 storage 部分。
