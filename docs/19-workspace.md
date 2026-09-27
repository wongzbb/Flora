# 处理工作目录中的文件

`workspace` 提供已经写好的文件工具，适合阅读项目、整理文档、修改代码等本地任务。只在你主动指定工作目录时注册；普通业务函数任务与 benchmark 接入不会默认增加文件或终端权限。

## 从一个现有目录开始

```bash
flora ask "读取 README.md，检查错别字与启动说明，修改后给出摘要。" \
  --workspace ./my-project
```

Python 等价入口：

```python network
from flora import Agent

with Agent(workspace="./my-project") as agent:
    print(agent.ask("读取 README.md，检查错别字，修改后说明改动。"))
```

路径必须指向已存在的目录。工具只处理 UTF-8 普通文件，不会自动创建缺失的父目录。当前实现依赖 POSIX 的文件描述符相对操作与 no-follow 语义，适用于 Linux/macOS 等支持这些接口的系统；Windows 使用 WSL 或提供自己的业务文件工具。核心 Python API 和普通函数绑定不因这一工具包限制而要求 POSIX。

## 随包工具

| 工具 | 模型可以请求的操作 |
| --- | --- |
| `list_files` | 列出目录项，按需有界递归 |
| `read_file` | 读取 UTF-8 内容和完整文件 SHA-256 |
| `search_files` | 按字面文本搜索文件，返回一基行号与截断信息 |
| `write_file` | 创建新文件，或以读取到的旧 hash 为前置条件替换全文 |
| `replace_text` | 以旧 hash 和准确匹配次数为前提替换字面文本 |
| `run_command` | 可选；执行 argv 列表中的可执行程序 |

文件工具描述会教模型先读再写。普通用户无需手工计算 hash；需要了解的是：如果文件在读取后已经改变，修改会失败，agent 应当重读当前内容再判断。

默认可编辑文件上限 1 MiB，每次读取内容默认最多 64 KiB；用返回的 `next_offset` 继续读取后续内容，直到 `has_more=false`。完整文件不超过 1 MiB 时，即使只返回一页内容，`sha256` 仍标识整份文件的版本；它不意味着 agent 已读过全部内容。超过文件限额时仍可分页读取，但完整 hash 为 null，编辑会被拒绝，需宿主明确提高限额。`partial_sha256` 只标识片段，不能用于全文覆盖。

目录和搜索结果以及命令保留输出上限默认为 64 KiB。遍历和搜索会报告遗漏、跳过与截断，不能把未列出的文件当作不存在。

## 创建与修改语义

创建新文件使用独占创建，目标已存在时失败，不会因“创建”操作覆盖旧文件。全文替换要求 `expected_sha256` 等于本次读取到的完整文件 hash。局部替换还要求匹配次数等于 `expected_occurrences`，默认只替换一个准确匹配。

发布写入采用同目录临时文件与原子替换。工具包的写者之间通过根目录 advisory lock 协调，但外部编辑器可能不遵守这个锁；因此它不是跨所有程序的事务数据库，也不能承诺没有任何外部编辑竞争。模型应在冲突时读取新状态，不应猜一个旧 hash 强行继续。

## 路径和访问范围

工具路径使用相对工作目录的 POSIX 形式，例如 `src/main.py`。拒绝绝对路径、`..` 父路径、反斜杠路径、符号链接组件和非普通文件。默认保护 `.git`、`.flora`、旧版 `.openharness` 以及 Agent 注入的目录内会话状态路径。

选择目录意味着将该目录中的可访问业务文件交给相应工具；普通文件例如 `.env` 不会因为名称敏感就自动隐藏。选择最小必要目录，或在你准备好的项目副本中运行。文件路径检查只约束这组文件工具，不限制你另外注册的 Python 函数。

## 运行测试或构建

```bash
flora ask "阅读项目的测试说明，运行相关测试，报告失败和实际输出。" \
  --workspace ./my-project --allow-commands
```

```python network
from flora import Agent

with Agent(workspace="./my-project", allow_commands=True) as agent:
    result = agent.run("检查项目如何运行测试，执行相关测试并解释结果。")
    print(result.status, result.value)
```

`run_command` 接收 argv 列表，不默认使用 shell，不展开管道、重定向、通配符或变量。被调用的程序仍可能自行启动 shell 或任意其他程序。默认命令最长等待 30 秒，可在工具包构造时调整；工具调用不得超过配置的最大等待时间。

结果包含 `returncode`、`timed_out`、stdout、stderr、真实读到的输出字节数、各流截断标记和 `output_complete`。输出超限后仍继续排空流，但只保留额度内内容。超时会终止启动的进程组；这不能撤销该命令已经写入的文件或已提交的网络请求。

命令工具不是操作系统沙箱。子进程继承用户权限、环境与网络能力，可能访问工作目录之外的文件，也可能绕过文件工具对会话目录的保护。要隔离执行，请在宿主层使用适用的容器、用户权限和网络配置。CLI 没有虚构一个未实现的逐命令审批模式。

## 自定义文件工具限额

高层 `workspace=` 使用默认工具配置。确实需要调整时，可自己构造工具包并交给 Agent：

```python
import tempfile
from pathlib import Path
from flora import make_registry
from flora.workspace import WorkspaceTools

with tempfile.TemporaryDirectory() as directory:
    Path(directory, "README.md").write_text("hello\n", encoding="utf-8")
    pack = WorkspaceTools(
        directory,
        max_file_bytes=2 * 1024 * 1024,
        max_output_bytes=128 * 1024,
        command_timeout=60,
    )
    tools = make_registry(pack.specs())
    observed = tools.call({"tool": "read_file", "args": {"path": "README.md"}})
    assert observed["content"] == "hello\n"
    assert observed["sha256"] is not None
```

应用中再使用 `Agent(tools=tools, ...)` 即可。不要同时传入同名文件工具和 `workspace=`，否则会出现重复能力名称。手工构造工具包时，持久会话目录若位于工作目录内部，应通过 `protected_paths` 明确保护；高层 `workspace=` 路径会自动处理这一步。

## 用于公平研究时

与 JAZ 在同一个环境比较时，只传入该环境原本公开的工具或 AgentEnvBridge，并保持任务、模型、预算与反馈协议一致。不要因新增的便捷 API 而额外打开 workspace 或命令能力。文件工具包用于用户主动选择的本地项目场景，不是增强 benchmark 权限的默认步骤。
