# Python API 索引

常用入口从包根导入：`Agent`、`invoke`、`AgentRunError`、`tool` 和 `make_registry`。内部组件使用 `flora.<module>`。本章列出主要接口，完整输入格式见对应专题。

## 直接使用 API

| 接口 | 参数与结果 |
| --- | --- |
| `Agent(model=None, *, provider=None, tools=None, workspace=None, allow_commands=False, session_dir=None, instructions="", on_event=None, config=None, budget_limits=None, provider_options=None, compiler_options=None, completion_guard=None)` | 同步的任务与会话入口；完整使用方法见前面的 Python 教程 |
| `Agent.run(task, *, data=None)` | 返回 RunResult；共享会话预算，未完成轮次不得被新任务覆盖 |
| `Agent.ask(task, *, data=None)` | 完成时返回 value，否则抛 AgentRunError，`.result` 保留结果 |
| `Agent.resume()` | 继续原任务和真实轨迹，返回 RunResult |
| `Agent.status()` | 返回本地会话、当前轮次、已完成/遗漏轮次数与预算，不含任务正文 |
| `Agent.close()` | 关闭会话资源；支持 with 上下文管理 |
| `invoke(task, *, data=None, **Agent参数)` | 创建一次性 Agent，返回完成值并关闭 |
| `tool(function=None, *, name=None, description=None, input_schema=None, opaque_parameters=(), timeout_seconds=None)` | 将函数包装为 ToolSpec，可作装饰器 |
| `make_registry(tools=None)` | 从注册表、函数/ToolSpec 列表或别名映射构造注册表 |

Agent 的可观察属性包括 `budget`、`history`、`last_result`、`current_trace_path` 和 `session_dir`。`config` 与 `budget_limits` 接受各自 dataclass 或字段字典。显式 provider 与 model/provider_options 不能同时传入。

`workspace.WorkspaceTools(root, *, allow_commands=False, max_file_bytes=1048576, max_output_bytes=65536, command_timeout=30, protected_paths=())` 提供 `.specs()`，用于自定义标准文件工具限额；依赖 POSIX no-follow 文件操作。

`session.SessionStateError` 表示会话绑定或轮次状态不允许当前操作；`session.SessionBusyError` 表示会话占用。持久会话恢复须使用同一能力和实际外部环境。

## 编码任务 API

从 `flora.coding` 导入 `CodingAgent`、`CodingWorkspace` 和 `CodingResult`。它们复用上述 Agent 内核；任务、独立 worktree、测试证据、补丁、恢复与参数的完整说明见前面的「Flora Coding Agent」章节。

## Runtime

```python
from flora.runtime import Runtime, RuntimeConfig, RunResult
from flora.tools import ToolRegistry

runtime = Runtime(ToolRegistry(), config=RuntimeConfig())
assert runtime.trace.epoch == 0
```

| 接口 | 参数与结果 |
| --- | --- |
| `Runtime(tools, *, compiler=None, trace=None, budget=None, config=None, memory=None, completion_guard=None)` | tools 为注册表；默认使用内存 trace、默认预算与配置 |
| `run(task=None, *, bundle=None)` | 返回 RunResult；第一次需要非空任务，后续不能静默替换任务 |
| `install_bundle(bundle)` | 检查锚点、程序、工具、诊断及可选修订，安装候选 |
| `apply_revision(candidate_id, program, *, migration, mode="PRESERVE", revision_id="revision", install=True)` | 纯迁移与历史检查，返回 accepted、逐样本结果和收益标记 |
| `Runtime.restore(tools, trace, *, compiler=None, completion_guard=None)` | 从真实日志与检查点恢复，匹配已记录事件而不重发 |
| `RunResult.to_dict()` | 转成标准 JSON 可表示的结果字典 |

`RunResult` 字段：`status`、`value`、`reason`、`steps`、`epoch`、`trace_digest`、`budget`、`reports`。

| status | 解释 |
| --- | --- |
| `completed` | 默认程序已返回；任务正确性仍由任务协议判断 |
| `incomplete` | 运行步数等推进条件未满足完成 |
| `budget_exhausted` | 共享资源预算或编译次数耗尽 |
| `needs_program` | 没有可执行程序，且编译不可用或失败 |
| `interrupted_unknown` | 外部效果未能确认，未发送自动重试 |

## IR 与 VM

