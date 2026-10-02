# Engineering Round 21

日期：2026-10-03（Asia/Shanghai）

## 本轮目标

本轮针对用户指定的压力任务验证七子 agent、复杂结果汇总，以及超过三层的嵌套委派；同时修复真实七子任务中发现的单一 JSON 分隔符错位。

## 改动

新增一个严格的边界层语法修复：当模型输出的第一个括号错位可以被唯一识别为缺失的 `]` 或 `}` 时，尝试替换该闭合符或在其前插入预期闭合符；只有候选文本没有任何剩余词法错误、能完整解析为 bundle，并随后通过全部 frontend、IR、tool capability 和 execution 校验时才接受。多处错位、截断、重复键、非有限数字和无法完整解析的文本仍拒绝。该修复不推断程序、结果、工具调用或合约。

## 离线验证

- recovery、协作、coordinator recovery、projected observe、前端和通用回归：112 passed。
- `ruff check src tests`：通过。
- `compileall src tests`：通过。
- `git diff --check`：通过。

## 真实 API 验证

API：用户授权网关 `http://35.220.164.252:3888/v1`。

### 七子 agent 微积分任务：通过

DeepSeek `deepseek-v4-flash` 任务：调用 7 个子 agent，每个生成不同微积分题目和答案；父任务读取 7 个完整结果、逐个 `review_agent`，再汇总。

首次运行已实际创建并收集 7 个 child，6 个原始 child 完成，1 个 child 因自身编译预算失败；父程序据实际失败观测启动替代 child，并完成剩余收集。但最终汇总程序出现一个可唯一定位的闭合符错位，旧代码拒绝。

在当前代码重跑后，7 个结果全部读取、逐个 review accepted，完成检查 ready，返回 7 道题目和答案。总计约 115 秒、5 次模型调用、12 次工具调用。结果仍明确包含 `claims_verified=false` 和“未独立验证数学真伪”的限制；review 只证明收集和 digest 完整性。

这也是本轮的跨输出对照：同一模型、同一任务的原始 malformed bundle 被拒绝；唯一分隔符修复后得到的完整 bundle 又经过正常语义校验并成功执行，没有改写答案或跳过 review。

### 四层嵌套多 agent：完成但合约不满足

DeepSeek `deepseek-v4-flash` 任务要求根 → 子 agent → 孙 agent → 曾孙 agent，最底层计算 `17+25=42`，每层读取和复核直接 child。

运行深度达到 4，根任务最终返回 42；约 643 秒、9 次模型调用、32 次工具调用，完成检查 ready。父子链的收集和 review 都执行了，但宿主发现直接 child 声明输出合约 `{result:42}`，实际返回标量 `42`。该结果被标记为 non-conforming，保留 observed value 和限制说明，未被当作合约满足。这证明合约约束跨嵌套层传播并能阻止静默类型漂移；同时说明嵌套任务的模型合约遵循仍不稳定。

## 限制

七子任务和嵌套任务都没有外部数学工具或源数据证据，因此不能证明答案真伪。嵌套任务虽然完成门 ready，但合约违反使其不能计为完全成功。超过三层的运行时间约十分钟，预算和模型长程序稳定性仍是主要风险。普通单 agent、多工具嵌套以及 GLM/DeepSeek 的更广任务矩阵仍需继续评测。
