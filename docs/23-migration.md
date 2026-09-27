# 从 OpenHarness 迁移

Flora 0.1.0 延续此前 OpenHarness 0.2.0 的实现，包含后续真实 API 修复；当前按首发版本重新编号。名称、安装入口和用户配置切换为 Flora；双重控制、可综合合约、真实工具调用与共享预算的执行约束继续保留。本项目与其他名为 Flora 的软件独立。

## 安装到专用环境

请为本项目单独创建虚拟环境，再安装交付 ZIP 中的 `dist/flora_lang-0.1.0-py3-none-any.whl`，具体命令见前面的安装章节。不要在已安装其他同名 `flora` 模块的环境中直接覆盖安装；`pip install flora` 不是本项目的安装命令。

本次交付使用固定 wheel，不要求从公共包索引下载一个名字相似的项目。新入口为 `flora` 或 `python -m flora`。用 `python -m flora --version` 确认当前解释器加载的是 0.1.0。

## 代码、命令与配置的对应关系

| OpenHarness 0.2.0 | Flora 0.1.0 |
| --- | --- |
| 发行包 `openharness-lang` | 发行包 `flora-lang` |
| `from openharness import Agent` | `from flora import Agent` |
| `openharness ask`、`openharness chat` | `flora ask`、`flora chat` |
| `python -m openharness` | `python -m flora` |
| `OPENHARNESS_MODEL` | `FLORA_MODEL` |
| `OPENHARNESS_BASE_URL` | `FLORA_BASE_URL` |
| `OPENHARNESS_API_KEY_ENV` | `FLORA_API_KEY_ENV` |
| `OPENHARNESS_CONFIG_HOME` | `FLORA_CONFIG_HOME` |
| `~/.config/openharness/settings.json` | `~/.config/flora/settings.json` |

更新应用源码中的 import、启动脚本中的命令和工具工厂路径。例如 `openharness.examples:pagination_tools` 改为 `flora.examples:pagination_tools`。本版本没有安装旧名 CLI 或 `openharness` 包的兼容别名。异常基类改为 `flora.errors.FloraError`；为迁移旧异常处理代码，`flora.errors.OpenHarnessError` 保留为同一类的别名，不需要改写持久数据。

`FLORA_API_KEY_ENV` 保存凭据变量的名字，不保存密钥值。服务商的真实凭据变量（例如 `OPENAI_API_KEY` 或你自己的 `MY_MODEL_KEY`）不必因项目更名而改名。原来的 `OPENHARNESS_*` 设置变量不会自动作为新变量读取。

## 迁移设置

推荐重新运行 setup，填入原来允许使用的模型与服务地址：

```bash
flora setup --model YOUR_MODEL_ID \
  --base-url https://your-provider.example/v1 \
  --api-key-env MY_MODEL_KEY
```

如果原服务无需认证，使用 `--no-api-key`。`setup` 不会自动请求模型，也不会复制密钥。新配置目录默认是 `~/.config/flora/`，受 `XDG_CONFIG_HOME` 和 `FLORA_CONFIG_HOME` 影响，详细优先级见模型配置章节。

另一种方式是自己查看旧 `settings.json`，将其中 `model`、`base_url`、`api_key_env` 三个非密钥设置复制到新路径；保留 `api_key_env: null` 的原意。不要把环境变量中的密钥写进 JSON，也不要把整个旧配置目录未经检查地搬到新位置。Flora 不会自动扫描或迁移旧目录。

## 恢复旧会话的条件

升级前停止使用旧版本的会话进程，保留原会话目录及业务环境的适用备份。可以先用新入口只读检查原轨迹，例如：

```bash
flora inspect /path/to/original-trace.sqlite3
```

这里是轨迹路径占位符，请使用原会话中的实际 SQLite 文件；不要创建空文件来代替它。检查哈希链成功说明记录结构可读，不代表外部系统已恢复，也不代表所有自定义工具都兼容。

恢复旧持久会话仍要求相同的实际工作目录、工具名称与描述、模型和 endpoint、instructions、运行时配置及预算限额。保留原累计预算，不因更名清零。会话路径通过 `--session` 或 `session_dir` 显式传入，不必仅因更名就移动目录。

本版本对原样使用的随包内置 provider 与 workspace 的名称变化提供兼容处理，在比较时额外接受对应的完整旧指纹，保留会话原有指纹和历史记录。自定义 provider 子类不会被当作内置类；内置 workspace 工具也须保持原处理函数及完整工具定义。兼容处理不等于任意自定义 provider、工具工厂、函数描述或适配器改名后都能继续原会话。自定义实现仍须连接原来的业务系统，并通过原会话绑定校验。发生绑定不匹配时，应先核对原配置和工具语义，不要修改 session JSON、伪造指纹或重新写入旧哈希来绕过检查。需要变更模型、能力或预算时，应开始新会话。

未知副作用不会因升级而变成成功或失败。旧会话若停在 `pending` 或 `interrupted_unknown`，必须先通过原业务允许的日志、请求 ID 或查询工具核实；必要时使用文档中的 `resolve` 登记已确认结果，再恢复原轮次。不要复制一份会话后同时继续两份，也不要用重新提交同一任务代替核实。

## 保留的内部格式标识

以下字符串是既有数据协议的一部分，有意保留：

| 内部标识 | 含义 |
| --- | --- |
| `openharness-session-v1` | 持久会话格式 |
| `openharness-trace-v1` | 轨迹导出格式 |
| `openharness-checkpoint-v1` | 执行检查点格式 |
| `openharness-reuse-v1` | 程序复用存储格式 |
| `__openharness_opaque__` | 进程内不透明对象的保留编码 key |
| `__openharness_continuation__` | 重新规划边界保存状态的保留 key |

它们不是当前安装命令或 Python 模块名。不要对已有 SQLite、JSON 轨迹、检查点或内部程序做全局 `openharness` → `flora` 替换；这会改变原始数据，并可能破坏哈希链和恢复语义。无需为了让文件里“没有旧名字”而重写历史。

内置工作目录文件工具同时保护 `.flora` 和旧 `.openharness`，也会保护高层 Agent 注入的目录内会话状态路径。该保护仍限于文件工具；自行执行的宿主命令并不因此获得操作系统隔离。