| 接口 | 用途 |
| --- | --- |
| `ir.parse_program(data, allowed_tools=None)` | 严格静态检查并复制 JSON IR |
| `ir.OP_ARITIES` | 纯指令参数范围表 |
| `vm.new_machine(program, inputs=None, *, mode="observed")` | 创建入口机器 |
| `vm.run_until_boundary(machine, *, receipts=(), memory=None, fuel=50000, max_value_bytes=1048576)` | 纯运行到下一边界 |
| `vm.resume(machine, outcome)` | 恢复效果续行；保持原模式 |
| `vm.Machine.to_dict()` / `from_dict(data)` | 完整可校验机器序列化 |
| `vm.Boundary.to_dict()` | 边界及内部候选序列化 |
| `vm.snapshot_program(machine)` | 仅当无需遗漏调用者时导出入口级快照，否则返回 None |

## 工具与效果执行器

| 接口 | 用途 |
| --- | --- |
| `tools.ToolSpec(name, handler, description="", input_schema=None, timeout_seconds=None, opaque_parameters=())` | 注册可信宿主能力 |
| `tools.ToolRegistry(tools=None, *, opaque_store=None)` | 创建工具集合 |
| `register(spec)` | 注册一个不重名工具 |
| `names` | 已排序名称 tuple |
| `descriptions()` | 编译器可见的名称、说明和参数 schema |
| `validate_request(request)` | 工具、JSON、schema 与处理函数签名检查 |
| `call(request)` | 低层调用；不包含完整 trace / budget 管理 |
| `effects.EffectExecutor(tools, trace, budget, *, max_output_bytes=4194304)` | 单一真实效果入口 |
| `execute(request, *, epoch, trace_digest, mode="observed", before_dispatch=None)` | 锚点检查、登记、执行、结算；返回真实记录 |

业务应用一般调用 Runtime；直接使用 EffectExecutor 时，调用者负责候选生命周期与检查点协调。

## 环境桥接

`adapters.AgentEnvBridge(agent_env, root=True)` 接入公开 agent-facing 工具协议。`registry` 是生成的工具注册表，`instructions` 是公开任务说明，`completion_guard` 是根会话完成保护或 None。只支持 pull 交付，不负责建立、重置或评分外部世界；完整接入限制见公平实验章节。

## 不透明对象

`opaque.OpaqueStore(*, max_handles=1024, ttl_seconds=None)` 保存进程本地原对象引用；`register(value)` 注册身份，`encode_result(value)` 在普通 JSON 结构内替换非 JSON 对象，`resolve(carrier)` 认证并返回原对象，`revoke(carrier)` / `clear()` 释放引用，`stats()` 返回资源统计。`is_opaque(value)` 只识别格式，不能代替认证。

`ToolRegistry.encode_result(value)` 使用其 store 编码实际返回，`materialize_args(request)` 验证并只还原获授权的句柄位置。程序和开发者都不能根据一份持久化 token 重建丢失的宿主对象。

## 编译器与模型

| 接口 | 用途 |
| --- | --- |
| `compiler.CompilerContext(...)` | 原任务、能力、实际历史与预算视图 |
| `compiler.validate_bundle(bundle, context=None, ...)` | 独立检查 bundle 和可选修订 |
| `compiler.ScriptedCompiler(bundles)` | 列表或 callable 驱动的离线编译器 |
| `compiler.LLMCompiler(provider, ...)` | 固定模型编译，有限格式修复 |
| `compile(context)` | 返回通过校验的 bundle，不执行工具 |
| `build_messages(context)` | 返回带明确遗漏说明的模型消息视图 |
| `set_accounting(before_call, on_usage)` | 装配共享预算回调；Runtime 会绑定自己的预算与检查点 |
| `providers.ModelResponse(text, input_tokens=None, output_tokens=None, request_id=None, raw_metadata={})` | 模型文本及可得的真实用量 |
| `providers.OpenAICompatibleProvider(...)` | 无 SDK 的有界 HTTP 传输 |

完整 provider/compiler 参数表在模型章节。`ScriptedCompiler.requests` 保留接收过的上下文，用于检查离线编译调用顺序。

## 消费者合约

| 接口 | 用途 |
| --- | --- |
| `contracts.ContextCheck(id, ...)` | 具体观察与消费者上下文 |
| `contracts.CheckResult(verdict, relation, witness={})` | 局部检查结果 |
| `contracts.check_revision(program, context, *, fuel=50000)` | 纯检查，不调用工具或模型 |
| `contracts.evaluate_predicate(predicate, value)` | 返回 True、False 或 None |
| `contracts.fit_guard(samples, *, max_atoms=24, max_terms=3, max_clauses=4)` | 有界经验规则综合 |
| `contracts.ContractStore(max_records=512, max_bytes=1048576)` | 限量保存可重算检查证据 |
| `record(program, context, result=None, *, fuel=50000)` | 重算后记录，拒绝伪造结果 |
| `fit_guard(program, *, relation=None, ...)` | 按程序 digest 与单一关系拟合 |
| `to_dict()` / `ContractStore.from_dict(data)` | 保存并重算校验恢复 |

