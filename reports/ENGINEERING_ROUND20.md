# Engineering Round 20

日期：2026-10-03（Asia/Shanghai）

## 本轮发现

GLM-5 的真实协作任务暴露了两个通用问题：

1. `wait_agents` 返回“持久记录 + 嵌套 read envelope”，与提示中描述的结果窗口不一致。模型因此把记录字段、结果字段混用。
2. child 仍在运行时，结果并不存在；程序直接索引结果会产生 `MISSING_KEY`。模型随后还把纯 IR 操作 `read_receipt` 当成外部工具调用。

## 改动

### 统一等待和读取的结果视图

`wait_agents` 现在为每个 child 返回与 `read_agent` 相同的扁平 read view，并合并状态元数据；不再把 read view 放进另一个 `result` 信封。完整窗口包含结构化 `result`、`result_digest`、`status` 和 `claims_verified=false`；未完成 child 在 wait 视图中明确返回 `result: null` 和空 digest。该 null 表示尚未观察到答案，宿主不伪造值，也不会放宽 review gate。

`read_agent` 的分页语义保持不变：不完整窗口仍不提供结构化 result，必须跟随 `next_offset`；只有完整读取才可用于 review。

### 提示和纯操作边界

协作提示明确说明：`result_available=false` 或 `result=null` 时必须等待或记录限制，不能索引 null；`read_receipt` 是纯 IR operation，只能放在 `ops`，不能当作 observe/effect 工具。

## 离线验证

- 协作、coordinator recovery、projected observe、前端和通用回归：88 passed。
- `ruff check src tests`：通过。
- `compileall src tests`：通过。
- `git diff --check`：通过。

## 真实 API 对照

API：用户授权网关 `http://35.220.164.252:3888/v1`。

### GLM-5：通过（含观测驱动修复）

任务为两个子 agent 分别计算 `17+25`、`9*8`，父任务必须读取、逐个 review 并求和。GLM 首次阶段出现 `MISSING_KEY`，宿主保留已完成的 spawn/wait 观测并要求修复当前 consumer；模型随后完成结果收集、两个 review 和 `update_work`，返回 `42`、`72`、`114`，完成检查为 ready。总计约 303 秒、8 次模型调用、10 次工具调用。该过程展示了行动产生的失败观测确实改变了后续程序，而不是静默继续或重放副作用。

### DeepSeek `deepseek-v4-flash`：通过

同一任务在当前代码一次完成，返回数值 `114`，完成检查为 ready；约 79 秒、4 次模型调用、6 次工具调用。它使用稳定 child IDs、读取和复核流程后汇总。两模型最终均未被宿主用格式特判放行；它们的程序路径不同，但共享同一结果收集和 review 约束。

## 限制

这两个正例只覆盖两层、两个 child 的算术协作，且没有原始源数据证据，所以 `claims_verified=false` 仍是正确状态。它们不能证明七个 child、多工具嵌套、超过三层递归或复杂源数据任务已经可靠。GLM 的一次修复仍消耗约五分钟，复杂程序的预算和局部状态维护仍需继续评测。
