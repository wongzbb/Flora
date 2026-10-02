# Engineering Round 24

日期：2026-10-03（Asia/Shanghai）

## 本轮改动

补充合约违反后的通用协议：如果观察到的值是原始 primitive，而父任务保证可以由该值保持，则允许显式修订 child 的 primitive 接口，再由父层纯程序包装回父接口。协议要求记录旧/新 shape，原始 violating receipt 仍保持 non-conforming，不能用转换结果追溯性地接受原 receipt，也不能重放已有副作用。

这使合约修订成为行动观测驱动的局部程序变化，而不是输出格式放宽。

## 离线验证

- 协作、coordinator recovery、projected observe、前端、recovery 和通用回归：113 passed。
- `ruff check src tests`、`compileall src tests`、`git diff --check`：通过。

## 真实 API 验证

API：用户授权网关 `http://35.220.164.252:3888/v1`。

### 四层嵌套任务：通过

DeepSeek `deepseek-v4-flash` 任务：根 → child1 → child2 → child3，最底层计算 `17+25`，每层读取并 review 直接 child；每个 handoff 声明 `outputs.result` 为 number，primitive/object 违反时按新协议处理。

当前运行完成检查为 ready，返回 `{"result":42}`；直接 child 的 nested chain 完成，`contract_check=ok`，父层读取并 `review_agent(accepted)`。约 263 秒、3 次模型调用、8 次工具调用。与此前同类运行中替代 child 因预算耗尽或 bundle 结构错误不同，本次没有重放副作用，也没有接受不符合合约的原始 receipt。

### 跨输出对照

- 早期四层运行：child 返回 scalar，父层只能标记 contract violation 并带限制完成。
- 中间运行：模型看到 violation 后尝试替代 child，但长程序预算耗尽。
- 本轮：同一语义任务把 primitive 观测转为显式修订接口和纯 wrapper，nested contract 通过并完成。

这表明观测确实改变了后续合约/程序选择；并非通过统一放宽类型检查取得成功。

## 限制

该正例仍是简单算术，没有外部源数据，因此 `claims_verified=false` 仍正确。四层链的成功不能外推到任意深度、多个分支或副作用工具。更复杂的合约修订仍可能消耗大量上下文和预算；七子 agent、多工具任务和复杂事实证据还需继续跨模型验证。
