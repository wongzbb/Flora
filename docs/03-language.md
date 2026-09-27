# 编写第一个 IR 程序

本章供内部语言维护与研究实验使用；日常调用 Agent 无需手写本章格式。Flora 的语言是有版本的 JSON IR。它没有 Python `eval`、`exec`、导入、反射或隐式文件访问。JSON 是机器接口：生产环境中通常由模型编译器生成，开发者可以手工编写小程序来验证工具和运行语义。

## 最小程序

下面的程序不调用工具，直接返回一个普通对象。

```python
from flora.ir import parse_program
from flora.vm import new_machine, run_until_boundary

program = parse_program({
    "version": 1,
    "entry": "main",
    "blocks": {
        "main": {
            "params": [],
            "ops": [],
            "term": {
                "op": "return",
                "value": {"message": "hello", "source": "local"},
            },
        },
    },
})

boundary = run_until_boundary(new_machine(program))
assert boundary.kind == "return"
assert boundary.value == {"message": "hello", "source": "local"}
print(boundary.value)
```

一个程序必须包含 `version`、`entry` 和 `blocks`。每个基本块包含参数列表 `params`、纯指令列表 `ops` 和恰好一个终止指令 `term`。终止指令决定控制流，纯指令只产生内部值。

## 表达式与寄存器

普通 JSON 标量、列表和字典可以直接作为表达式。变量读取使用 `{"var": "name"}`。当数据本身恰好长得像变量表达式时，使用 `{"literal": value}` 取消解释。

```json
{
  "op": "add",
  "dest": "total",
  "args": [{"var": "base"}, 30]
}
```

这条纯指令将 `base + 30` 写入寄存器 `total`。寄存器名不是宿主 Python 变量。算术、容器和输出受大小及燃料限制；超限不能用来访问宿主资源。

输入只能通过入口块声明的参数进入。以下程序要求 `base`，并返回增加后的值：

```python
from flora.ir import parse_program
from flora.vm import new_machine, run_until_boundary

program = parse_program({
    "version": 1,
    "entry": "main",
    "blocks": {
        "main": {
            "params": ["base"],
            "ops": [{"op": "add", "dest": "next", "args": [{"var": "base"}, 30]}],
            "term": {"op": "return", "value": {"var": "next"}},
        },
    },
})
assert run_until_boundary(new_machine(program, {"base": 120})).value == 150
```

## 效果与显式续行

工具请求不是普通函数调用。`effect` 返回一个暂停边界；只有宿主运行时才有权调用对应工具。

```python
from flora.ir import parse_program
from flora.vm import new_machine, run_until_boundary, resume

program = parse_program({
    "version": 1,
    "entry": "main",
    "blocks": {
        "main": {
            "params": [],
            "ops": [],
            "term": {
                "op": "effect",
                "tool": "lookup",
                "args": {"id": "B"},
                "resume": "after_lookup",
                "bind": "reply",
                "capture": {},
            },
        },
        "after_lookup": {
            "params": ["reply"],
            "ops": [{"op": "get", "dest": "value", "args": [{"var": "reply"}, "value"]}],
            "term": {"op": "return", "value": {"var": "value"}},
        },
    },
}, allowed_tools={"lookup"})

first = run_until_boundary(new_machine(program))
assert first.kind == "effect"
assert first.request == {"tool": "lookup", "args": {"id": "B"}}

# 仅用于解释 VM 的受控本地示例；真实运行由 Effects 执行工具并提供结果。
continued = resume(first.machine, {"status": "returned", "value": {"start": 900}})
second = run_until_boundary(continued)
assert second.value == {"start": 900}
```

手工调用 `resume()` 是底层 VM API，不会写入真实事件记录。生产任务应交由运行时及效果执行器接入真实工具。不得将手工构造的 outcome 宣称为环境观察。

`reply` 是状态信封。成功内容位于 `reply.value`；工具异常位于 `reply.error`。真实程序应先读取 `status` 再处理结果，不能假设每次调用都成功。

## 捕获必须显式

跨工具边界需要保留的变量写入 `capture`：

```json
{
  "op": "effect",
  "tool": "lookup",
  "args": {"id": {"var": "item_id"}},
  "resume": "after",
  "bind": "reply",
  "capture": {"offset": {"var": "offset"}}
}
```

`after` 的 `params` 必须恰好匹配 `reply` 与 `offset`。不显式捕获，就不能在新块里读取旧寄存器。这使暂停状态可以检查和保存，避免依赖隐形闭包。

## 分支、循环与内部调用

`branch` 根据纯布尔条件选择块；`jump` 带着新的参数进入块。循环由回跳构成，并受每次纯运行的 fuel 限制。`call` 是内部程序调用，它用显式调用栈返回 `resume`，不产生真实工具事件。

`alternative` 在内部产生多个机器状态。它本身不复制环境，也不调用工具。运行时限制活跃候选数；候选达到效果边界之后，仍然只能提交一个真实动作。

## 从手写到模型生成

手写 IR 适用于最小集成、故障定位和确定性工具流程。模型编译器接收相同的语言定义并输出相同的 JSON 结构。无论作者是人还是模型，都经过同一 `parse_program`、工具名单与运行时边界检查，不存在“模型代码可跳过校验”的路径。
