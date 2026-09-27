# 会话、结果与恢复

一次任务可以包含多次模型思考和多次工具调用。一个会话可以包含多轮任务。区分两者很重要：后续任务不是对未知工具结果的自动重试，重新打开会话也不是把外部环境回滚到旧状态。

## 内存会话

不设置 `session_dir` 时，一个 Agent 在当前进程内保留会话历史与共享预算。退出进程或关闭实例后，不会得到自动恢复文件。`invoke` 使用一次性临时会话，适合单次任务；需要多轮上下文时保持同一个 Agent 对象。

## 持久会话

```python network
from flora import Agent

with Agent(workspace="./my-project", session_dir="./my-agent-session") as agent:
    result = agent.run("阅读项目文档，列出启动步骤。")
    print(result.status)
    print(agent.current_trace_path)
```

把工作目录换成实际存在的路径。会话目录保存元数据、共享预算、保留的任务历史与每轮独立 SQLite 轨迹。随后使用相同设置再次构造 Agent，便可加载该会话。CLI 的等价参数是 `--session ./my-agent-session`。

重新打开时会检查模型身份、工具描述、工作目录、instructions 和执行配置等会话绑定。修改这些条件后继续同一会话可能改变旧程序的含义，所以不允许悄悄替换。要更换能力集合或实验条件，请使用新的会话目录。检查并不验证工具函数实现是否与原来字节级相同；宿主仍须提供语义一致、连接同一个真实业务环境的工具。

会话目录应由一个执行者持有，不能让多个进程同时继续同一轨迹。部署时保留原业务权限并保护会话文件，它们可能包含任务、参数、工具返回和结果。

## 连续对话保留什么

已完成任务与结果以有界记录提供给下一轮，最多保留最近 32 轮且总编码不超过 64 KiB；超出限额按整条记录省略，并显式记载遗漏数量。单条过大的结果也会整条省略，不把裁掉的片段伪装成完整事实。系统不会把模型生成摘要伪装成原始事实，不会静默重放过去的所有工具调用，也不会承诺把所有旧 data 与回执无限保留到新轮次。

工具状态始终属于真实环境。前一轮修改文件后，下一轮读到的是修改后的文件；两次相同参数的工具调用也可能得到不同返回。需要最新信息时应通过任务原本开放的工具再次取得，而不是把历史结果当作实时查询。

`agent.status()` 提供不含任务正文的本地状态：是否有活跃轮次、是否需要 resume、当前/累计轮次、遗漏轮数、轨迹路径与预算。`agent.history` 则包含实际保留的任务与结果，注意其数据访问范围。

## 查看结构化结果

```python network
from flora import Agent

with Agent() as agent:
    result = agent.run("用中文给出三条可执行的代码审查建议。")
    print(result.status, result.value)
    print(result.reason)
    print(result.budget)
    print(result.to_dict())
```

| 状态 | 含义 | 接下来怎么处理 |
| --- | --- | --- |
| `completed` | 程序返回，且可选完成条件通过 | 读取 value；按任务协议判断业务是否正确 |
| `incomplete` | 未达到完成条件或推进限制 | 读取 reason 和 reports，判断是否继续 |
| `budget_exhausted` | 共享预算或资源限额耗尽 | 检查配置与任务规模；恢复不会自动重置预算 |
| `needs_program` | 目前没有可执行程序，编译缺失或失败 | 排查模型配置、格式与编译报告，再继续同一轮 |
| `interrupted_unknown` | 真实工具结果未确认 | 先核实外部结果；禁止盲目重发动作 |

`completed` 是执行状态，不是对任意自然语言目标的形式化正确性证明。例如“写好了测试”还需要实际测试结果来支持；没有开放测试工具时，模型不能凭返回值制造该证据。

## 继续尚未完成的一轮

```python network
from flora import Agent

# 使用创建该会话时相同的模型、工具和工作目录设置。
with Agent(workspace="./my-project", session_dir="./my-agent-session") as agent:
    result = agent.resume()
    print(result.status, result.reason)
```

`resume()` 不接受替换任务文本；它继续原任务、原预算和当前已经记录的真实轨迹。新任务使用 `run()`。上一轮未完成时，新任务会被阻止，避免覆盖仍需处理的状态。CLI 用 `/resume`；`/status` 会显示当前轨迹路径和结果。

网络失败之后可以在修复配置或可达性问题后继续；已预留但无法确认的模型消耗会保守记账，不因重启退款。工具结果未知时继续运行仍会停止在未知状态。

## 工具结果未知时

设想“提交订单”在客户端超时：订单可能已成功，也可能未成功。记录超时并不证明动作被撤销。此时应通过原业务日志、请求 ID 或已授权的查询接口核实结果，然后将已知事实写入原轨迹。

低层 `resolve` 命令只登记已经核实的结果，不执行原工具、不推测成功、不回滚世界。一般使用者无需学习该格式，只有处理这种中断时才需要阅读后半部分「事件存储、恢复与重放」的完整步骤。核实之后再使用原配置打开会话并 resume。

如果工具返回的是进程本地不透明对象，持久化只保存引用标记，不保存对象本身。重启后不能恢复丢失的数据库游标或 SDK 实例；应使用原本允许的工具重新取得，或明确停止，不会自动重建宿主对象。

## 共享预算

```python network
from flora import Agent
from flora.budget import BudgetLimits

limits = BudgetLimits(
    max_tool_calls=40,
    max_model_calls=8,
    max_input_tokens=100000,
    max_output_tokens=65536,
    max_wall_seconds=900,
)
with Agent(budget_limits=limits) as agent:
    print(agent.ask("设计一份精简的代码发布清单。"))
    print(agent.ask("把上一份清单压缩成五项。"))
```

两个任务共享上面的总额度。模型格式修复、根据工具结果重新思考、诊断动作均会消耗实际资源。恢复时预算不清零；持久预算的时间记录包含此前已保存的运行耗时，但不会把程序关闭期间的现实等待时间全部计作新的工作耗时。

模型请求前预留最大输出 token；未知 usage 保守消耗预留值。输入 token 依服务返回的 usage 记账，没有供应商 tokenizer 时不能承诺每次请求前精确裁掉所有可能超出的输入 token。字节限额与模型上下文 token 限额是两种约束。

## 资源关闭

推荐 `with Agent(...) as agent:`，也可在结束后显式调用 `close()`。关闭会话会释放框架持有的连接与锁，不会自动关闭你在自定义函数中创建的每一个业务客户端；这些资源仍由工具宿主管理。
