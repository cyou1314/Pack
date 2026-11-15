# LangGraph 拆解笔记（langchain-ai/langgraph）

> 审计方式：2026-11-15 浅克隆到 `~/workspace/learn-inspect/langgraph`（版本 1.2.22，HEAD `08a2016`），纯静态读码，未运行、未填任何凭证。路径均相对仓库根；核心库在 `libs/langgraph/langgraph/`，检查点接口在 `libs/checkpoint/`，SQLite 实现在 `libs/checkpoint-sqlite/`。
> 定性一句话：LangGraph 是把「图执行 + 检查点持久化 + 中断/恢复」做成形式化底盘的**运行时**——它仓库自己的 AGENTS.md 明说正在从 SDK 收敛为底层 runtime，上层 agent 框架另有其人。对 Pack 的价值不在框架本身，在它把状态机纪律做绝的那几件小事。

## 1. 状态 / 节点 / 边模型（StateGraph）

出处：`libs/langgraph/langgraph/graph/state.py`（1982 行）。

- **状态即通道表**：`StateGraph(State)` 的 State 是一个 TypedDict/Pydantic schema，**每个字段就是一条 channel**。节点函数收到完整状态、返回**局部更新 dict**，运行时按每条 channel 的 reducer 合并（默认覆盖；`Annotated[list, operator.add]` 这类声明改成追加/累加）。状态合并语义是声明式的，不是节点自己读写全局变量。
- **节点**：`add_node(name, fn)`（state.py L376 起多个重载），fn 可以是普通函数、协程、Runnable，或一个已编译的子图（见 §5）。START / END 是两个保留伪节点。
- **边**：`add_edge(a, b)`（L928）是无条件跳转；`add_sequence` 是语法糖。
- **条件边**：`add_conditional_edges(source, path, path_map)`（L982）——节点跑完后调 `path(state)` 得一个 Hashable（或一组），经 `path_map` 映射到目标节点名；返回 END 即停。**路由函数只读状态、不改状态**，与节点职责分离；同一 source 可挂多个命名 branch。
- **执行模型是 Pregel 式 BSP 超步**（`libs/langgraph/langgraph/pregel/`，main.py 4585 行）：每一超步内，所有「输入通道出现新版本」的节点并行执行，写出统一收集、合并、落检查点，再进下一超步。节点是否该跑由检查点里的 `versions_seen`（每个节点已消费到各 channel 的哪个版本）决定——这是它恢复时不重跑的根（见 §2）。
- `compile(checkpointer=..., interrupt_before/after=...)`（L1177）把 builder 冻成 `CompiledStateGraph`（L1404），编译后图结构不可变。

对照 Pack：Pack 的状态是任务一行的 status 字段 + 人工定义的迁移，路由是 watcher/调度员的判断逻辑。LangGraph 的「路由函数只读状态」和「合并语义声明化」是它形式化的核心，但其超步并行是进程内毫秒级语义，与 Pack 跨机器、分钟级租约的执行体不是一个尺度（见「不借的理由」）。

## 2. Checkpointer：thread、快照链、崩溃恢复

出处：`libs/checkpoint/langgraph/checkpoint/base/__init__.py`（889 行，`BaseCheckpointSaver`）。

- **接口四件套**：`get_tuple / list / put / put_writes`（外加 `delete_thread / copy_thread / prune`，L344/374/398）。任何存储只要实现这套接口就能当检查点后端（官方有 memory / SQLite / Postgres 三家）。
- **thread 概念**：调用时在 config 里给 `configurable.thread_id`，它是检查点的主键维度——一条 thread = 一条可无限续跑的执行线；同一 thread 反复 invoke 就是在同一状态上继续。子图另用 `checkpoint_ns` 命名空间在同一 thread 内分层（见 §5）。
- **检查点结构**（`Checkpoint` TypedDict，L93 起）：`id`（uuid6，单调递增可排序）、`channel_values`（全量状态快照）、`channel_versions`、`versions_seen`、`updated_channels`；元数据 `CheckpointMetadata` 记 `source`（input/loop/update/fork）、`step` 步号、父检查点映射。**每个超步落一个完整快照，父子成链**——所以能 list 出整条历史、能 fork、能 time-travel 回任意步。
- **写出先于提交**：节点执行中的写出经 `put_writes` **边产生边落盘**（`pregel/_loop.py` L475 起：写出先存 `checkpoint_pending_writes` 并即时调 saver 的 put_writes，然后才轮到超步末尾的 `put` 落正式检查点）。粒度纪律：**写出级 exactly-once、节点执行级 at-least-once**。
- **崩溃恢复路径**：同 thread_id 再次 invoke（无新输入）→ 取最新检查点 → 先把未提交的 pending writes 应用到通道 → 按 `versions_seen` 算哪些节点还没消费最新输入 → 只跑那些。已完成并落盘的节点不会重跑；死在半路的节点整节点重跑（它未落盘的写出视为没发生）。
- **持久化强度可调**：`Durability = Literal["sync", "async", "exit"]`（`types.py` L99）——sync 是下一步开始前必须落盘，async 是后台落盘（快但崩溃可能丢最后一步），exit 是只在结束时落。把「落盘多勤」做成显式旋钮而不是写死。

