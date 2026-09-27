# Flora Coding Agent

给 Flora 一个代码仓库、一个自然语言任务和一条测试命令。它会在独立的 Git worktree 中读取代码、修改文件、运行测试并生成补丁。你不需要编写内部程序。

## 完成第一个编码任务

准备 Python 3.11+、Git 和 POSIX 环境；Windows 使用 WSL。先按安装章节从 `coding-agent` 分支安装 Flora 并配置模型。目标仓库必须已经有提交，且没有未提交的修改或未忽略的新文件。

下面假设 `my-project` 是目标仓库，其中的测试可用 `python -B -m unittest discover -s tests` 执行。请换成你的实际测试命令。`flora-job` 放在仓库外面。

```bash
export OPENAI_API_KEY="你的模型服务密钥"
flora setup --model YOUR_MODEL_ID --base-url https://your-provider.example/v1

flora code "修复分页函数在空列表输入时的错误，保留现有接口。" \
  --repo ./my-project \
  --session ./flora-job \
  --test "python -B -m unittest discover -s tests"
```

CLI 显示规划和工具执行进度。结束时输出状态、工作目录、补丁路径、测试状态和模型总结。退出码 `0` 表示系统接受了完成；退出码 `2` 表示未完成或输入有误。

目标仓库仍保持原样。实际修改位于 `flora-job/worktree`，完整补丁位于 `flora-job/changes.patch`。Flora 不会替你提交或推送项目。

## 查看并采用结果

这两个命令只检查本地文件，不需要模型或 API 密钥：

```bash
flora code --session ./flora-job --status
flora code --session ./flora-job --diff
```

先阅读 diff 和测试输出。确认需要采用时，在原仓库执行：

```bash
git -C ./my-project apply --check ../flora-job/changes.patch
git -C ./my-project apply ../flora-job/changes.patch
```

`--check` 只检查可应用性。第二条才真正修改原仓库。补丁包含 Git 可见的修改、删除和新文件，也支持 Git 二进制补丁；被 Git 忽略的文件不在补丁中。原仓库后来发生变化时，补丁可能无法直接应用，不要跳过检查或强行覆盖自己的修改。

## 再提一个任务，或恢复未完成任务

一个任务完成后，可以复用同一个会话继续修改。新任务继承 worktree 中已有的改动和会话历史，预算也是累计的；每个新任务都会清除“已测试”的完成资格。

```bash
flora code "为刚才的修复增加边界用例。" --session ./flora-job
```

对于模型请求中断或输出格式校验失败等仍有活动任务的状态，CLI 会显示具体原因、累计用量与恢复命令。使用恢复：

```bash
flora code --session ./flora-job --resume
```

继续和恢复时使用与创建会话相同的模型、配置和权限。若起初使用了 `--config`，后续模型调用也应传入同一配置文件。CLI 自动读取保存的测试命令、测试超时和命令权限；不会自动保存或恢复 API 密钥。

预算耗尽后，恢复不会获得额外预算。已提交但结果未知的工具操作也不会被恢复机制随意重发。先检查状态及原始会话记录，再决定是否需要人工处理或创建新会话。

## 测试命令如何选择

选择你确实信任、能够验证任务相关行为的命令。Flora 不知道项目真正的正确答案，不能把“进程返回 0”解释为所有需求都实现了。

```bash
# Python unittest，禁止生成新的字节码缓存
flora code "修复任务" --repo ./my-project --session ./job-python \
  --test "python -B -m unittest discover -s tests" --test-timeout 180

# 已经安装 pytest 的 Python 环境
flora code "修复任务" --repo ./my-project --session ./job-pytest \
  --test "python -m pytest -q" --test-timeout 180

# JavaScript 项目，依赖需要预先准备
flora code "修复任务" --repo ./my-project --session ./job-node \
  --test "npm test -- --runInBand" --test-timeout 300
```

命令按参数列表执行，不经过 shell。`&&`、管道、重定向和 shell 变量展开不会自动生效；需要多步检查时，准备一个受信任的检查脚本并将它设为测试命令。不要用 `true` 等无验证内容的命令充当验收。

Python 在极短时间内对同大小文件反复改写，可能受到已有字节码缓存影响。使用干净测试环境；示例中的 `-B` 避免生成新缓存，但不会删除已有缓存。

## 环境与权限

