# 验证报告

## 当前代码检查

- 401 项回归测试通过（13.024 秒），包括 16 项 Coding Agent 验收、7 项源码导航与兼容检查，以及执行内核、预算和恢复检查。
- wheel 构建与独立虚拟环境安装通过；安装后的 16 项 Coding Agent 和 7 项导航验收全部通过。
- Ruff 检查通过；35 段离线文档示例执行通过，16 段模型服务示例仅做语法检查。
- docs 分支手册含 24 章，HTML 内部链接、无外部资源和 JavaScript 语法检查通过。
- 核心 engine、language、checks 等实现未改动。改动限于 coding 应用层、其 CLI 和说明资料。

## 真实模型验收

在固定公开 more-itertools 仓库上构造的 8 项任务，首次完整通过 3 项；每个失败任务最多一次显式恢复后，完整通过 4 项。6 项的最终补丁通过外部检查，不能把这个数字当作 Agent 完成率。详见 [逐项结果、失败与用量](REPOSITORY_EVALUATION.md) 及 [机器可读指标](REPOSITORY_EVALUATION.json)。

此次真实运行表明，大文件定位已经改善，JSON/IR 生成、输出截断和预算消耗仍是主要可靠性问题。本轮没有执行正式 benchmark 或算法消融。

早先的小型 Coding Agent 任务与通用 API 接入检查，分别保留在 [Coding Agent 初始验收](CODING_VALIDATION.md) 和 [通用 API 验收](LIVE_VALIDATION.md)，不与本轮计数混合。

## 交付边界

代码在 coding-agent 分支，内核基线保留于 core，说明文档位于 docs，总览页面位于 overview。测试源码、凭据、轨迹数据库、开发环境和二进制安装包不进入这些分支。

总览 HTML 的唯一 ID、内部锚点、仓库链接格式、无外部资产、真实代码节选一致性与脚本语法检查通过；原创 SVG 角色已单独渲染检查。受预览浏览器本地 URL 策略限制，未完成整页桌面/手机视觉验收。

实际环境为 CPython 3.12，声明 Python 3.11+。未完成跨平台矩阵或生产压测；worktree 不是安全沙箱，局部合约和测试证据不证明任意任务正确。
