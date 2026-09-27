# 排错与限制

定位故障时先确定它位于哪一层：安装与导入、静态 IR、纯 VM、工具效果、模型传输、预算、历史锚点或合约关系。不要把所有错误归为“模型不够聪明”。

## 先检查直接使用入口

| 现象 | 处理 |
| --- | --- |
| 没有配置模型 | CLI 运行 setup 或传 --model；Python 传 Agent(model=...) 或设置 FLORA_MODEL |
| CLI 已 setup，Python 仍说缺模型 | Python API 使用显式参数/环境变量；设置 FLORA_MODEL，或传 model |
| 没有文件工具 | 显式传 workspace 或 --workspace；默认不会开放当前目录 |
| 无法运行测试命令 | 只有同时选择工作目录与 allow_commands 才注册 run_command |
| Windows 工作目录初始化失败 | 标准文件包依赖 POSIX；用 WSL 或提供自己的文件函数 |
| 文件改写 hash 冲突 | 文件已变；重新读取当前内容后重新判断，不猜测旧 hash |
| 文件只读到前一页 | 跟随 next_offset，观察 has_more；完整 hash 不表示已读完内容 |
| 会话 fingerprint 不匹配 | 使用原来的模型、工具、workspace、instructions 和配置，或新建会话 |
| 已有未完成轮次 | 用 resume 或 /resume 继续，不用新任务覆盖 |
| unknown 工具效果导致 resume 仍停止 | 先核实真实结果并明确 resolve；恢复不是盲目重发 |
| ask 抛 AgentRunError | 查看 exc.result.status/reason；需要分状态处理时使用 run |
| 相同会话已占用 | 关闭另一个执行者；不要并发写同一目录 |

普通用户不必为了排错先改 IR；先检查配置、真实工具结果与运行报告。以下表格面向进一步定位内核或模型生成问题的开发者。

## 常见问题速查

| 现象或错误 | 原因 | 处理 |
| --- | --- | --- |
| 找不到 `flora` 命令 | 安装到了不同解释器或环境没有激活 | 用同一个 Python 运行 `python -m pip install .`，再用 `python -m flora --help` |
| `undefined variable` | 读取未声明寄存器，或未显式 capture | 检查块参数、前序 dest 和续行 capture |
| `resume parameters must equal ...` | 续行参数与 capture + bind 不一致 | 统一 key 集合，不依赖上一个块的隐式变量 |
| `tool not allowed` | 程序引用未注册工具 | 检查任务能力集合和适配器名称；不要为通过校验自动扩权 |
| `Tool argument type mismatch` | 参数违反已注册 schema | 查看工具 schema 与实际 args |
| `MISSING_KEY` | 直接 get 了不存在的字段或索引 | 用 has / get_default 或明确分支 |
| `FUEL_EXHAUSTED` | 纯程序不终止或配额不足 | 查看循环与机器 steps；保留 UNKNOWN，不改成 PASS |
| `RECEIPT_UNAVAILABLE` | 请求不存在的实际历史索引 | 使用原 trace_index，不能按裁剪后模型列表重编号 |
| `StaleAnchor` | bundle 或请求引用旧历史 | 基于当前真实 trace 重新编译，不直接改数字 |
| `InterruptedEffect` | 有 pending / interrupted_unknown | 先核实已发生的动作，再明确 resolve |
| `compiler output failed validation` | 模型没有生成合法 bundle | 查看具体校验错误和受限修复报告，检查模型上下文能力 |
| `Insufficient output-token reservation` | 剩余额度无法覆盖本次输出上限 | 在任务配置中调整合理上限或正常终止 |
| `select one relation before fitting a guard` | 将不同合约关系的标签混合 | 为 fit_guard 明确指定 relation |
| 诊断分数一直为 0 | 缺少互斥预测、见证或行为差异 | 查看 DiagnosticReport.reasons、pairs 和 witness_results |
| `explicit_consumer_checkpoint_mapping_required` | 改写了程序但未映射完整消费者状态 | 提供显式迁移或候选机器映射，不能退回入口冒充同一上下文 |

## 模型 HTTP 故障

401 / 403 首先检查密钥环境变量和供应商权限；404 检查 base_url 是否多写或漏写 `/v1`；400 检查 `max_tokens_parameter` 以及额外模型参数。provider 不输出原始错误 body，是为了避免把密钥或请求内容带入错误日志。

收到 HTTP 重定向会失败。请配置最终可信 endpoint，不通过自动跟随 redirect 解决。网络 timeout 没有自动重试，usage 可能未知；这笔请求仍按共享预算记录。

支持 plain-text、非流式、单 choice 的 Chat Completions 响应。多模态、流式、供应商原生工具调用和其他响应协议需要自定义 provider 适配，不是默认传输层的隐含能力。

## 工具失败与状态未知

业务工具主动抛出的普通异常会保存为 raised，并交给 IR 续行。网络超时应明确抛出 `TimeoutError` 或 `InterruptedEffect`，以表达效果可能已经发生。把不确定写入错误包装成普通 ValueError，运行时就无法替适配器推断真正的不确定性。

同步工具卡住不能由 fuel 中断。为网络和数据库请求设置自己的 timeout；必要时使用进程级任务监管。async timeout 会取消等待，但远端效果仍可能发生。

## 合约通过但任务仍然失败

这是可能的。DEFINED_PREFIX 只检查内部计算可达边界；SAME_BOUNDARY 可能保持旧错误；SOURCE_FIDELITY 只证明特定来源关系。任务成功需要原环境的评估，不能从局部 PASS 推导。

经验 guard 如果仅见过正例，可能非常宽。命中后仍应进行本地检查。规则无法区分矛盾样本时应保留 no_guard 或 partial，而不是让模型解释掉反例。

## 为什么无法恢复某个旧程序

当前真实世界可能已经经历了旧程序未预期的动作。Flora 会冻结这个续行，再要求显式重新编译或状态迁移。这是执行语义，不是恢复 bug。

历史记录不足、工具返回只能以进程本地句柄保存、检查点处在不支持的上下文映射位置，也会阻止重放。系统宁可返回未知，也不会给新请求借用旧响应。

## 当前明确不包含的能力

- 模型权重训练、自动 reinforcement learning 或 trainer。
- 任意 Python 代码的安全沙箱或宿主进程隔离。
- 外部工具的快照、回滚、分支环境或分布式 exactly-once。
- 对任意自然语言规范的形式化正确性证明。
- 真实奖励函数、准确后验概率或最优信息价值求解。
- 官方 AppWorld / StuLife 数据与已经取得的性能优势。
- 所有供应商协议、完整 JSON Schema、任意对象的跨进程序列化。

这些边界不影响已实现机制的运行；它们决定如何诚实解释执行结果，以及未来扩展需要新增哪些明确接口。