`ContextCheck` 的可选字段包括：`inputs`、`receipts`、`memory`、`reference_program`、`relation`、`source_path`、`output_path`、`source_mode`、`mode`、`reference_machine`、`candidate_machine`。缺省 relation 为 DEFINED_PREFIX，source_mode 为 equal，mode 为 observed。

## 程序复用

`reuse.ReuseLibrary(max_variants=64)` 保存带消费者 scope 的变体。`register(source_machine, program, migration, guard, *, mode="PRESERVE", variant_id=None)` 登记经过结构校验的项；`route(machine, *, receipts, memory, fuel=50000)` 运行规则、纯迁移和当前关系检查，返回 ReuseDecision。`variants` 返回只读副本；`drop_oldest()` 整条淘汰最旧项；`to_dict()` / `from_dict()` 保存并严格恢复。CHANGE 不可登记为自动兼容复用。

## 诊断与调度

| 接口 | 用途 |
| --- | --- |
| `diagnostics.validate_diagnostic(diag, *, allowed_tools=None, candidate_ids=None)` | 严格诊断结构校验 |
| `diagnostics.proven_exclusive(first, second)` | 保守证明谓词不能同时为真 |
| `diagnostics.evaluate_diagnostic(diag, effect_boundary=None, *, candidate_ids=None, receipts=(), memory=None, anchor_epoch=0, anchor_digest="", fuel=10000)` | 纯执行假想续行，返回 DiagnosticReport |
| `diagnostics.check_forecasts(diag, actual_event, *, expected_request, expected_event_id, expected_previous_hash)` | 对精确的真实事件检验限定预测 |
| `scheduler.choose(frontiers, incumbent, *, diagnostic_allowed=True)` | 返回被选 frontier ID，不执行动作 |

DiagnosticReport 字段包括 id、request、score、anchor_epoch、anchor_digest、groups、witness_results、pairs、reasons；`eligible` 等于 score > 0，`to_dict()` 明确标记 hypothetical 与 contract_evidence=false。

## Trace、预算与重放

| 接口 | 用途 |
| --- | --- |
| `trace.MemoryTrace(max_journal_bytes=7340032, max_checkpoint_bytes=7340032)` / `trace.SQLiteTrace(path, ...)` | 内存或本地持久日志 |
| `records`、`epoch`、`digest` | 当前真实事件视图与锚点 |
| `journal()` / `export()` | 追加式条目或完整可导出对象 |
| `begin(tool, args, *, expected_epoch, expected_digest)` | 登记 pending 请求 |
| `settle(event_id, outcome)` | 追加真实结果 |
| `resolve(event_id, outcome, *, reason)` | 记录已核实结果，不调用工具 |
| `save_checkpoint(data)` / `load_checkpoint()` | 保存与读取 agent 状态 |
| `MemoryTrace.from_dict(data)` | 校验并导入日志 |
| `close()` | 关闭持久连接 |
| `budget.Budget(limits=None)` | 共享模型与工具资源账本 |
| `budget.BudgetLimits(...)` | 五类外部预算上限 |
| `Budget.to_dict()` / `Budget.from_dict(data)` | 保存、恢复消耗；未知 reservation 不退款 |
| `replay.replay(program, inputs, records, *, memory=None, fuel=50000, max_effects=1000, max_value_bytes=1048576)` | 精确顺序重放纯计算；不调用工具 |

## 资源边界

`resources.ResourceLimitExceeded` 是 BudgetExceeded 子类，保留 resource、limit 与可得的 required 字节数。`encoded_size()` 计算规范 JSON 字节，`retain_newest()` 只适用于可替换的可选记录，禁止用它裁剪真实 journal。`ContractStore.trim_bytes(max_bytes)` 整条移除最旧见证并返回保留统计。

## 严格 JSON 与异常

`values.canonical_json(value)` 产生排序、无多余空格的严格 JSON；`digest(value)` 为其 SHA256；`clone(value)` 复制并校验；`bounded_clone(value, max_bytes)` 增加指定字节界限。

异常基类为 `FloraError`。常用子类：`ValidationError`、`MachineStateError`、`BudgetExceeded`、`StaleAnchor`、`InterruptedEffect`、`CompilerError`、`TraceIntegrityError`；provider 的 `ProviderError` 是 CompilerError 子类。不要捕获所有异常后无条件重新提交工具。
