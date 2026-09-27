# 公平实验与消融

Flora 改变的是内部程序执行与修订机制，不是任务能观察到什么。研究对比必须先固定信息与预算边界，再讨论成功率。

## 与基线保持一致的条件

| 条件 | 要求 |
| --- | --- |
| 模型 | 同一固定模型版本与可比的解码参数 |
| 工具 | 同一工具名称、签名、文档和权限 |
| 任务 | 相同任务实例、顺序与初始环境 |
| 反馈 | 只使用该任务原本返回给 agent 的反馈 |
| 预算 | 统一记入编译、修复、候选生成、诊断和普通行动 |
| 重置 | 只使用原协议允许的重置，不为新方法增加分支世界 |
| 外部存储 | 原协议不开放文件系统时，使用进程内存状态；不添加额外信息源 |

新解释器属于一种新的方法条件。不要把它标成“只改 prompt 的 JAZ”，也不要用它的内部日志获得基线看不到的隐藏评估标签。

## AppWorld 与 StuLife 的反馈区别

AppWorld 的提交会推进任务生命周期。提交后得到的原有反馈可以帮助后续任务，但不能被当作提交前 oracle，也不能假定能够回到同一个任务重复试错。

StuLife 中存在的内部评分，不等于 agent 能读取该评分。如果适配器原本只返回进度，那么 Flora 也只能使用进度。完成保护与工具返回形状检查不等于正确性反馈。

这两个名称出现在研究对齐讨论中，不代表发布包自带官方评测数据、完整环境或已执行的成绩。本实现没有通过离线演示冒充真实 benchmark 测试。

## 推荐的消融矩阵

1. 原基线：原有运行机制，相同模型、工具与预算。
2. 单候选：使用新 IR 和效果边界，只保留一个候选，不做主动诊断与经验合约复用。
3. 关闭主动诊断：保留候选及合约，但按默认程序执行。
4. 关闭条件化复用：保留诊断，不依据历史适用规则复用改写。
5. 完整方法：全部机制开启，预算与其他条件一致。

每个实验应保存任务清单、模型标识、采样参数、配置、程序编译次数、工具次数、token 数、内部计算耗时及终局指标。不得只报告成功率，不报告多候选带来的额外开销。

## 主指标与机制指标

**主指标**是原环境定义的任务成功率、每任务成本、延迟和完成率。任务正确性必须由已有评估协议给出。

**机制指标**可以包括有效诊断次数、实际预测反驳率、合约规则命中后的本地失败率、冻结与反驳分别计数、程序重新编译次数、历史重放在何处停止，以及记忆与消费者联合修改的兼容率。

机制指标不能代替主指标。更高的合约通过率可能来自保留旧错误；更少的候选可能来自行动承诺；更高的诊断分数也不证明信息真正改善了任务决策。

## 怎样避免选择偏差

把阈值、最大候选数、诊断额度和规则复杂度在开发集确定后锁定。测试时不要为某个失败任务手工添加专用规则再计算整体成绩。所有模型调用都保留 request id 或等效审计标识，公开失败与预算超限的处理方式。

对随机模型至少使用多个独立运行并报告不确定性。已有候选的顺序是算法的一部分，不能在失败后偷偷交换默认候选。若模型或工具版本改变，应将结果分成不同实验条件。

## 没有 trainer 的含义

0.1.0 不执行梯度下降、强化学习或模型微调。在线变化的是 IR 程序、候选状态、消费者检查样本和有限谓词规则。未来可以研究如何学习调度参数，但这不属于当前交付实现，也不是运行本版本的前提。

## 通过公开 AgentEnv 接口接入原评估环境

发布包提供 `AgentEnvBridge`，用于把已经由宿主建立的 agent-facing 环境接到独立运行时。桥接器使用公开工具、公开说明与完成保护；它不导入 JAZ runtime，也不调用环境 setup、reset、grade 或私有评分接口。

接入流程是：原评估宿主按原协议准备环境，给方法一个 AgentEnv；方法只把该对象交给 bridge。任务开始、提交与推进仍通过原本开放的工具完成。

```python-template
from flora.adapters import AgentEnvBridge
from flora.runtime import Runtime

# agent_env：原评估宿主已准备好的公开 agent-facing 对象。
# compiler：已按模型章节配置的固定模型编译器。
bridge = AgentEnvBridge(agent_env, root=True)
runtime = Runtime(
    bridge.registry,
    compiler=compiler,
    completion_guard=bridge.completion_guard,
)
result = runtime.run(bridge.instructions)
```

CLI 也接受工厂直接返回这个 bridge：`--adapter my_env:create_bridge`。未指定 `--task` 或 `--task-file` 时使用 bridge.instructions；显式任务优先。根会话 completion guard 随 bridge 进入运行时与恢复路径，不能通过 CLI 省略。

这是需要外部评估环境的接入模板，不是交付时运行过的官方任务。AppWorld、StuLife 及其数据、安装依赖和许可不包含在核心包中。

### 权限与任务交付模式

