# M2 Hub Session Binding / Event Projection Design

状态：设计基线；基于 PR #45 的 Hub Inbox Consumer 实现与 Runtime / Hub 源码审计。当前仅定义可信 Session 绑定、事件投影和原子消费边界；尚未实现，不代表 Session 消息已进入 Hub 或 UI。

## 1. 目标

在 Inbox Lease / ACK 之上建立第一个真正产生业务状态的内部 Consumer：将可信 Agent Session 的生命周期、Run 状态和 Assistant 消息投影到 Hub 专用数据模型。

必须同时满足：

- Room 归属由 Hub 创建的 Session 绑定决定，绝不信任 Agent Event 自报的 `roomId`。
- Provider 原生 Session ID 与 Pack 内部 Session ID 分离。
- Session 消息、Agent Run 与现有成员聊天消息 / 任务 Run 分表存储。
- 投影副作用与 Consumer cursor / Lease ACK 在同一个 SQLite 事务内提交。
- 至少一次投递 + 数据库事务幂等；不宣称跨 Provider 或外部系统 exactly-once。
- 不新增面向公网的 Consumer API，不在本阶段连接 SwiftUI 或生产 Runtime。

## 2. 源码审计结论（PR #45 HEAD）

### 2.1 现有模型与边界

- migration v1 已创建 `agent_sessions`：`session_id`、`agent_id`、`room_id`、`provider_session_id`、`status`、`last_event_sequence` 等字段；其中 `room_id` 是指向 Hub `rooms.id` 的外键。
- 旧 `messages` 表要求 `author_id` 指向 `members`，其读路径也通过 members 取作者；不能把 Agent 冒充成员写入该表。
- 旧 `runs` 表绑定 `task_id` 和 `member_id`，不能承载 Provider Agent Run。
- Runtime Event Envelope 当前有可选 `roomId`、`sessionId`、`runId`。Codex Adapter 的 `sessionId` 是 Provider thread ID。
- Agent Event Gateway 由 Agent Token 认证 Agent / Provider，但事件中的 `roomId` 仍是请求内容，不能因此视为 Hub 授权。
- Codex Adapter 的生产 `createSession()` 当前返回 Provider Session ID，但没有像测试 FakeAdapter 那样发出 `session.created` 事件。生产事件流与测试假适配器存在差异，必须补齐并测试。

### 2.2 安全结论

禁止按事件的 `roomId` 创建 `agent_sessions`、Agent 消息或房间可见 Activity。否则持有合法 Agent Token 的进程仍可能尝试把事件关联到另一个房间。

Room 归属只能来自 Hub 内部已授权的 Session 创建记录。事件中的 `roomId` 保留为兼容/诊断字段，但投影器不得将其作为授权依据，也不得覆盖数据库中的绑定。

## 3. 身份模型

区分三个 ID：

| 字段 | 生成方 | 含义 |
|---|---|---|
| `hubSessionId` | Hub | Pack 内部 Session 主键，随机不可预测，绑定唯一 Agent 和 Room |
| `sessionId` | Provider | Provider 原生 Session / Thread ID；兼容当前 Event Envelope 语义 |
| `runId` | Provider | Provider 原生 Turn / Run ID |

新增可选事件字段 `hubSessionId`。不重解释现有 `sessionId`，避免把历史事件中的 Provider ID 误当成 Hub 主键。

Runtime Adapter 在创建/恢复 Provider Session 时，将 Hub 签发的 `hubSessionId` 与返回的 Provider `sessionId` 建立本地映射。之后该 Provider Session 的 `message.*`、`run.*`、`tool.*`、`approval.*` 事件都携带相同的 `hubSessionId`。Provider 原生 ID 仍放在 `sessionId`。

## 4. Hub 可信 Session 绑定

新增 Hub 内部服务，不直接暴露 HTTP/MCP：

- `create_agent_session(context, actor, agent_id, room_name, ...)`
- `bind_agent_provider_session(context, principal, hub_session_id, provider_session_id)`（如需由 Runtime 回报绑定，必须验证 Agent Token 与 Hub Session 绑定）
- `get_agent_session_for_runtime(...)`

创建规则：

1. Actor 必须由 Hub 已有身份解析层认证，首版只允许 dispatcher / owner。
2. Room 必须在 Hub `rooms` 表中存在；写入数据库中的 numeric `room_id`，不保存 Agent 自报 room。
3. Agent 必须存在、未 removed/disabled，且 Provider 处于 active。
4. Hub 生成随机 `hubSessionId`，写入 `agent_sessions`，状态为 `creating`，`provider_session_id` 暂空。
5. 将 `hubSessionId` 返回给受信任的 Runtime 调用方；未来由 Hub 控制面/本机 App 在调用 `RuntimeSupervisor.createSession()` 时传入 Adapter。不能由 Agent Event 自行注册 Session。
6. Provider Session 创建成功后，Runtime 发出 `session.created`，包含 `hubSessionId` 和 Provider `sessionId`；Hub 投影器只允许更新已存在且 `agent_id` 匹配的 Session。
7. 创建失败必须有显式失败路径；不得留下无限期 `creating` 状态。首版定义有界超时回收/显式失败服务，不由任意事件推断失败。

