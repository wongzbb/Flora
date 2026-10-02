# Engineering Round 19

日期：2026-10-03（Asia/Shanghai）

## 本轮问题

上一轮的 GLM 真实任务在跨 block 修复时使用了错误的 continuation 参数名。`block-list-v3` 已支持 outcome target 只接收部分 capture，但仍要求 target 参数名必须等于 effect 的 `bind`。这个要求是接口命名约束，不是值或控制语义约束，降低了跨模型组合性。

同一复测还显示，模型把已经抽取的 `agent_ids` 再包成嵌套身份信封，旧 schema 在工具边界拒绝了它。

## 改动

### Outcome 参数的通用 alpha-renaming

在 projected observe（`block-list-v3`）中，每个 success/error target 现在必须有且只有一个不属于 capture 的结果参数，名称可以是局部名称。前端在 lowering 时把该唯一参数映射到真实 raw value/error；其余参数仍只能来自明确声明的 capture。多余、缺少结果参数或重复参数仍拒绝。`observe-v1`、普通 effect/call/branch 和 IR 规则保持严格不变。

这只做确定性的接口 alpha-renaming，不猜测值、分支或结果，不改变双重控制：effect 仍先执行并产生观测，之后的 outcome target 才消费它。

### 有界身份信封归一化

协作工具接受最多四层的 `agent_id` 包装，并只读取 `agent_id` 字段，最终必须命中当前 delegation 记录。schema、`read_agent`、`wait_agents`、`read_agents`、`review_agent`、`resume_agent` 的边界保持一致；任务、结果和其他字段不会被信任或合并。

## 离线验证

- 协作、projected observe、前端和通用回归：69 passed。
- `ruff check src tests`：通过。
- `compileall src tests`：通过。
- `git diff --check`：通过。

## 真实 API 对照

API：用户授权网关 `http://35.220.164.252:3888/v1`。

### DeepSeek `deepseek-v4-flash`：通过

同一两子 agent 任务（`17+25`、`9*8`，收集、逐个复核、求和）在当前代码完成，返回 `42`、`72`、`114`。本次约 157 秒，9 次模型调用、9 次工具调用，完成检查为 ready。模型生成了 replan，并基于实际 child 结果继续后续阶段；没有把 spawn 自称完成当成最终证据。

### GLM-5：仍失败，失败位置发生变化

同一任务的 GLM-5 输出先触发了嵌套身份信封和后续局部结果字段缺失。当前边界归一化已使嵌套身份不再成为 schema 阻断，但模型随后生成的程序在 `wait_agents`/结果处理处出现 `MISSING_KEY`，并最终耗尽模型调用/输出预算，宿主返回 `needs_program`，没有发布错误答案。

这是跨输出行为对照：DeepSeek 能完成完整协作闭环，GLM 能开始协作但在长程序的结果形状维护上仍不稳定。失败不是通过放宽结果检查来掩盖的。

## 限制

本轮没有证明普通路径或超过三层嵌套任务已广泛可靠。真实正例的 `claims_verified` 仍为 false，因为该算术任务没有原始源数据证据要求；它只能证明协作和完成门流程。GLM 的剩余失败集中在长程序局部状态/结果字段维护，后续需要继续改进通用的阶段接口与观测驱动重规划，并重新评测七子 agent、五个子 agent 各自调用工具，以及超过三层嵌套任务。