## 3. interrupt：人工介入（暂停 / 等输入 / 恢复）

出处：`libs/langgraph/langgraph/types.py`——`Interrupt` 类 L594、`interrupt()` 函数 L913、`Command` 类 L843。

- **带内异常机制**：节点里调 `interrupt(value)`，首次调用抛 `GraphInterrupt` 异常，整图暂停；value 随异常带出给客户端（流里是 `__interrupt__` 事件）。**前提条件是必须配 checkpointer**——暂停点靠检查点落盘存活，进程死了也能等。
- **恢复**：客户端用同一 thread_id 发 `Command(resume=value)`，图从检查点续跑。关键语义（函数 docstring 明写）：**被中断的节点从头重跑**，再次执行到 `interrupt()` 时不再抛异常、直接返回 resume 值；一个节点里多个 interrupt 按出现顺序与 resume 值一一匹配（resume map 按 task 作用域隔离，`pregel/_loop.py` L964 起处理 RESUME 通道的重放与去重）。
- **防重复执行的边界要看清**：不重跑的只是**其他已完成节点**（其通道更新已进检查点）；被中断节点本身整段重跑，所以官方纪律是——**副作用要么放在 interrupt 之后，要么做成幂等**。interrupt 解决的是「暂停与续跑的记账」，不替你解决「重跑的副作用」。
- 另有静态版：`compile(interrupt_before=["node"], interrupt_after=[...])` 在指定节点前后无条件暂停，不需要节点内代码配合。

对照 Pack：Pack 的批准门是**带外**的（执行体发 approval_request、调度员/群主在房间里决议），执行体不是一个可重放的函数，批准等待期间它只是阻塞轮询——这与 Pack 的执行体形态（外部进程 + 真实账号会话）是匹配的。LangGraph 的带内 interrupt 借不了实现，但它「恢复 = 重跑 + 幂等纪律」这条教训对 Pack 同样成立（见可借清单第 4 条）。

## 4. 重试与错误处理

出处：`RetryPolicy`（`types.py` L428）、`default_retry_on`（`libs/langgraph/langgraph/_internal/_retry.py`）、执行在 `pregel/_retry.py`（874 行，含较新的 TimeoutPolicy 协作式超时）。

- **策略形状**：`RetryPolicy(initial_interval=0.5s, backoff_factor=2.0, max_interval=128s, max_attempts=3, jitter=True, retry_on=...)`。可挂在单个节点上，也可作全图默认；节点级覆盖默认。
- **按错误类型决定重试与否**：默认谓词 `default_retry_on` 的分类表值得抄：连接错误重试、HTTP 5xx 重试、4xx 不重试、`ValueError/TypeError/ArithmeticError` 等逻辑错误不重试（重试也不会好）。**「先分类、再决定重试」**而不是一律重试 N 次。
- 重试发生在节点（task）粒度，在同一超步内，不产生新检查点；耗尽后异常向上抛、图失败，但检查点还在，可修复后同 thread 续跑。
- 图级错误类型另有专设（`errors.py`：GraphInterrupt、NodeCancelled、ParentCommand 等），把「控制流异常」与「业务失败」分开，不混进重试谓词。

对照 Pack：Pack 现有纪律是超时回池 + 同任务最多 3 次尝试，**没有退避、没有失败分类**——网络抖动和逻辑错误同等对待，且回池立即可见、可能被同一执行体立刻重领再撞同一堵墙。这是两边差距最实的一处。

## 5. 子图嵌套

出处：`graph/state.py` 的 `add_node` 对已编译图的接纳 + `compile` 的 checkpointer 继承（L1200 附近 docstring）、命名空间生成在 `pregel/_algo.py` L615/624。

- **嵌套方式**：`add_node("sub", compiled_graph)` 直接把一个编译好的图当节点；也可以节点函数内部 invoke 另一个图（松耦合版）。
- **状态隔离**：父子图各有自己的 State schema，父图只通过子图的输入/输出 schema 映射键进出——子图内部通道对父图不可见，父图状态变化也不漏进子图（除非显式映射）。
- **检查点分层**：子图与父图共用同一 thread，但落在 `checkpoint_ns = "节点名"`、再嵌套是 `父ns|子名:task_id` 的命名空间里（NS_SEP/NS_END 拼接）；子图编译时没给 checkpointer 就继承父图的。子图的 interrupt 会冒泡到顶层客户端，恢复时按命名空间找回原地。
- 子图的历史可单独按命名空间 list 出来审计。

对照 Pack：Pack 目前没有子任务概念（任务卡是平的）。若将来做「一张卡拆成子卡分给多个成员」，这套「同库存储 + 命名空间分层 + 状态只经映射进出」是现成的组织方式，不必新开一套表。

## 6. 持久化对存储的要求（SQLite 可行性）

出处：`libs/checkpoint-sqlite/langgraph/checkpoint/sqlite/__init__.py` 的 `setup()`（L141 起）。

