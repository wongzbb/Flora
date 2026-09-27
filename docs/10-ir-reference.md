# IR 指令与表达式全集

本章描述 IR version 1。JSON schema 文件可帮助编辑器提供提示；最终授权与语义校验以 `ir.parse_program()` 和运行时检查为准。通过 schema 不代表程序能完成任务。

## 静态结构限制

| 项 | 限制 |
| --- | --- |
| 程序版本 | 仅整数 `1` |
| 基本块 | 1–512 个 |
| 全程序纯指令 | 不超过 10,000 条 |
| 块参数 | 不超过 128 个，不能重复 |
| 块与寄存器标识 | ASCII 字母或下划线开头，之后为字母、数字、下划线，最多 128 字符 |
| 寄存器定义 | 块内 SSA，同一个目标不能覆盖参数或先前目标 |
| alternative 分支 | 1–16 个不同块；运行时另有候选上限 |
| 内部调用栈 | 最多 128 层 |

值层另有统一硬界限：整数 4096 bit，嵌套深度 64，节点数 200,000，单值规范 JSON 编码 16 MiB；VM 的默认 1 MiB 限额更严格。

所有块都检查，包括不可达块。未知字段、未知指令、未定义变量、无效跳转目标和续行参数不匹配均拒绝。`allowed_tools=None` 只跳过静态工具名单检查，不为程序赋予调用任何宿主函数的能力。

## 表达式规则

- 标量 JSON 直接表示对应值。
- 列表和普通字典递归求值。
- `{"var":"x"}` 引用当前块已定义的寄存器。
- `{"literal": value}` 返回未解释的 JSON 数据。
- 对象自己的 key 包含 `var` 或 `literal` 时，必须符合对应表达式格式；要把这些 key 当业务数据，使用 literal 转义。

JSON 值不允许 NaN、Infinity、非字符串字典 key 或任意宿主对象。整数、深度、节点数和序列化大小受值层及 VM 限制；这些限制是资源边界，不是业务范围约束。

## 数据访问与容器

所有纯指令使用统一形状 `{"op": name, "dest": register, "args": [...]}`。表中的参数个数对应 `args` 长度。

| 指令 | 参数数 | 行为 |
| --- | --- | --- |
| `const` | 1 | 返回传入表达式的值 |
| `get` | 2 | 读取对象 key 或列表、字符串索引；缺失产生具体故障 |
| `get_default` | 3 | 缺失时返回第三个参数 |
| `has` | 2 | 判断对象 key 或序列索引是否存在 |
| `set` | 3 | 返回设置指定对象 key 或已有列表位置后的副本 |
| `delete` | 2 | 返回删除已有 key 或列表位置后的副本 |
| `keys` | 1 | 对象 key 的排序列表 |
| `values` | 1 | 按排序 key 顺序排列的对象值 |
| `length` | 1 | 对象、列表或字符串长度 |
| `append` | 2 | 将一个值追加到列表副本 |
| `extend` | 2 | 拼接两个列表 |
| `slice` | 2–4 | 序列、start、可选 stop、可选 step；step 不得为零 |
| `contains` | 2 | 列表值、字典 key 或字符串子串成员检查 |
| `concat` | 2–128 | 拼接同类型的字符串或列表 |

对象 key 必须为字符串；序列索引必须为整数。VM 的序列访问允许负索引，而合约谓词路径只接受非负索引。`set` 不会扩大列表来填空洞；对象可以添加新 key。`delete` 不接受原本不存在的 key。

所有容器修改均为纯值副本，不修改原始工具返回、memory 或其他候选的数据。

## 算术、比较与逻辑

| 指令 | 参数数 | 行为 |
| --- | --- | --- |
| `add`、`sub`、`mul` | 2 | 两个数的加、减、乘 |
| `div`、`mod` | 2 | 除法、取模；除数不得为零 |
| `eq`、`ne` | 2 | 结构相等 / 不等；布尔值与数字区分 |
| `lt`、`le`、`gt`、`ge` | 2 | 两个数或两个字符串的顺序比较 |
| `and`、`or` | 2–128 | 布尔合取、析取 |
| `not` | 1 | 布尔否定 |
| `assert` | 1–2 | 布尔条件，及可选失败说明；为假产生具体故障 |

算术不接受布尔值，也不把字符串隐式转数。字符串拼接使用 `concat`。布尔操作要求真正的布尔值，不能依靠空列表、零或非空字符串的 Python truthiness。

