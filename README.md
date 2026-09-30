# Flora 操作手册

本分支维护 Flora 的说明文档。[通用终端 Agent 源码位于 general-agent 分支](https://github.com/wongzbb/Flora/tree/general-agent)，[内核基线位于 core 分支](https://github.com/wongzbb/Flora/tree/core)。

下载 [manual.html](manual.html)，使用浏览器打开即可阅读。手册为单文件，支持搜索、章节导航、代码复制、明暗主题和打印，无需联网。

[项目总览与 Logo](https://github.com/wongzbb/Flora/tree/overview)位于独立 overview 分支。

## 编辑与构建

```bash
git clone --branch docs --single-branch https://github.com/wongzbb/Flora.git Flora-docs
cd Flora-docs
python scripts/build_docs.py
```

- `docs/`：Markdown 章节和章节目录。
- `scripts/build_docs.py`：仅依赖 Python 标准库的生成脚本。
- `manual.html`：可下载的完整操作手册。

应用安装和代码示例在 general-agent 分支的源码目录中运行；此分支仅用于维护和阅读文档。

- [多 agent 协作、持续工作与可靠性](docs/31-reliability.md)
