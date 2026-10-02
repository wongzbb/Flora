# Engineering Round 22

日期：2026-10-03（Asia/Shanghai）

## 本轮改动

上一轮真实五子工具任务中，所有 child 的工具调用和结果都已完成，但父程序在 bundle 成员边界遗漏了 candidate/program 数组闭合符。继续扩展边界层修复：当词法扫描没有内部错位、只剩未闭合结构时，仅在 bundle 已知成员（`incumbent`、`diagnostics`、`expected_epoch`、`expected_digest`）之前尝试长度不超过 3 的闭合符序列；候选必须唯一、完整解析为 bundle，并继续通过全部 frontend、IR、工具权限和运行时校验。它不修改程序值、工具请求、合约或 review 结论。

## 离线验证

- recovery、协作、coordinator recovery、projected observe、前端和通用回归：113 passed。
- `ruff check src tests`：通过。
- `compileall src tests`：通过。
- `git diff --check`：通过。

## 真实 API 验证

API：用户授权网关 `http://35.220.164.252:3888/v1`。

### 五子 agent、多工具任务：通过

DeepSeek `deepseek-v4-flash`：调用 5 个子 agent，每个执行 1–3 个不同的只读工具，父任务读取完整结果并逐个 review，再汇总。

当前代码完成检查为 ready，约 130 秒、5 个 child、9 次工具调用。五个 child 实际执行了 `list_files`、`workspace_context`、`search_files`、`list_sources`、`list_skills` 或 `web_search` 等只读工具；父任务逐一读取并接受 review。网络策略拒绝的搜索、空工作区和空来源都被作为明确限制汇总，没有伪造页面或文件结果。没有 child 写文件或修改外部状态。

### 跨输出对照

同一任务的前一版输出在相同 child 证据已经收集后只因 bundle 边界闭合符错位被拒绝；本轮唯一可判定的结构修复后，仍执行原有程序、工具和 review gate 才成功。这验证了边界修复没有替换语义检查。

## 与此前压力任务的结合证据

本轮沿用已验证的七子 agent 和四层嵌套结果：七子任务已完成 7 个 child 的逐一读取和 review；四层嵌套能运行到根返回，但曾发现底层 `{result:42}` 合约被标量 `42` 违反，并保留限制。五子工具任务则证明多工具调用和网络失败观测可以在同一 review/完成门中汇总。

## 限制

本轮五子任务没有可验证的源数据事实，`claims_verified=false` 是正确结果。网络搜索被策略拒绝，不能据此评价 web 工具成功率。四层嵌套合约违反仍未自动修订；复杂嵌套任务耗时和模型合约遵循仍是主要风险。普通单 agent、更多模型和超过四层的多分支嵌套仍需继续评测。
