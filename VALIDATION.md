# Flora 0.1.0 验证报告

日期：2026-09-27。本报告记录真实模型接入修复后的 Flora 0.1.0 验证结果。

本次仅统一发布版本号。以下 376 项测试与真实 API 验收沿用此前 0.2.2 的记录；全部实现文件已逐项核对，仅版本常量和 HTTP User-Agent 改号。另行验证了新 wheel 的安装、CLI 版本和发布清单。

## 验证结果

- 自动化测试 **376 / 376 通过**，0 失败、0 错误；完整运行 10.326 秒。
- 35 段离线 Python 文档示例、20 条离线 CLI 检查通过；15 段模型服务示例仅做语法检查，没有执行远程模型请求。
- Ruff 检查通过；纯 Python wheel 构建成功。
- 已通过：发布包解压、SHA-256 清单检查、wheel 与源码逐项比对、新虚拟环境离线安装，以及 CLI/Python API 的真实本地 HTTP 协议验收。
- 更名回归包含由旧 OpenHarness 0.2.0 在独立进程生成的持久数据；验证预算/历史延续、已记录作用不重复执行、配置变更拒绝，以及新旧状态目录保护。旧序列化标识原样保留。
- 手册 24 章、95 个可复制代码块、247 个唯一 ID、50 条有效内部链接；无外部资源，JavaScript 语法通过。本轮未做浏览器截图验收。
- ZIP 与 wheel 不包含测试源码、测试日志、故障注入程序、开发环境、会话数据或凭据。

环境：Linux x86_64，CPython 3.12.14。核心声明 Python 3.11+，本次实际验证 Python 3.12；未运行完整跨平台矩阵。

## 更名范围与兼容边界

产品名 Flora（芙洛拉），分发包 `flora-lang`，Python 包和 CLI `flora`，新环境变量 `FLORA_*`，默认配置目录 `~/.config/flora`。旧配置和环境变量通过文档中的明确步骤迁移，不隐式回退。

旧事件链、检查点、复用库和会话的 v1 格式标识保留。内置 provider/工作目录工具的精确旧配置可恢复；配置约束仍然有效。自定义 provider/tool 模块改名、工具描述变化和 opaque 对象跨进程恢复不被自动推断为安全迁移。

## 测试分布

| 测试组 | 数量 | 结果 |
|---|---:|---|
| `test_adapters` | 16 | PASS |
| `test_adversarial` | 18 | PASS |
| `test_agent_api` | 12 | PASS |
| `test_binding` | 16 | PASS |
| `test_cli_adapters` | 5 | PASS |
| `test_compiler` | 24 | PASS |
| `test_contracts` | 25 | PASS |
| `test_deployment_edges` | 4 | PASS |
| `test_diagnostics` | 12 | PASS |
| `test_direct_cli` | 24 | PASS |
| `test_direct_workflow` | 10 | PASS |
| `test_identifiers` | 4 | PASS |
| `test_ir` | 9 | PASS |
| `test_live_pipeline` | 13 | PASS |
| `test_opaque` | 21 | PASS |
| `test_providers` | 17 | PASS |
| `test_real_model_regressions` | 8 | PASS |
| `test_rename_compatibility` | 3 | PASS |
| `test_replan` | 12 | PASS |
| `test_replay` | 11 | PASS |
| `test_resource_limits` | 13 | PASS |
| `test_reuse` | 20 | PASS |
| `test_runtime_reuse` | 6 | PASS |
| `test_runtime_semantics` | 19 | PASS |
| `test_scheduler` | 9 | PASS |
| `test_sessions` | 12 | PASS |
| `test_user_entry_core` | 4 | PASS |
| `test_vm` | 14 | PASS |
| `test_workspace` | 15 | PASS |

## 尚未验证的范围

真实 DeepSeek API 验收与修复前失败记录见 LIVE_VALIDATION.md。离线 HTTP 协议测试仍是本地 fixture，不能冒充模型实验。本轮没有跑 AppWorld/StuLife 官方完整任务集，没有测量广泛任务分布的成功率，也没有完成生产负载或长时间运行验收。测试通过不构成优于 JAZ 的研究结论。

宿主工具是可信代码；工作目录工具不是命令执行的操作系统沙箱。文件锁仅协调遵守协议的进程。未知外部作用仍需实际证据解决，不自动重试。合约和经验复用提供局部证据，不证明任意任务正确或跨任务泛化。

## 版本指纹

- 核心 Python 源码集合 SHA-256：`04b4a2b9a45a8a0deabc7d558e0c204eaf3b37a4ab050f49695cd62d2d346d0d`
- 外部测试源码集合 SHA-256：`87dcfdfe8a18b3e6f712519cfc55afe17a95188bc818e8b554dba120521d026a`
- 逐文件 SHA-256 见 `MANIFEST.sha256`。

测试源码按要求独立保管，不进入交付 ZIP。发布包内仅含 Flora 0.1.0 wheel。
