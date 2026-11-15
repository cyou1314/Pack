# AutoGen 群聊调度拆解笔记（为 Pack 活群备课）

> 审计方式：2026-11-15 浅克隆 `microsoft/autogen` 到 `~/workspace/learn-inspect/autogen`（HEAD `8390985`，版本 0.7.5），纯静态读码，未运行、未填凭证。以下路径均相对 `python/packages/autogen-agentchat/src/autogen_agentchat/`，简称 `agentchat/`。本仓已是 0.4+ 重写版：老 0.2 的 `autogen.GroupChat/GroupChatManager` 实现不在仓内（`pyautogen` 只是转发包），老概念仅作对照提及。

## 0. 一句话定性

AutoGen 的群聊 = **一个进程内的「主持人状态机」**：所有消息广播给全体成员（各自存一份上下文），但**只有被主持人点名的人能发言**。主持人（GroupChatManager）本身不产内容，只做四件事：存消息线、数轮次、判终止、选下一个发言人。Pack 的活群缺的正是这层「讨论调度」；Pack 有而它没有的，是跨进程、跨时间的任务纪律（认领/租约/关单）。

结构速览（`teams/_group_chat/_base_group_chat.py` 的 `_init`，L191 起）：Team 启动时把每个参与者包进一个 `ChatAgentContainer`、连同 Manager 一起以工厂方式注册进 autogen-core 运行时，topic 类型 = 成员名。成员容器（`_chat_agent_container.py`）订阅群 topic 收全部消息进自己的模型上下文（`handle_start/handle_agent_response` 只入队记账），只有收到 Manager 单独发到自己 topic 的 `GroupChatRequestPublish`（`handle_request`，L86）才真正生成回复。**「听得到 ≠ 能说话」是整个设计的地基。**

## 1. Speaker selection：谁决定下一个谁说

选人逻辑不在 Manager 基类里——基类只留抽象方法 `select_speaker(thread)`（`_base_group_chat_manager.py` L296），每种 Team 配一个 Manager 子类实现它。共五种：

### 1.1 RoundRobin（轮转）
`_round_robin_group_chat.py` 的 `RoundRobinGroupChatManager.select_speaker`（L71）：纯索引递增取模，`_next_speaker_index` 随状态保存。零模型调用、零延迟、完全可预测；代价是与内容无关——该谁说由座位顺序决定，不看谁更合适。适合固定流水线，不适合讨论。

### 1.2 Selector（LLM 选人，新版主力）
`_selector_group_chat.py` 的 `SelectorGroupChatManager.select_speaker`（L152），三层优先级：

1. **`selector_func` 自定义函数**（同步/异步均可）：拿到消息线直接返回人名；返回 `None` 才落到下一层。返回非法名直接抛错。注意它**不可序列化**，dump/load 配置时被忽略（类 docstring 明示）。
2. **`candidate_func` 候选过滤**：先把候选池裁小（如只许某几人），再交 LLM 选；返回空/非法名抛错。设了它时 `allow_repeated_speaker` 失效。
3. **LLM 选人**（`_select_speaker`，L232）：默认 prompt 是角色扮演框架（L607）——`{roles}` 是「名字: 描述」逐行清单（描述来自每个 agent 的 `description`，选人质量全押在这份名片上）、`{participants}` 候选名、`{history}` 全文记录；要求模型只回一个名字。解析不靠结构化输出，而是 `_mentioned_agents` 数名字在回复里出现的次数：0 个或 >1 个都算失败，发反馈消息让它重选，最多 `max_selector_attempts`（默认 3）次；仍失败则**兜底用上一个发言人，没有就用第一个成员**（L302–307）——永不卡死，但兜底选择可能很笨。`allow_repeated_speaker=False`（默认）：候选先剔除上一位发言人，模型仍选了上一位也判失败重试。

代价：**每一轮多一次完整 LLM 调用**，且要把全量历史喂给选人模型——轮次和人数一多，选人本身的 token 成本可观；好处是选人理由与内容相关，且选人过程有 `SelectSpeakerEvent` 可观测（基类 `_log_speaker_selection`）。

### 1.3 Swarm（发言人自选下家）
`_swarm_group_chat.py` 的 `select_speaker`（L82）：没有中央选人——当前发言人的回复里若带 `HandoffMessage(target=...)`，下一位就是 target；没有 handoff 就自己连任。开工前校验第一位成员必须能产 HandoffMessage。权力下放给成员，Manager 只做合法性校验。**这正是 Pack 规格明令禁止的「成员互相派活」的实现版**，对照价值大于借鉴价值。

### 1.4 DiGraph（图定流转）
`_graph/_digraph_group_chat.py` + `_graph_builder.py`：成员是节点，边定义「谁之后可以轮到谁」，边可带条件与 activation group（`all`/`any`：前置边全满足/任一满足才激活）。本质是把轮转顺序写成有向图，适合 DAG 式固定工艺。选人 = 图遍历，无模型参与。