首版不增加任意 Agent 对任意 Room 的自助绑定能力。MCP 工具、SwiftUI 调用入口和 Runtime 控制命令通道另立实现切片。

## 5. 数据模型（建议 additive migration v7）

### 5.1 扩展 `agent_sessions`

- 新增 `last_inbox_seq INTEGER NOT NULL DEFAULT 0`，用于记录 Hub Inbox 水位；不把 Provider 的 `event.sequence` 当作持久化游标。
- 新增部分唯一索引 `(agent_id, provider_session_id)`，仅对非 NULL Provider ID 生效。
- `session_id` 始终是 Hub 内部 `hubSessionId`；`provider_session_id` 存 Provider 原生 ID。
- 不改变旧 `messages`、`runs`、members 和现有任务状态机。

### 5.2 新增 `agent_session_messages`

保存 Agent Assistant 消息，不写入旧 `messages`。

建议字段：

- `message_id`：Hub 生成的稳定主键。
- `session_id`：引用 `agent_sessions.session_id`。
- `provider_message_id`：Provider item/message ID。
- `provider_run_id`：可空。
- `content`：当前消息正文。
- `status`：streaming / completed / failed / interrupted。
- `phase`：Provider 提供的安全、有限长度阶段标签。
- `first_inbox_seq` / `last_inbox_seq`：首次与最近投影事件的 Hub 序号。
- `created_at` / `updated_at` / `completed_at`。

唯一键：`(session_id, provider_message_id)`。消息查询按 `session_id, created_at, message_id` 建索引。内容长度必须有硬上限；超限事件进入阻塞/人工恢复路径，不截断后继续 ACK。

### 5.3 新增 `agent_session_runs`

与任务 `runs` 完全分离，保存 Provider Run：

- Hub 主键 `run_record_id`。
- `session_id`、`provider_run_id`。
- `status`：running / completed / failed / interrupted。
- `started_at` / `updated_at` / `ended_at`。
- `first_inbox_seq` / `last_inbox_seq`。

唯一键：`(session_id, provider_run_id)`。

### 5.4 新增 `agent_event_projection_receipts`

记录内部投影器对每条 Inbox 的处理结果，不复制完整 payload：

- `consumer_id`、`agent_id`、`inbox_seq`、`event_id`、`event_type`。
- `outcome`：applied / ignored / blocked。
- `reason_code`：固定错误码，不存 Token、正文或敏感数据。
- `projected_at`。

唯一键：`(consumer_id, agent_id, inbox_seq)`，并约束同 Consumer + Agent + event_id 唯一。它是诊断和幂等审计记录；Inbox 仍是原始事件事实来源。

## 6. 事件投影规则

首个 Consumer 使用既有 `hub-session-projector` principal。

| Event | 投影行为 |
|---|---|
| `session.created` | 必须携带已存在的 `hubSessionId` 和 Provider `sessionId`；绑定 Provider ID，Session 置 active |
| `run.started` | upsert 独立 Agent Run 为 running；Session 置 active |
| `run.completed` | Agent Run 置 completed，写 ended_at |
| `run.failed` | Agent Run 置 failed，写 ended_at |
| `run.interrupted` | Agent Run 置 interrupted，写 ended_at；Session 置 interrupted |
| `message.delta` | 按 `provider_message_id` upsert，追加 delta；同一 Inbox 事件不可追加两次 |
| `message.completed` | 用 Provider 完整正文覆盖 streaming 缓冲并置 completed，不能把完整正文再次 append 到 delta |
| `connection.changed` | 首版只记录 receipt，不批量改写所有 Session 状态 |
| `run.progress`、`tool.*`、`approval.*`、`usage.updated` | 首版记录 ignored receipt；后续 Activity / Approval 独立切片处理 |

Session 绑定规则：

- 有 `hubSessionId`：必须查到该 Session，且 `agent_id` 与事件 Token 绑定的 Agent 一致。
- 事件中的 `roomId` 不参与房间查询或授权。
- 没有 `hubSessionId` 的旧事件：标记 ignored（legacy_unbound_session），不得猜测映射。
- 未知 Session、Agent 不匹配、Provider Session ID 冲突：标记 blocked，不推进该事件和后续 cursor，等待显式修复；不得静默丢弃。
- `message.*` 缺少 Provider message ID 或 `run.*` 缺少 run ID：按事件类型记录 blocked，不能创建无法幂等定位的记录。