纯指令的参数先求值；`and` 与 `or` 不是通用惰性控制流。需要避免某个可能失败的访问时，先 `has`，再用终止指令 `branch` 分支。

VM 算术相等把数值相等的整数和浮点数视为相等，而合同和请求的规范 JSON 比较保留具体 JSON 编码区别。不要依赖 `1` 与 `1.0` 的混用来表示工具接口同一性。

## 格式、类型与观察读取

| 指令 | 参数数 | 行为 |
| --- | --- | --- |
| `to_string` | 1 | 字符串保持原值，其他值转为规范 JSON |
| `parse_json` | 1 | 解析严格 JSON 字符串；拒绝重复 key 与非有限数 |
| `type` | 1 | 返回 `null / boolean / integer / number / string / array / object` |
| `read_receipt` | 1 | 读取宿主提供的真实回执列表的指定索引 |
| `read_memory` | 0–2 | 无参数读取全部 memory；一个 key 读取其值；第二参数为缺失默认值 |

回执索引不可用、回执未结算、memory key 不存在且没有默认值时，返回 `unknown` 边界。它不会访问外部系统去补数据，也不会拿另一个历史位置代替。

## 终止指令完整格式

```json
{"op":"return","value":{"var":"answer"}}
```

根程序 return 产生最终返回；内部 call 的 return 先恢复调用者续行。

```json
{"op":"jump","target":"next","args":{"x":{"var":"x"}}}
```

`args` 的 key 集合必须与目标块参数完全一致。

```json
{"op":"branch","condition":{"var":"ok"},"yes":"accepted","no":"rejected","args":{"x":{"var":"x"}}}
```

两个目标块必须有相同的参数集合，并与 args 一致。条件必须为布尔值。

```json
{"op":"call","target":"worker","args":{"x":{"var":"x"}},"resume":"after","bind":"result","capture":{"saved":{"var":"x"}}}
```

`after` 的参数必须为 `result` 和 `saved`。调用不会产生工具事件。

```json
{"op":"effect","tool":"lookup","args":{"id":{"var":"id"}},"resume":"after","bind":"reply","capture":{}}
```

工具名是静态字符串，args 求值后必须是对象。该指令只产生边界，真实调用由效果执行器负责。

```json
{"op":"alternative","branches":["method_one","method_two"],"args":{"x":{"var":"x"}},"resume":"join","bind":"choice","capture":{}}
```

两个分支都必须接受 args。每个内部实现最终 return 时进入 join。解释器返回若干隔离机器状态，运行时对它们统一进行候选数和真实轨迹管理。

```json
{"op":"replan","reason":"需要理解刚取得的实际内容","state":{"reply":{"var":"reply"}}}
```

`reason` 与 `state` 都是表达式。reason 求值为非空字符串，state 必须求值为 JSON 对象；replan 表示请求模型继续推理，不表示任务完成，也不自行调用模型或工具。运行时选择真实的重新规划后，将这份实际状态放入 `memory["__openharness_continuation__"]`，携带当前轨迹锚点和剩余预算重新编译。选中的正常候选会以新编译结果替换当前程序及其调用栈；任何需要保留的局部状态必须显式放进 state，不能依赖旧寄存器自动继承。原任务和实际事件轨迹仍保留。纯 VM、重放或假想检查运行到此处只能观察重新规划边界，不能代替下一次模型推理。

## Machine 与 Boundary

`new_machine(program, inputs=None, mode="observed")` 建立机器状态。`inputs` key 必须准确匹配入口参数。`mode` 只能为 observed、hypothetical 或 replay；模式不会在 `resume()` 时自动升级为 observed。

`run_until_boundary(machine, receipts=(), memory=None, fuel=50000, max_value_bytes=1048576)` 返回新状态，不修改传入机器。`Boundary.kind` 可以为 effect、return、fault、unknown、alternative、replan；按种类读取 `request`、`value`、`details` 或 `alternatives`。

`resume(machine, outcome)` 仅接受暂停于效果边界的机器。成功信封必须恰好包含 status 和 value；普通异常信封包含 status 及 error 的 type、message。`interrupted_unknown` 使机器进入未知状态，不通过用户代码隐藏重试。

`Machine.to_dict()` 与 `Machine.from_dict()` 用于完整机器序列化。恢复时验证块、寄存器、指令位置、续行帧和暂停请求是否一致，不能随便修改 pending 请求后继续执行。