### 1.5 MagenticOne（账本式编排）
`_magentic_one/_magentic_one_orchestrator.py`：一个专用 Orchestrator 成员维护任务账本（facts/plan）与进度账本，每轮由它决定派谁、是否重规划。是五种里唯一带「计划」概念的，但仍是单进程单次运行内的编排，不是持久任务系统。

**Random**：新版 agentchat **没有内置随机选人**（`teams/` 下 grep `random` 零命中）；老 0.2 有 `speaker_selection_method="random"`，新版要随机只能用 `selector_func` 自己写一行 `random.choice`。

## 2. GroupChatManager 的职责边界

基类 `BaseGroupChatManager`（`_base_group_chat_manager.py`，326 行）管：

- **消息线**：`_message_thread` 全量留存，每收到回复先并线（`update_message_thread`）再决策。
- **轮次与终止**：`_current_turn` 计数；每轮回复后先过终止条件再选人（`_apply_termination_condition`，L218）；终止时发 `GroupChatTermination` 到输出队列通知调用方，并自动 reset 条件与轮次。
- **点名派发**：`_transition_to_next_speakers`（L194）支持**同时点名多人**——`_active_speakers` 列表跟踪在场发言人，全部回完才推进下一轮（天然支持并行发言轮）。
- **状态存取**：save/load 只涉及消息线、轮次、子类私有索引（如轮转指针、上一位发言人），是可序列化 dict。
- **不管**：不生成内容、不碰成员内部上下文（成员上下文在各自容器里）、不做持久化、不知「任务」为何物。暂停/恢复在基类是空实现（`handle_pause/handle_resume` 为 no-op），由容器侧实现。

## 3. 终止条件

`conditions/_terminations.py`（614 行）+ 基类 `base/_termination.py`：条件是**有状态可组合对象**，支持 `|`（或）与 `&`（与）拼装，命中返回 `StopMessage`，用后必须 `reset()`。内置九种：

- `MaxMessageTermination` 消息总数上限；另有 Manager 级 `max_turns` 轮次上限（与条件并行生效，`_base_group_chat_manager.py` L239）；**两者都不设 + 无终止条件 = 无限跑**，docstring 明示警告。
- `TextMentionTermination` 关键词（如 "TERMINATE"），可限定只认某些来源。
- `TextMessageTermination` 某成员发出纯文本消息即停（常用于「成员交纯文本 = 交活」）。
- `StopMessageTermination` 收到 StopMessage 即停。
- `TokenUsageTermination` 累计 token 上限（分输入/输出）。
- `TimeoutTermination` 墙钟超时。
- `HandoffTermination(target)` 有人 handoff 给指定对象（如 "user"）即停——Swarm 交回人类的标准收尾。
- `SourceMatchTermination` 指定成员发言即停。
- `FunctionalTermination` / `FunctionCallTermination` 自定义谓词 / 某工具被调用即停。
- `ExternalTermination` 外部信号：外部代码调 `.set()` 后下一轮生效——**程序/人从外部掐停的官方口子**。

## 4. Human-in-the-loop 怎么插进群聊

人不是特殊通道，**人就是一个普通成员**：

- `agents/_user_proxy_agent.py` 的 `UserProxyAgent` 实现同一 `BaseChatAgent` 接口，被点名时调 `input_func`（默认 `cancellable_input` 读控制台，可换成任意同步/异步函数——GUI、消息队列都行）拿人的输入当回复。流式模式下先发 `UserInputRequestedEvent` 事件给 UI 层，UI 拿到输入再喂回。
- 组合打法：`UserProxyAgent` + `ExternalTermination`（人看输出决定掐停）、或 Swarm 里 `HandoffTermination(target="user")`（成员主动交回给人）。
- 对照 Pack：Pack 的人（群主）不在成员席里，而是经调度员和批准门介入——AutoGen 是「人坐进群里排队发言」，Pack 是「人在群外拍板」，两种 HITL 拓扑。

## 5. Agent 的能力声明与注册

- 声明面极小：`agents/_base_chat_agent.py` 的 `BaseChatAgent` 只要求 `name`、`description`、`produced_message_types`（能产哪些消息类型）+ `on_messages`。没有能力清单、没有工具注册表挂在这层——工具是各 agent 实现内部的事。**唯一被调度用到的能力声明就是 `description`**：Selector 的 `{roles}` 名片、Swarm 的 handoff 合法性都靠它。
- 注册是**构造时静态清单**：Team 构造器收 `participants` 列表，名字必须唯一（重复直接报错）；运行时注册是框架内部动作（工厂 + AgentType = 名字对应的 topic）。没有运行时动态入群/退群、没有能力发现协议——群在代码里写死，跑起来不变。
- 可序列化：Team 与 agent 都实现 Component（`_to_config/_from_config`），整群可 dump 成 JSON 配置再 load 回来（`selector_func` 这类函数字段除外）。这是它「声明式组群」的底气。

## 6. 确认：没有持久任务状态机

