# 安装与五分钟开始

这一章的目标是让你直接提交自然语言任务。你不需要先阅读内部语言、合约或研究章节。需要 Python 3.11+，以及一个可访问的模型服务；安装包不包含模型权重或 API 额度。

## 1. 安装发布包

Flora（芙洛拉）是本项目的名称，与其他同名软件独立。发行包名为 `flora-lang`，import 和 CLI 名为 `flora`；`pip install flora` 不是本项目的安装命令。

解压本次交付的 ZIP，进入含 `pyproject.toml` 的目录。使用其中的固定版本 wheel，可离线安装，核心运行时没有第三方依赖。为本项目单独创建虚拟环境，不要复用已安装其他同名 `flora` 模块的环境。

Linux / macOS：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --no-index dist/flora_lang-0.1.0-py3-none-any.whl
flora --version
```

Windows PowerShell：

```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --no-index dist/flora_lang-0.1.0-py3-none-any.whl
flora --version
```

如果本机不允许 PowerShell 激活脚本，直接使用 `.venv\Scripts\python.exe -m flora ...`；无需修改全局策略。所有 `flora` 命令都可以写成 `python -m flora`。

## 2. 配置模型

把占位符换成你的实际模型 ID 和凭据。默认 endpoint 为 `https://api.openai.com/v1`，默认凭据变量为 `OPENAI_API_KEY`：

```bash
export OPENAI_API_KEY="你的模型服务密钥"
flora setup --model YOUR_MODEL_ID
```

PowerShell 使用 `$env:OPENAI_API_KEY="你的模型服务密钥"`。密钥只放入环境变量或宿主的密钥管理系统；`setup` 保存模型名称、endpoint 和凭据变量名称，不保存密钥值。

其他 Chat Completions 兼容服务：

```bash
export MY_MODEL_KEY="你的兼容服务密钥"
flora setup --model YOUR_MODEL_ID \
  --base-url https://your-provider.example/v1 \
  --api-key-env MY_MODEL_KEY
```

`base-url` 通常到 `/v1` 为止，不要再追加 `/chat/completions`。网络代理、模型权限和服务商额度由你的服务配置决定。

## 3. 提交一个任务

不需要文件工具的任务：

```bash
flora ask "写一个检查 Python 项目 README 是否清楚的简短清单。"
```

查看现有项目，把路径换成实际存在的目录。内置工作目录工具依赖 POSIX 文件操作；以下命令适用于 Linux/macOS，Windows 请在 WSL 中运行，或用 Python 提供自己的文件工具：

```bash
flora ask "查看目录中的文件，告诉我这个项目如何安装和启动。" \
  --workspace ./my-project
```

这是实际模型任务，会产生模型费用或使用本地模型资源。文件工具能够列目录、搜索、读取、写入和编辑目录内的文本文件。没有 `--workspace` 时，不会自动向模型开放当前目录。

## 4. 连续对话

```bash
flora chat --workspace ./my-project --session ./my-agent-session
```

在提示符后直接输入任务。完成后可以继续提出后续要求，例如“现在根据刚才的结论更新 README”。新一轮会获得保留的已完成任务与结果；工具仍从当前真实文件状态读写，不会使用虚构的旧世界。

`/help` 显示交互命令，`/status` 查看会话与预算，`/resume` 继续尚未完成的一轮，`/quit` 退出。再次用同一组模型、工具与工作目录设置打开同一个 `--session`，即可读取持久会话。不要同时由多个进程打开一个会话目录。

## 5. 需要执行命令时

文件修改不需要开启命令工具。只有任务确实需要运行测试、脚本或构建时，显式加上：

```bash
flora ask "检查项目的测试说明，运行相关测试并解释失败原因。" \
  --workspace ./my-project --allow-commands
```

这会允许模型请求宿主命令执行。命令工具不使用 shell 解释命令字符串，但被执行的程序本身具有宿主进程权限；工作目录约束不构成操作系统沙箱。对于不可信项目或依赖，应在你配置好的容器或受限环境中运行。

## 结果、进度与脚本集成

`ask` 默认显示便于阅读的完成值，进度写往标准错误。`--quiet` 关闭进度，`--json` 输出包含状态、值、原因、预算与报告的 JSON 结果，适合脚本读取。输出 JSON 时仍要检查 `status`，不能只检查 `value` 是否非空。

```bash
flora ask "列出这个目录的入口文件。" \
  --workspace ./my-project --quiet --json
```

## 可选：离线检查安装

没有模型账户也可以运行以下命令：

```bash
flora demo calendar
flora demo pagination
```

这些示例使用预先编写的程序，经过实际解释器、调度器、工具与事件记录，但不调用模型。它们用于检查执行机制和安装状态，不代表真实模型已完成任务。

## 源码运行与项目文件

无需安装包时，在解压目录中运行：

```bash
PYTHONPATH=src python -m flora --help
```

Windows 可先设置 `$env:PYTHONPATH="src"`。修改源码后，可使用 `python -m pip install -e .` 安装开发版本；这需要环境中已具备构建 backend。发布环境建议安装固定 wheel。

```python
import flora
print(flora.__version__)
print(flora.__file__)
```

| 路径 | 用途 |
| --- | --- |
| `src/flora/` | Python API、CLI、标准工具和执行内核 |
| `dist/*.whl` | 可离线安装的发行包 |
| `examples/` | 用户示例与可检查的离线程序 |
| `configs/` | 低层运行参数示例 |
| `docs/`、`manual.html` | 手册源码与离线成品 |
| `schemas/` | 供内核开发者使用的程序格式 schema |
| `VALIDATION.md` | 本次实际验证范围和结果 |

包名为 `flora-lang`，import 和 CLI 名为 `flora`。测试源码按交付要求单独保管，不进入 ZIP。
