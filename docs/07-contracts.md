# 消费者合约与规则综合

消费者合约把“这段程序可以怎样复用”变成可执行的局部关系。它不是由模型自行签发的保证，也不把工具成功返回直接视为任务成功。

## 三种已实现关系

| 关系 | PASS 的含义 | FAIL 的含义 |
| --- | --- | --- |
| `DEFINED_PREFIX` | 在这份实际上下文中，候选到达下一效果请求或纯返回 | 候选的纯前缀出现具体故障 |
| `SAME_BOUNDARY` | 新旧程序的下一请求或返回完全相同 | 候选纯前缀故障，或边界发生变化 |
| `SOURCE_FIDELITY` | 选定输出符合指定真实来源的等值、子串或成员关系 | 已取得来源和输出，但二者不满足关系 |

无法取得输入、没有可比较边界、参考程序不可用、来源路径不存在或只提供了假想上下文时，结果为 `UNKNOWN`。资源不足也不能变成通过证据。

## 最小兼容性检查

```python
from flora.contracts import ContextCheck, check_revision, Verdict

def reading_program(field):
    return {
        "version": 1, "entry": "main",
        "blocks": {
            "main": {
                "params": ["record"],
                "ops": [{"op": "get", "dest": "text",
                         "args": [{"var": "record"}, field]}],
                "term": {"op": "return", "value": {"var": "text"}},
            }
        },
    }

old_program = reading_program("title")
new_program = reading_program("label")
context = ContextCheck(
    id="same-record",
    inputs={"record": {"title": "alpha", "label": "alpha"}},
    reference_program=old_program,
    relation="SAME_BOUNDARY",
)
result = check_revision(new_program, context)
assert result.verdict is Verdict.PASS
print(result.to_dict())

changed = ContextCheck(
    id="different-record",
    inputs={"record": {"title": "alpha", "label": "beta"}},
    reference_program=old_program,
    relation="SAME_BOUNDARY",
)
assert check_revision(new_program, changed).verdict is Verdict.FAIL
```

第二个 FAIL 只表示不保持旧行为。也许新的 label 才符合任务，然而那需要真实任务证据，不能重新解释 `SAME_BOUNDARY` 的意思。

## 来源检查

`source_path` 从 `{"inputs": ..., "receipts": ..., "memory": ...}` 开始寻址。`output_path` 从候选的纯返回值，或其效果请求 `{"tool": ..., "args": ...}` 开始寻址。

```python
from flora.contracts import ContextCheck, check_revision, Verdict

program = {
    "version": 1, "entry": "main",
    "blocks": {"main": {
        "params": ["document"], "ops": [],
        "term": {"op": "return", "value": "quoted words"},
    }},
}
context = ContextCheck(
    id="quote-check",
    inputs={"document": "A source containing quoted words."},
    relation="SOURCE_FIDELITY",
    source_path=["inputs", "document"],
    output_path=[],
    source_mode="substring",
)
assert check_revision(program, context).verdict is Verdict.PASS
```

`substring` 只检查字面包含，不证明引用没有断章取义。`member` 只检查输出是指定来源列表中的一个相等元素。`equal` 检查规范 JSON 相等。

## 效果之后的消费者检查

对刚收到工具返回的消费者进行检查时，使用 `reference_machine` 保存完整的恢复后机器状态。该状态包含显式调用栈；不能把内部函数返回偷换成根程序返回。

如果候选程序不同于参考程序，就必须提供该候选对应的 `candidate_machine` 映射。缺少显式映射返回 `UNKNOWN`，不会擅自从程序入口重跑，以免遗漏调用栈或错误使用旧输入。

对整个“生产者＋表示＋消费者”改写进行入口级检查时，可以提供完整候选程序和相应入口输入。这样允许接口变化，但仍需明确如何构造新输入，不能让合约引擎猜测迁移。

## 有限适用规则综合

`fit_guard()` 从 `PASS / FAIL / UNKNOWN` 样本中生成有限析取范式规则。它只从已经出现的路径、类型、字段、标量常量和容器长度提取候选原子。不会向模型索要“真实标签”，也没有隐藏 oracle。

