# Engineering Round 23

日期：2026-10-03（Asia/Shanghai）

## 本轮改动

将合约违反后的处理协议写入 coordinator 的通用 handoff 指令：

- `read_agent`/`review_agent` 报告违反时，不能接受或静默转换类型；
- 若纯消费能保持父保证，可以在父程序中完成确定性转换；
- 否则必须创建带明确修订合约的新 handoff，携带已观察的限制，或将分支 blocked；
- 替代 child 必须重新读取和 review，原 violating receipt 永远不能被当作 conforming。

这保留了双重控制：合约违反是行动后的观测，观测必须改变后续程序或分解；也保留了 assume–guarantee 合约的显式修订，而非放宽检查器。

## 离线验证

- 协作、coordinator recovery、projected observe、前端、recovery 和通用回归：113 passed。
- `ruff check src tests`、`compileall src tests`、`git diff --check`：通过。

## 真实 API 验证

API：用户授权网关 `http://35.220.164.252:3888/v1`。

### 四层嵌套复测：观测驱动修订已触发，但最终仍失败

使用 DeepSeek `deepseek-v4-flash`，要求根 → child → grandchild → great-grandchild，且所有层读取和复核直接 child；同时要求合约违反时创建修订合约的替代 child 或 blocked。

运行达到多层委派阶段。父程序观察到 child 的 `child_review` 输出违反声明的“对象或 null”保证，记录了明确的 `contract_check=violation`，随后尝试创建带修订合约的替代 child。这与上一轮直接保留标量结果并返回限制不同，说明观测实际改变了任务分解。

替代路径后，模型生成的长 bundle 把 normal program envelope 写成了不满足 `id/program/inputs` 的结构，经过一次修复仍未通过，最终 `needs_program`，约 566 秒、10 次模型调用、14 次工具调用。宿主没有接受原 violating receipt，也没有把替代 child 的未完成状态伪装成成功。

### 对照

上一轮同类嵌套任务返回 42，但只报告原合约违反；本轮相同语义约束促使模型尝试替代 handoff，随后暴露了新的程序 envelope 错误。两次都由真实 observation 决定后续路径，且完成门阻止未完成链发布为 conforming。

## 限制

本轮没有证明四层嵌套端到端成功；长程序的 bundle envelope 稳定性和预算耗时仍是主要瓶颈。替代 child 的合约修订已被触发，但未能完成收集和 review。七子 agent、五子多工具任务在前一轮已通过，数学真伪和外部源数据证据仍未验证。目标保持进行中。