Git worktree 提供独立的代码检出，不是操作系统安全沙箱。测试命令执行的是项目代码，拥有宿主用户的权限，也继承当前进程环境。只对可信仓库执行；需要安全隔离时，将整个 Flora 进程和目标仓库放入你管理的容器或虚拟机，再给予所需凭据。

默认模型只能使用文件工具、`run_tests`、`coding_status` 和 `inspect_changes`。文件工具限制在 worktree 内，保护 Git 元数据并拒绝符号链接路径。测试命令由宿主固定，模型不能通过 `run_tests` 的参数更换命令，但仓库内的测试与源码仍可编辑；运行结束后应审查测试是否被弱化。

需要安装依赖、编译或运行其他命令时，可显式开启：

```bash
flora code "实现任务并运行测试" --repo ./my-project --session ./job-build \
  --test "python -m pytest -q" --allow-commands
```

这会给予模型任意宿主命令执行能力。命令的工作目录在 worktree，不代表进程无法访问外部路径或网络。不要把目录限制当成权限隔离。

worktree 不会复制未跟踪或被忽略的 `.venv`、`node_modules`、`.env`、构建产物；它使用 Git 提交里的代码。可以先在外部环境安装依赖，使用解释器或检查脚本的绝对路径。默认模式也可以先创建会话并在保留的 worktree 中由人工准备环境，再恢复。Flora 不自动启动容器、安装工具链、服务或数据库。

## 在较大仓库中定位代码

Coding Agent 的文件工具还包括两个源码导航接口：

| 工具 | 用途与界限 |
| --- | --- |
| `search_code(query, path=".", glob="*", case_sensitive=True, max_matches=30, max_files=200)` | 在完整的有界文件中搜索字面文本，返回文件名、行号和完整文件哈希；每个文件最多 1 MiB，单次扫描预算 8 MiB，返回数据约束为 16 KiB |
| `read_lines(path, start_line=1, end_line=None)` | 一基、包含两端的行窗口；默认 60 行，最多 200 行或 16 KiB 源码，返回完整文件哈希和后续行号 |

模型先搜索，再读取相关函数和测试附近的行，用哈希保护的 `replace_text` 修改。大文件不需要整体进入模型上下文；完整文件哈希只证明读取时的版本，并不表示模型读过全部内容。窗口内容不能拿去覆盖整个文件。

搜索返回的 `truncated`、跳过计数和扫描统计需要一起阅读：超大文件、非文本、深度或扫描额度限制，都会让“没有匹配”不足以证明全文不存在目标。`glob` 匹配相对 worktree 根目录的路径。已有 `search_files` 保留通用行为，可能只检索文件前面的字节切片；源码导航优先使用 `search_code`。

状态文件固定会话使用的能力集合。重开已有会话沿用原能力集合；新会话启用源码导航工具，恢复不会静默换掉工具身份。

## 完成检查的含义

完成门槛是以下三项同时成立：

1. 本任务调用过固定测试命令，退出码为 0 且未超时。
2. 测试前后 Git 可见的文件状态相同，防止测试运行时修改代码却沿用旧证据。
3. 模型提交最终结果时，文件状态仍与测试完成时一致。

模型提前返回、测试失败、测试后再编辑，都会导致完成被拒绝；模型可以读取实际状态、修复并再次测试。`CodingResult.status == "completed"` 表示内核接受完成且最后一次本地核验仍有效。极少数并发外部修改发生在内核完成与导出之间时，包装层会报告 `verification_stale`。

指纹只覆盖基准提交与 Git 可见的补丁；不覆盖被忽略的文件、外部依赖、网络服务、宿主环境变量。没有任何测试能因此自动获得“任务正确”的证明。不要让其他编辑器或进程同时修改会话；会话锁只能阻止多个 Flora 实例同时打开同一个会话。

## Python API

```python network
from flora.coding import CodingAgent

with CodingAgent(
    repo="./my-project",
    session_dir="./flora-job-python",
    test_command=["python", "-B", "-m", "unittest", "discover", "-s", "tests"],
    model="YOUR_MODEL_ID",
    provider_options={"base_url": "https://your-provider.example/v1"},
    compiler_options={"max_output_tokens": 8192},
    budget_limits={
        "max_model_calls": 12,
        "max_tool_calls": 60,
        "max_output_tokens": 60000,
        "max_wall_seconds": 1800,
    },
) as agent:
    result = agent.run("修复空输入处理，并运行现有测试。")
    print(result.status)
    print(result.patch_path)
    print(result.verification["tests_current_and_passed"])
```