## 7. 原子投影与 ACK

不能采用“先调用 projector，之后再调用当前独立事务的 `ack_agent_events()`”模式；进程可能在副作用提交后、ACK 前崩溃，导致重复追加 delta。

新增固定内部服务，例如：

`process_agent_event_lease(context, principal, lease_id, lease_token)`

事务边界：

1. 验证 issuer-owned principal 必须是 `hub-session-projector`。
2. `BEGIN IMMEDIATE`，校验 Lease、Token、Agent 和 expires_at。
3. 读取当前 Lease 中从 ACK 前缀之后开始的未确认 items，按 inbox_seq 排序。
4. 每条事件先执行固定的 Session projector，再写 projection receipt。
5. 对可处理事件写 applied；对明确不属于本 Consumer 的事件写 ignored；遇到 blocked 事件时停止本批次，不确认该事件及之后的事件。
6. 对成功处理的连续前缀更新 Lease items、Consumer cursor、Lease acked_through 和 completed 状态。
7. 投影数据、receipt、ACK item 与 cursor 在同一事务 commit；任何异常 rollback 全部。
8. 不在 SQLite 事务内调用 Provider、网络、文件系统或 UI。所有投影副作用必须是同一 Hub 数据库内的确定性 SQL。

首版一次处理一个 Lease 批次，允许 blocked 前缀之前的已成功事件提交为部分 ACK；不得越过 blocked 事件。已提交事件重放时由 cursor 与 receipt 双重保护，不重复 append delta。

## 8. 测试与验收

### 身份与房间安全
- Agent Event 自报其他 `roomId` 不改变 Session 的 Hub room_id。
- Agent A 不能向 Agent B 的 hubSessionId 投影。
- 未知 hubSessionId、跨 Agent、Provider Session ID 冲突必须 blocked 且不推进 cursor。
- 旧事件没有 hubSessionId 时不得按 provider session id 或 roomId 猜测绑定。
- 只有 dispatcher / owner 能通过 Hub 服务创建 Session；普通成员不能跨房间绑定。

### 数据与幂等
- migration v6→v7 保留 Inbox、Lease、cursor、Agent、旧 Session 行和 v1-v6 checksum。
- 同一 message.delta 重放不能重复追加文本。
- message.completed 用完整正文替换 delta 缓冲，重复完成事件幂等。
- run.started / completed / failed / interrupted 状态转换正确。
- Agent Session Message 与旧 members/messages、tasks/runs 无交叉写入。
- FK、唯一索引、query plan、fresh / legacy schema snapshots 一致。

### 故障注入
- 投影 SQL 成功后、cursor 更新前抛错：全部 rollback。
- cursor 更新后、commit 前抛错：投影与 ACK 全部 rollback。
- commit 后 ACK 响应丢失：重试不重复追加消息。
- 批次中第 N 条 blocked：只提交前 N-1 条连续前缀，N 及之后保持未 ACK。
- Lease 过期、释放、旧 Token、跨 Consumer principal 不能执行投影。
- 重启后从 durable cursor 恢复，不依赖内存状态。

## 9. 明确非目标

- 不把 Agent 消息写进旧 `messages`，不伪造 member author。
- 不复用任务 `runs` 表，不自动创建 Task / task_card。
- 不在本阶段实现 Tool 调用执行、审批决策、用量计费或自动协作。
- 不新增公网 Consumer HTTP/MCP API。
- 不接入生产 Runtime Supervisor，不改变 Muse v1 主循环。
- 不新增 SwiftUI 页面，不部署或迁移用户 Mac 数据库。
- 不宣称跨 Provider / 外部系统 exactly-once。

## 10. 实施拆分与顺序

1. **M2a Session Binding**：Hub 内部创建/失败/绑定服务；事件契约增加 `hubSessionId`；Runtime Adapter 持有 Provider Session → Hub Session 映射；Codex 生产 Adapter 补齐 `session.created`。
2. **M2b Atomic Consumer Apply**：在 Consumer 服务层增加固定 handler 的事务化 apply + ACK，blocked 前缀语义与 receipt。
3. **M2c Session Projection**：migration v7、Agent Session Message / Run 表、投影服务和真实 SQLite 故障注入测试。
4. **M2d Read API / UI**：单独设计受权限控制的 Session / Agent Message 读取接口，再接 SwiftUI Activity / Chat 页面。
5. **M2e Runtime Wiring**：只有控制面能提供 Hub Session ID、Event Worker 和 Consumer 生命周期后，才接生产 Supervisor；保持可关闭和回退。

每个子阶段独立 PR、CI 和 Codex review。M2a-M2c 完成前不把事件投影接到生产运行路径。