根会话 `root=True` 使用原接口允许的根工具与共享工具。`root=False` 只绑定共享工具，且不使用全局队列的 completion guard，避免子任务返回被整个队列状态阻塞。

当前桥接支持 pull 任务交付：原工具主动拉取下一任务。如果 `delivered_task_tool()` 指示必须由外部驱动推送任务，bridge 会在建立时拒绝，不能把 push 协议假装成 pull。要支持新的交付协议，需要显式驱动实现与相应公平性测试。

`completion_guard` 只约束“是否允许结束本次工作”，不是任务正确性 oracle。返回 false 时，Runtime 会记录提前结束并要求有预算的程序修订；不会把它当作正确答案标签。

### AppWorld 的工具树

普通 callable 保留原工具名称、签名与说明。AppWorld 的 `apis` 是动态公开工具树，IR 不能使用任意 Python 属性语法，因此桥接成同名工具的明确请求：

```json
{
  "tool": "apis",
  "args": {
    "path": ["app_name", "public_endpoint"],
    "kwargs": {"argument_name": "argument_value"}
  }
}
```

路径只能使用公开、非下划线开头的标识符，实际权限仍由原工具树决定。bridge 不静态增补一份隐藏 endpoint 名单，也不暴露原树禁止访问的提交或评分接口。

这一变换改变了工具调用的表达形式。模型能否同样理解动态树文档，需要实际评估；不能宣称与 JAZ 的 Python 调用语法毫无差异。比较中应把这个表示差异作为方法条件记录下来。

### 恢复时的环境责任

保存 agent SQLite trace 不会保存 AppWorld 或 StuLife 的整个外部状态。恢复时必须重新连接到同一有效的环境实例或原协议允许恢复的状态。根会话检查点如果要求 completion guard，恢复时也必须重新提供真实的 guard，不能省略完成约束。

## 可运行的公开协议桥接教程

下面用一个最小本地环境实现同一公开协议，展示任务完成保护如何与实际工具链配合。它没有评分接口，也不是 AppWorld / StuLife 的替代评测。

```python
from flora.adapters import AgentEnvBridge
from flora.runtime import Runtime

class PublicEnvironment:
    def __init__(self):
        self.done = False

    def delivered_task_tool(self):
        return None

    def get_instructions(self):
        return "Read the record, submit once, then return its ID."

    def is_complete(self):
        return self.done

    @property
    def tools(self):
        return [
            {"name": "read_record", "signature": "()", "description": "Read the current record."},
            {"name": "complete_task", "signature": "()", "description": "Submit this task once."},
        ]

    def read_record(self):
        return {"id": 7}

    def complete_task(self):
        self.done = True
        return {"submitted": True}

    def shared_tool_bindings(self):
        return {"read_record": self.read_record}

    def root_tool_bindings(self):
        return {"complete_task": self.complete_task}

program = {"version": 1, "entry": "main", "blocks": {
    "main": {"params": [], "ops": [], "term": {
        "op": "effect", "tool": "read_record", "args": {},
        "resume": "submit", "bind": "read_reply", "capture": {}}},
    "submit": {"params": ["read_reply"], "ops": [], "term": {
        "op": "effect", "tool": "complete_task", "args": {},
        "resume": "done", "bind": "submit_reply",
        "capture": {"read_reply": {"var": "read_reply"}}}},
    "done": {"params": ["read_reply", "submit_reply"], "ops": [
        {"op": "get", "dest": "record", "args": [{"var": "read_reply"}, "value"]},
        {"op": "get", "dest": "id", "args": [{"var": "record"}, "id"]},
    ], "term": {"op": "return", "value": {"var": "id"}}},
}}
env = PublicEnvironment()
bridge = AgentEnvBridge(env, root=True)
runtime = Runtime(bridge.registry, completion_guard=bridge.completion_guard)
bundle = {"programs": [{"id": "main", "program": program, "inputs": {}}],
          "incumbent": "main", "diagnostics": [],
          "expected_epoch": runtime.trace.epoch, "expected_digest": runtime.trace.digest}
result = runtime.run(bridge.instructions, bundle=bundle)
assert result.status == "completed"
assert result.value == 7
assert runtime.trace.epoch == 2
assert env.done
```

带有必需 positional-only 参数的 callable 不能直接通过本桥接的 keyword JSON 传输，建立 bridge 时会拒绝。动态树只允许 1–32 个公开标识符，且终点必须可调用。动态树传输主要面向同步 keyword endpoint，不承诺任意对象方法和 async 叶节点都兼容。桥接器不会执行类型注解来猜出新工具约束。这些都是明确的传输限制，不能靠假设所有原工具都可适配而忽略。


## 直接使用接口不改变实验能力

0.1.0 的 Agent 与普通函数注册只是接入层。与 JAZ 对齐实验时，使用原环境公开工具、任务和完成协议，不启用额外 workspace 或命令工具。高层 API 不默认开放文件系统；模型重新规划照常计入共享模型预算。进程本地 HTTP fixture 用于验证传输与执行链，不能作为真实模型基准成绩。