- **就两张表**：`checkpoints(thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, type, checkpoint BLOB, metadata BLOB)` 与 `writes(thread_id, checkpoint_ns, checkpoint_id, task_id, task_path, idx, channel, type, value BLOB)`，主键都是复合键；开 WAL 模式。状态经 `JsonPlusSerializer` 序列化成 BLOB 整存（另有加密 serde 可选）。
- 即：**单文件 SQLite 是官方一等后端**，与 Pack 中枢的单文件 SQLite 哲学不冲突——LangGraph 的全套持久化语义（快照链 + pending writes）被证明用两张 BLOB 表就能承载。它的代价是状态不可 SQL 查询（全在 BLOB 里，要查得先反序列化），这正是 Pack 的行式任务表更优的地方：Pack 的 status/assignee/lease 都是可直接查询的列。
- Postgres 版（`libs/checkpoint-postgres/`）是同一接口的放大版，说明这套接口对存储的要求本质只有：按 (thread, ns, id) 的键值存取 + 按 id 排序列举，没有用任何数据库高级特性。

---

## Pack 状态机可借清单

| # | 借什么 | 思想/实现 | 落到中枢哪段 | 改动量 |
|---|---|---|---|---|
| 1 | 每次状态迁移存一份完整任务快照并链 parent（§2 快照链） | 思想 | tasks 之外加一张 task_snapshots 表，task_claim/complete/review/cancel 等迁移点写快照；Pack 现有 audit 是流水账，快照链补的是「任意一步可恢复/可 fork」 | 中 |
| 2 | 写出先落盘、提交在后（§2 put_writes 先于 put） | 思想 | 执行体的工作产物/中间进度先登记到 run 名下再报 task_complete；现在完成是一次性提交，执行体中途崩溃产物全丢。给 runs 加中间产物登记即可 | 小–中 |
| 3 | versions_seen 式「已消费不重跑」（§2） | 思想 | 任务加 steps 进度位（执行体逐步勾选落盘），超时回池被重领时从未完成步继续，而不是整单从零重来 | 中 |
| 4 | interrupt 的恢复纪律：恢复 = 重跑，被恢复的动作必须幂等或先查状态（§3） | 思想（纪律） | 写进执行体契约：批准通过后动手前先查 run/task 现状（Pack 已有 run fence，方向一致，把它从「防双开」明确扩为「批准恢复后的第一道检查」） | 小（契约 + 执行体接入时遵守） |
| 5 | 重试先分类再退避（§4 default_retry_on + RetryPolicy 形状） | 思想 | 中枢回池逻辑：按 failure_kind 分流——网络/上游 5xx 类延迟回池（指数退避后才重新可见），逻辑错误类立即回池或直接 blocked 等人工；tasks 加 next_visible_at + failure_kind 两列 | 小–中 |
| 6 | Durability 三档旋钮（§2） | 思想 | 心跳/进度落盘频率目前写死；可给任务加「落盘强度」概念区分长任务与短任务。优先级低，先记着 | 小（可选） |
| 7 | 子图命名空间式层级 id（§5 checkpoint_ns） | 思想 | 将来做父子任务卡时：子卡 id/归属用父任务前缀分层、同表存储、状态只经父卡映射汇总，不新开一套子任务表 | 小（做子任务时用） |
| 8 | 从历史快照 fork 重跑（§2 copy/fork） | 思想 | 依赖第 1 条落地后才成立：失败任务「从第 N 步重开新尝试」而非整单重来；与现有 attempts 上限配合（fork 算一次尝试） | 中（依赖 #1） |

## 不借的理由

- **框架重量与层级不对**：LangGraph 是 monorepo 多包（langgraph + checkpoint + serde + langchain-core 依赖链），且按其 AGENTS.md 自述定位是底层 runtime、agent 层另有 Deep Agents。Pack 中枢是单文件 hub.py + stdlib sqlite3，且中枢本身干的就是 harness/调度层的活——引它进来等于换底盘，还把层级装反了。
- **执行模型尺度不合**：Pregel 超步是进程内函数节点的毫秒级并行，channel/reducer 的声明式合并是为「同一进程里多节点写同一状态」设计的；Pack 的节点是跨机器、带真实账号会话的执行体，分钟级租约，根本没有共享内存状态要合并。
- **interrupt 的实现前提不成立**：带内 interrupt 要求节点函数可从头重放；Pack 执行体的动作发生在外部世界（网页会话、真实账号），不可重放，带外批准门才是与形态匹配的设计——借实现会把执行体逼成假函数。
- **BLOB 快照牺牲可查性**：LangGraph 状态全进 BLOB，运维时不能直接 SQL 查「哪些任务卡在 review」；Pack 的行式列存储在可审计、可手查上更好，不值得为抄快照格式而退回去——所以第 1 条只借「快照链」思想、仍用 Pack 自己的行式 + JSON 列存法。
- **迭代速度与未定型面**：1.2.x 仍在快速演进（DeltaChannel 还挂着 beta、元数据格式明说可能变），Pack 单文件自维护的确定性更适合一个要长期无人值守跑的中枢。