- 全 `agentchat/` grep `lease / claim / assignee` 零命中（仅有的近似词是无关语境）。没有任务实体：`run(task: str)` 的 task 只是一条初始消息；没有待认领/已认领/验收/关单，没有租约心跳，没有尝试次数上限与回池，没有跨进程恢复。
- 所谓状态（`state/_states.py` 的 `TeamState`）= 各 agent 的状态 dict + Manager 的消息线/轮次，`save_state()/load_state()` 返回/接收普通 dict，**存哪、存多久、丢了怎么办全是调用方的事**，框架不落盘、不上数据库。
- 一次 `run()` 就是一个进程内 async 执行：进程死 = 群死，save_state 没在死前调就全丢。
- 与 Pack 对照：Pack 的任务卡（原子认领 + 租约 + 心跳 + 3 次尝试 + 调度员关单 + SQLite 落盘）是 AutoGen 完全没有的一层。**AutoGen 解决「一次讨论怎么进行」，Pack 解决「一堆活怎么长期可靠地派完」——两者不在同一层，这也是为什么只能借它的讨论调度、借不了（也不需要借）它的任务观。**

## 7. 对 Pack 活群的可借鉴清单

### 7.1 调度员仲裁可借（与 Pack「调度员唯一仲裁」同构，可直接吸收）

1. **选人三层优先级**（selector_func 硬规则 → candidate_func 裁候选池 → LLM 选人）→ 落 **中枢/watcher**：活群讨论模式下，调度员选下一个发言成员时先走硬规则（@ 点名、任务相关方优先），规则没覆盖才由调度员（LLM 本人）判断——Pack 的调度员就是 LLM，不需要 AutoGen 那样每轮多调一次独立选人模型，省掉它最大的成本项。
2. **description 即名片**：选人质量押在成员描述上 → 落 **中枢**：成员登记表里的能力/站点登记（规格第 5 节已有）应同时充当讨论选人的 roles 清单，派活与选人共用一份名片，别维护两套。
3. **选人可观测**：每次点名发 `SelectSpeakerEvent` 留痕 → 落 **中枢**：调度员每次决定「下一个谁说」在房间留一条可审计记录（谁、为什么），与任务审计同一套账。
4. **终止条件可组合 + ExternalTermination 外部掐停** → 落 **中枢**：讨论模式的收尾规则拼装化（轮次上限 | 关键词 | 群主喊停信号），群主喊停 = 外部信号置位、下一轮生效，不必等当前发言结束。
5. **并行发言轮**（`_active_speakers` 全部回完才推进）→ 落 **watcher/中枢**：需要多成员同时表态（如各自汇报进展）时可一轮点多人，而不是串行排队；Pack 现有接力上限 3 次的规矩可直接套在轮次上。

### 7.2 成员间讨论可借（不碰派活权，只借讨论形态）

6. **「听得到 ≠ 能说话」的容器模型** → 落 **中枢/watcher**：讨论时全体成员可见全部消息（上下文完整），但只有被调度员点名者发言——比「每人只看派给自己的活」更适合讨论，又不破坏仲裁。实现上就是 watcher 现在的反面：现在是过滤分发，可加一个「讨论模式全量分发 + 点名才回」的开关。
7. **candidate_func 候选裁剪** → 落 **watcher**：讨论议题涉及某站登录/某能力时，先按登记表把候选裁到相关成员，调度员只在小池里选，减少无关成员被拉进讨论烧额度。
8. **allow_repeated_speaker=False 的防独白规则** → 落 **watcher**：同一成员不得连续两轮发言（除非调度员明示），防一个成员在讨论里自说自话刷屏。
9. **HandoffTermination 式「交回」信号** → 落 **中枢/App**：成员发言中带约定信号（如「交回调度员」）即结束讨论轮、回到仲裁——是信号机制，不是派活权，成员只是请求收尾，关单权仍在调度员。

### 7.3 明确不适合 Pack

10. **Swarm 式成员自选下家**：成员互相 handoff = 规格明文禁止的成员互派活，不借。
11. **进程内状态观**：save_state dict 由调用方自存、进程死群死——Pack 已有 SQLite 落盘 + 租约恢复，严格更强，反向不借；AutoGen 的 TeamState 结构也不值得参考。
12. **UserProxyAgent 式「人排队当成员」**：Pack 的群主在群外拍板、走批准门，人不进成员席排队发言，拓扑不同，不借。
13. **无终止即无限跑的默认值**：AutoGen 不设条件就无限讨论；Pack 的一切自动流程必须有上限（接力 3 次、租约、尝试上限已有），讨论模式上线时必须默认带 max_turns，不许裸奔。
14. **静态组群**：构造时写死成员、无动态入群——Pack 的成员经连接器动态入群（规格第 3 节）是产品形态本身，不借它的静态清单；它可序列化的 Component 配置倒是 App 侧日后做「讨论组预设」时可参考的格式，不列入活群改造。

---
*对照基线：Pack SPEC v0.8（角色第 2 节、任务状态机第 4 节、活群 watcher 现状）。本笔记只覆盖群聊调度层；AutoGen 的单 agent 工具调用、模型上下文管理、Studio UI 不在本次范围。*
