# 程序复用与条件路由

可复用组件不是一段脱离上下文的好代码。它必须说明：替换的是哪个消费者位置、旧状态如何迁移、什么条件下值得尝试，以及当前真实输入上的局部关系是否通过。

`ReuseLibrary` 实现这一流程。经验 guard 命中只是启动检查；迁移和当前消费者检查通过后，才会返回新的机器状态。整个过程不调用外部工具。

## 一条复用项包含什么

| 字段 | 含义 |
| --- | --- |
| `source_program` | 被替换的旧程序 |
| `scope` | 旧程序 digest、块、指令位置、调用栈续行结构 |
| `program` | 新的消费者程序 |
| `migration` | 从旧实际状态生成新入口输入的纯 IR |
| `guard` | 对旧机器 registers 求值的三值经验规则 |
| `mode` | PRESERVE 或 EXTEND |
| `id` | 可审计的变体标识 |

scope 不把动态捕获值当作固定常量，但包含调用栈的结构；当前完整调用栈仍参与真正的兼容检查。同名块或同一条文本 prompt 不足以证明上下文相同。

`CHANGE` 不能注册为自动保持行为的复用项。有意改变策略需要显式探索与原任务反馈，不能借经验规则把变化伪装成兼容替换。

## 路由顺序

1. 找到与当前消费者控制位置和续行结构匹配的复用项。
2. 按确定的登记顺序检查 guard；false 或 unknown 均不切换。
3. 以实际 `{inputs, receipts, memory}` 运行纯迁移。
4. 迁移必须返回准确匹配新程序入口参数的对象。
5. PRESERVE 对当前新旧消费者运行 SAME_BOUNDARY。
6. EXTEND 只有在旧前缀明确 FAIL 时才转为检查新程序的 DEFINED_PREFIX，其余仍检查兼容。
7. 只有当前结果 PASS 才接受。没有实际状态变化的空替换被跳过。

调用者得到的是一个尚未执行外部动作的新 Machine。真实工具调用仍必须经 Runtime 的历史锚点、效果登记与共享预算。

## 可运行的底层教程

下面把旧寄存器 `x` 改成 `{payload: {value: ...}}`。两种消费者在当前值上返回同样结果；规则来自一个局部定义性检查的真实输入样本。

```python
from flora.contracts import ContextCheck, check_revision, fit_guard
from flora.reuse import ReuseLibrary
from flora.vm import new_machine, run_until_boundary

old = {"version": 1, "entry": "main", "blocks": {
    "main": {"params": ["x"], "ops": [],
             "term": {"op": "return", "value": {"var": "x"}}}
}}
new = {"version": 1, "entry": "main", "blocks": {
    "main": {"params": ["payload"], "ops": [
        {"op": "get", "dest": "answer", "args": [{"var": "payload"}, "value"]}
    ], "term": {"op": "return", "value": {"var": "answer"}}}
}}
migration = {"version": 1, "entry": "main", "blocks": {
    "main": {"params": ["context"], "ops": [
        {"op": "get", "dest": "inputs", "args": [{"var": "context"}, "inputs"]},
        {"op": "get", "dest": "x", "args": [{"var": "inputs"}, "x"]},
    ], "term": {"op": "return", "value": {"payload": {"value": {"var": "x"}}}}}
}}
observed = {"x": 7}
check = check_revision(old, ContextCheck(id="local-input", inputs=observed))
guard = fit_guard([{"value": observed, "verdict": check.verdict.value}])["guard"]
source = new_machine(old, observed)
library = ReuseLibrary(max_variants=8)
library.register(source, new, migration, guard, variant_id="packed_value")
decision = library.route(source, receipts=[], memory={})
assert decision.accepted
assert run_until_boundary(decision.machine).value == 7
assert decision.report["task_correctness"] == "UNVERIFIED"

# 同一库可以交给 Runtime，在正常候选纯执行之前自动路由。
from flora.runtime import Runtime
from flora.tools import ToolRegistry
runtime = Runtime(ToolRegistry())
runtime.reuse = library
bundle = {"programs": [{"id": "main", "program": old, "inputs": observed}],
          "incumbent": "main", "diagnostics": [],
          "expected_epoch": runtime.trace.epoch, "expected_digest": runtime.trace.digest}
result = runtime.run("Return the observed local value.", bundle=bundle)
assert result.value == 7
assert any(r["kind"] == "reuse_checked" and r["accepted"] for r in result.reports)
```

`register()` 是低层接口，只校验复用项的结构，不自行宣布历史上已经正确。默认 Runtime 会在修订通过历史和当前检查后登记；直接使用 library 的宿主应承担同样的历史证据责任。无论怎样登记或恢复，route 都不能跳过当前局部检查。

## 默认 Runtime 的自动接入

`Runtime` 自带 `reuse` 库。`apply_revision()` 接受 PRESERVE / EXTEND 后，使用同一消费者控制点的保留样本和当前已通过检查的输入拟合 guard，登记程序与状态的联合变体。CHANGE 不登记为自动兼容复用。

之后，每个正常候选进入纯执行前，运行时尝试 scope 路由；存在匹配项时记录 `reuse_checked` 报告。guard 为真仍然需要当前迁移和消费者关系 PASS，才替换机器并继续。`enable_contracts=False` 同时关闭这条自动路由路径。

旧候选若已经落后于当前真实历史，不能用 PRESERVE 或 EXTEND 把过去状态直接换成现在；报告为 `stale_source_requires_explicit_change`。此时要显式重新编译或提出 CHANGE，并基于当前真实观察检查新的剩余程序。

自动路由只检查当前输入到下一个效果或纯返回的局部关系，不声称后续整段工作流等价、所有新输入都正确或任务成绩提高。复用库保存进检查点；检查点需要压缩时，可通过 `drop_oldest()` 整条移除可选变体。

## 返回报告

`ReuseDecision` 有 `accepted`、`machine`、`report`，支持 `to_dict()`。拒绝时 machine 为 None；report 保留各次尝试的 guard 结果、迁移边界、局部 verdict、选中项和原因。

常见原因包括 `no_matching_scope`、`guard_false`、`guard_unknown`、`migration_did_not_return_input_object`、`migration_inputs_invalid`、`current_relation_not_passed` 和 `no_state_change`。这些原因用于区分“不适用”“数据不可得”和“出现反例”，不能混成一个任务失败标签。

## 持久化与容量

`ReuseLibrary(max_variants=64)` 最多接受 64 个变体。达到数量上限时淘汰最旧项，并累计 `dropped_variants`；存储另有 1 MiB 编码上限，接近上限时整条淘汰最旧项；单项本身无法装入时拒绝新登记。任务检查点本身还有更严格的整体大小限制。

`to_dict()` 和 `from_dict()` 保存、校验复用库。恢复会检查程序、迁移、scope、谓词、ID 冲突和容量；已保存的 guard 不能作为当前输入 PASS 的缓存。

普通字段中的新值可以在同一个 scope 中再次检查。涉及不透明对象的状态关系、不匹配调用栈或没有对应观察的续行，仍必须保持未知或拒绝复用。