```python
from flora.contracts import fit_guard, evaluate_predicate

samples = [
    {"value": {"kind": "known", "items": [1]}, "verdict": "PASS"},
    {"value": {"kind": "known", "items": [2]}, "verdict": "PASS"},
    {"value": {"kind": "other", "items": []}, "verdict": "FAIL"},
    {"value": {"kind": "unclear"}, "verdict": "UNKNOWN"},
]
fitted = fit_guard(samples)
assert fitted["requires_runtime_check"] is True
assert fitted["scope"] == "empirical_routing_only"
for sample in samples:
    if sample["verdict"] != "PASS":
        assert evaluate_predicate(fitted["guard"], sample["value"]) is not True
print(fitted)
```

默认最多保留 24 个原子，每个合取最多 3 个原子，选择最多 4 个合取。确定性的贪心覆盖优先覆盖更多通过样本，接着偏好较短规则。未知样本不提供正证据；本实现也不会让拟合规则在已有未知样本上返回真。

如果没有可分离规则，结果为 `no_guard`；如果只覆盖部分通过样本，结果为 `partial`。矛盾样本不能靠丢弃失败标签来“修好”。仅有通过样本时，可能得到恒真规则，但依然标记 `requires_runtime_check`。

## 谓词 DSL

| 操作 | 示例 | 语义 |
| --- | --- | --- |
| `eq` / `ne` | `{"op":"eq","path":["kind"],"value":"known"}` | 规范 JSON 相等 / 不等 |
| `has` | `{"op":"has","path":["items"]}` | 该路径是否实际存在 |
| `type` | `{"op":"type","path":["items"],"value":"array"}` | 值类型检查 |
| `len_ge` / `len_le` | `{"op":"len_ge","path":["items"],"value":1}` | 字符串、列表或对象长度 |
| `lt` / `le` / `gt` / `ge` | `{"op":"gt","path":["count"],"value":0}` | 同类可比较值 |
| `all` / `any` | `{"op":"all","args":[...]}` | 三值合取 / 析取 |
| `not` | `{"op":"not","arg":...}` | 三值否定 |

路径使用对象字符串 key 或非负列表索引。空路径表示当前值本身。除了 `has`，路径缺失通常得到 `None`，表示未知。`number` 排除布尔值；`true` 与 `1` 不是相等的 JSON 值。合约规则允许空 `all` 表示真、空 `any` 表示假；模型诊断的谓词校验采用更严格的非空组合限制。

## 保存、重新检查与复用

`ContractStore.record(program, context)` 会自行重新运行检查。即使调用者传入一个 `CheckResult`，它也必须与重算结果一致，不能上传一个凭空构造的 PASS。

```python
from flora.contracts import ContractStore, ContextCheck

program = {"version": 1, "entry": "main", "blocks": {
    "main": {"params": ["x"], "ops": [],
             "term": {"op": "return", "value": {"var": "x"}}}
}}
store = ContractStore(max_records=64)
store.record(program, ContextCheck(id="actual-input", inputs={"x": 1}))
fitted = store.fit_guard(program, relation="DEFINED_PREFIX")
restored = ContractStore.from_dict(store.to_dict())
assert len(restored.records) == 1
```

store 按具体程序 digest 保存关系，不能把不同程序或不同关系的标签混合拟合。超出存储上限会移除最旧条目，并增加 `dropped_records`。恢复存储时会校验并重算记录；这会消耗内部计算时间，应纳入运行统计。

## 规则在默认运行时中的接入点