| 接口 | 含义 |
| --- | --- |
| `CodingAgent(...).run(task)` | 开始任务；拒绝覆盖未结束的活动任务 |
| `CodingAgent(...).resume()` | 恢复底层 Agent 的活动任务 |
| `CodingResult.status` | 编码工作流状态；保留内核状态或报告 `verification_stale` |
| `CodingResult.result` | 底层 `RunResult.to_dict()`，含结果、原因、预算等 |
| `CodingResult.worktree` / `patch_path` | 独立工作目录和补丁绝对路径 |
| `CodingResult.verification` | 当前指纹、测试命令、最近测试结果和适用范围 |
| `CodingResult.to_dict()` | 可序列化的完整结果 |
| `CodingWorkspace(...).coding_status()` | 无模型的本地检查 |
| `CodingWorkspace(...).patch()` | 返回完整补丁字符串 |
| `CodingWorkspace(...).run_tests()` | 人工或宿主重新执行保存的测试命令 |

Python 重新打开会话时，若原先设置过非默认 `test_timeout` 或 `allow_commands`，需要显式传入相同值。`repo` 和 `test_command` 可省略，提供时必须与保存值一致。使用 `with` 关闭底层会话和锁。

## 参数参考

| CLI 参数 | 说明 |
| --- | --- |
| `--repo PATH` | 创建时必填，干净且已有提交的 Git 仓库根目录 |
| `--session PATH` | 必填，位于目标仓库外的持久会话目录 |
| `--test COMMAND` | 创建时必填，固定验证命令，支持引号但不启动 shell |
| `--test-timeout SECONDS` | 单次检查时间上限，默认 120，最大 3600 |
| `--allow-commands` | 开启任意命令工具，默认关闭 |
| `--config FILE` | 同通用 Agent 的 provider/compiler/runtime/budget JSON 配置 |
| `--model` / `--base-url` / `--api-key-env` | 覆盖模型、地址和密钥环境变量名 |
| `--max-output-tokens` | 单次模型输出额度 |
| `--resume` / `--status` / `--diff` | 互斥操作；不能同时提供新任务 |
| `--json` / `--quiet` | 输出结构化结果／隐藏进度 |

不提供预算配置时，Coding Agent 的默认上限为 12 次模型调用、60 次工具调用、60,000 个输出 token 和 1,800 秒运行预算。传入的预算字典交给底层预算系统，未列出的项不会自动补齐 Coding Agent 默认值。测试进程还有独立的超时限制；模型 HTTP 超时由 provider 控制。

## 内部机制与会话文件

`coding/` 是应用层。它向现有 `Agent` 注册文件工具和三个编码工具，用已有 `completion_guard` 接入当前文件版本的测试证据。模型仍生成 Flora 程序；双重控制的候选行动与诊断选择、消费者合约综合、工具回执和恢复由原执行内核负责。应用层没有加入预知的正确补丁、隐藏测试答案或训练器。

会话目录包含 `coding.json`、`test-result.json`、`result.json`、`changes.patch`、`worktree/` 和底层 `agent/` 状态。任务未完成也尽量导出已取得的改动；如果进程被强制结束，结果文件可能尚未写入，仍可用 `--diff` 检查现存 worktree。

当前不支持子模块仓库，不允许会话内移动 HEAD；Git 输出和补丁各有 4 MiB 上限，补丁必须能无损表示为 UTF-8。`inspect_changes` 向模型返回至多 65,536 个字符，完整内容保存在导出的补丁里。单次测试保留至多 128 KiB stdout/stderr，输出中标注截断。很大的仓库或差异超限会明确报错。

结束使用时，先确认修改已经保存或应用。可用 `git -C ./my-project worktree list` 查看检出；不要直接删除还需要的会话。清理脏 worktree 需要 Git 的强制删除选项，Flora 不会自动替你执行这类丢弃修改的操作。

## 已验证的范围

公开仓库编码验收基于固定的 more-itertools 提交，包含 6 项植入回归和 2 项新 API 要求。8 项任务首次完整通过 3 项；每个失败任务最多一次显式恢复后，完整通过 4 项。6 项补丁通过外部检查，但其中有任务仍因模型输出或预算问题无法完成，不能按 6 项完整成功解释。

[完整验收报告](https://github.com/wongzbb/Flora/blob/coding-agent/reports/REPOSITORY_EVALUATION.md)包含任务设计、逐项失败原因、用量与限制。这是开发验收集，不是官方 benchmark，也没有证明双重控制或合约带来统计性能增益。
