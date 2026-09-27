# 两个离线工作流

本章所有 Python 示例都可以在已安装包的环境中直接运行，不请求语言模型。它们用人工编写的 IR 作为编译结果，专门验证执行语义。把确定性程序换成 LLMCompiler 后，使用的是同一个运行时。

## 教程一：日程诊断

任务是把事件 A 调整到事件 B 当前开始时间之后 30 分钟。旧观察支持 B=14:00，后来的消息支持 B=15:00。正常候选分别准备把 A 调到 14:30 或 15:30；诊断程序先读 B，再按实际返回计算 A。

```python
from flora.examples import run_demo

report = run_demo("calendar")
assert report["result"]["status"] == "completed"
assert report["host_observation"]["events"]["A"] == 930
assert [c["tool"] for c in report["host_observation"]["calls"]] == [
    "read_event", "move_event"
]
assert report["result"]["budget"]["tool_calls"] == 2
assert report["result"]["budget"]["model_calls"] == 0
print(report["result"]["value"])
```

分钟数从午夜开始计算，930 即 15:30。`host_observation` 是演示结束后的测试环境观测，用于读者检查实际结果；它没有作为隐藏输入提供给候选程序。

### 执行顺序

1. 两个正常候选到达不同的 `move_event` 边界，没有执行修改。
2. 诊断拥有互斥预测 840 / 900，两个假想输入会导致不同的修改请求，因此得到正分数。
3. 调度器实际提交 `read_event(B)`，世界返回 900。
4. 预测被本次观察检验；旧正常续行因实际路径不同而被冻结。
5. 诊断自己的续行收到 900，计算 930，再真实提交 `move_event(A, 930)`。
6. 工具返回后程序完成，保存真实日志与消费者检查。

旧候选被冻结并不是读取动作证明整段旧程序错误。两种原因在 reports 中分开记录：`forecast_observation` 描述预测，`execution_commitment` 描述路径承诺。

### 如果真实返回在全部预测之外

```python
from flora.examples import CalendarWorld, calendar_bundle
from flora.runtime import Runtime

world = CalendarWorld(events={"A": 960, "B": 960})
runtime = Runtime(world.registry())
result = runtime.run(
    "Move A to 30 minutes after B.",
    bundle=calendar_bundle(runtime.trace.epoch, runtime.trace.digest),
)
assert result.status == "completed"
assert world.events["A"] == 990
assert len(world.calls) == 2
```

实际 B 为 16:00，候选的两个预测都没有覆盖它。续行仍然读取真实值，计算出 16:30。系统不会把 960 强行投到最接近的 900，也不会把假想值写进真实历史。

### 关闭诊断观察消融

```python
from flora.examples import run_demo
from flora.runtime import RuntimeConfig

report = run_demo("calendar", config=RuntimeConfig(enable_diagnostics=False))
assert report["result"]["status"] == "completed"
assert report["host_observation"]["events"]["A"] == 870
assert report["result"]["budget"]["tool_calls"] == 1
```

这个受控例子中，默认候选采用旧时间，直接修改成 14:30。它展示诊断机制的作用，不是一般任务性能实验。真实模型可能提出更好或更差的默认候选，需要独立评测。

## 教程二：有状态分页与消费者反例

`next_page()` 每次调用都会推进游标，没有参数，也不能回退。两个候选都先请求第一页，但分别假设字段名为 `items` 和 `data`。实际返回使用 `data`。

```python
from flora.examples import run_demo

report = run_demo("pagination")
assert report["result"]["status"] == "completed"
assert report["result"]["value"] == ["a", "b", "c"]
assert report["host_observation"]["cursor"] == 2
assert report["result"]["budget"]["tool_calls"] == 2
verdicts = [r["result"]["verdict"] for r in report["contracts"]["records"]]
assert "FAIL" in verdicts
assert "PASS" in verdicts
print(report["result"]["value"])
```

第一次相同请求被共享，真实游标只推进一次。得到同一份真实返回后，`items_parser` 在消费者纯计算中出现缺失字段故障，`data_parser` 正常计算，并准备下一次分页调用。

第二次参数仍为空对象，但 epoch 已变。运行时真实执行第二次调用，游标到达 2，最终聚合 a、b、c。它不能使用第一页缓存来“节省调用”。

### 查看真实日志

```python
from flora.examples import run_demo
from flora.trace import MemoryTrace

report = run_demo("pagination")
trace = MemoryTrace.from_dict(report["trace"])
assert trace.epoch == 2
assert [r["tool"] for r in trace.records] == ["next_page", "next_page"]
assert trace.records[0]["value"] != trace.records[1]["value"]
assert trace.records[0]["args"] == trace.records[1]["args"] == {}
```

日志证明两个调用的参数相同，但返回不同。消费者反例证明某个字段假设在这份实际输入上失败，不证明所有名为 items 的接口都错误。

## 在 Python 应用中组合运行时

```python
from flora.budget import Budget, BudgetLimits
from flora.examples import PaginationWorld, pagination_bundle
from flora.runtime import Runtime, RuntimeConfig
from flora.trace import MemoryTrace

world = PaginationWorld()
trace = MemoryTrace()
runtime = Runtime(
    world.registry(),
    trace=trace,
    budget=Budget(BudgetLimits(max_tool_calls=10, max_model_calls=0)),
    config=RuntimeConfig(max_steps=20),
)
result = runtime.run(
    "Read all record IDs.",
    bundle=pagination_bundle(trace.epoch, trace.digest),
)
assert result.value == ["a", "b", "c"]
assert len(runtime.contracts.records) > 0
```

自定义环境只需替换 registry；真实模型模式再提供 compiler。普通工具、预算和事件记录路径不会因编译器类型改变。