合约样本既能形成下一次模型编译的经验报告，也能约束真实的消费者程序复用。默认 Runtime 在修订通过后登记可复用变体，后续匹配消费者位置时运行 guard、纯迁移和当前局部检查，再决定是否切换。完整数据结构、路由语义与底层教程见[程序复用与条件路由](#reuse)。

## 必须保留的限制

调用者传入的 `ContextCheck(mode="observed")` 并不是密码学证书。生产路径必须从可信 trace 与运行时取得输入。底层 API 无法判断某个 Python 调用者有没有伪造整份上下文。

`PRESERVE / EXTEND / CHANGE` 是研究中的修改意图，不是本版本会自动推断的任务语义。兼容关系可以被机械检查，策略是否更优仍需要原任务的真实反馈。新的输入即使命中经验 guard，也必须进行可用的本地检查后才用于复用。

## 完整教程：修改状态表示与消费者

下面在一次真实工具返回之后暂停，把消费者需要保存的状态从整个 reply 信封改成单个 item。原程序和修改后的消费者都返回同一个 ID；迁移本身是受限 IR，不能调用工具。

```python
from flora.runtime import Runtime, RuntimeConfig
from flora.tools import ToolRegistry, ToolSpec

calls = []
def read_item():
    calls.append("read")
    return {"id": 7, "unused_text": "large optional metadata"}

original = {"version": 1, "entry": "main", "blocks": {
    "main": {"params": [], "ops": [], "term": {
        "op": "effect", "tool": "read_item", "args": {},
        "resume": "consume", "bind": "reply", "capture": {}}},
    "consume": {"params": ["reply"], "ops": [
        {"op": "get", "dest": "data", "args": [{"var": "reply"}, "value"]},
        {"op": "get", "dest": "item", "args": [{"var": "data"}, "id"]},
    ], "term": {"op": "return", "value": {"var": "item"}}},
}}
revised = {"version": 1, "entry": "main", "blocks": {
    "main": {"params": ["item"], "ops": [],
             "term": {"op": "return", "value": {"var": "item"}}}
}}
migration = {"version": 1, "entry": "main", "blocks": {
    "main": {"params": ["context"], "ops": [
        {"op": "get", "dest": "old", "args": [{"var": "context"}, "inputs"]},
        {"op": "get", "dest": "reply", "args": [{"var": "old"}, "reply"]},
        {"op": "get", "dest": "data", "args": [{"var": "reply"}, "value"]},
        {"op": "get", "dest": "item", "args": [{"var": "data"}, "id"]},
    ], "term": {"op": "return", "value": {"item": {"var": "item"}}}}
}}

runtime = Runtime(
    ToolRegistry([ToolSpec("read_item", read_item)]),
    config=RuntimeConfig(max_steps=1),
)
bundle = {"programs": [{"id": "reader", "program": original, "inputs": {}}],
          "incumbent": "reader", "diagnostics": [],
          "expected_epoch": runtime.trace.epoch,
          "expected_digest": runtime.trace.digest}
assert runtime.run("Read the item ID.", bundle=bundle).status == "incomplete"
assert calls == ["read"]

revision = runtime.apply_revision(
    "reader", revised, migration=migration,
    mode="PRESERVE", revision_id="compact_reader",
)
assert revision["accepted"] is True
assert revision["task_improvement"] == "UNMEASURED"
assert calls == ["read"]  # 迁移与历史检查没有再次调用工具。
assert runtime.candidates["reader"].machine.registers == {"item": 7}
assert len(runtime.reuse.variants) == 1
```

本教程用单步上限制造一个可检查的暂停点；它没有删除原始事件日志，也不声称已经降低整个系统的持久化体积。被压缩的是新消费者的程序状态。完整历史保留与否需要单独的数据保留策略，不得为了展示压缩而删掉用于审计的真实事件。

`PRESERVE` 要求保留上下文全部通过 SAME_BOUNDARY，且不能只挑最近一个成功案例。`EXTEND` 对旧程序可以正常运行的区域检查 SAME_BOUNDARY；对旧程序已有具体纯故障的区域改查 DEFINED_PREFIX，使修改能够修复失败输入。它没有证明所有未知新输入都正确。`CHANGE` 允许显式行为变化，但留下失败兼容见证，并把任务收益标为 `UNMEASURED`。三种方式都重新迁移当前真实状态并执行当前局部检查；历史样本通过不能替代当前检查。

模型可在 bundle 的可选 `revisions` 数组提出同样的修改。要激活该修改，正常候选中的目标 ID 和程序必须与修订一致，输入来自经过检查的迁移；不能通过 bundle 里另填一份输入绕过保留关系。如果没有对应的新正常候选，则只记录修订提案。
