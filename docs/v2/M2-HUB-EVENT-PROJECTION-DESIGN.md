# M2 Hub Event Projection / Session Recovery Design

状态：设计基线；基于 Inbox Gateway、Outbox Worker 与 Consumer Lease 实现分支制定。尚未实现业务投影、Session 消息存储、Session replay API 或生产 Runtime 接线。

## 1. 目标

把已持久化的 `agent_event_inbox` 转换为 Pack Hub 可查询、可恢复的 Agent Session 业务状态，同时保持 Inbox 为事件事实源。投影必须可重放、可幂等、可审计；ACK 只在投影结果与投影检查点已持久化后推进。

本阶段先建立 Hub 内部投影边界和 SQLite 模型，不直接接 SwiftUI，不将 Agent 输出伪装成房间聊天消息，不自动批准工具/命令/文件操作。

## 2. 当前代码基线与关键约束

- `agent_event_inbox` 保存规范化事件 JSON，按 `(agent_id,event_id)` 幂等；Hub 全局 `inbox_seq` 是消费排序依据，不能使用 Agent 的 `event.sequence` 代替。
- 事件类型目前包括 `connection.changed`、`session.created`、`message.delta`、`message.completed`、`run.started/progress/completed/failed/interrupted`、`tool.started/completed`、`approval.requested`、`usage.updated`。
- Envelope 可选 `roomId`、`sessionId`、`runId` 当前只是文本字段；Gateway 尚未把它们解析成外键或验证其归属。
- `agent_sessions` 已有 `session_id`、`agent_id`、`room_id`、`provider_session_id`、状态与 `last_event_sequence`，但没有独立的 Agent 消息、Tool 活动或 Approval 请求表。
- Hub 的 `messages` 是现有房间聊天协议，`tasks/runs` 是现有任务执行状态机。二者不能被复用来承载 Agent Session 的原始输出或 Provider Turn。
- 当前 Consumer ACK 服务独立创建连接并拥有事务；因此不能在调用现有 ACK 函数之后再提交投影，否则会产生“游标已推进、投影未提交”的丢事件窗口。投影实现必须先提供同连接事务 API，再启用业务 Consumer。

## 3. 设计原则

1. Inbox 是不可变事实源；投影表是可丢弃并可重建的读模型。
2. 一个逻辑投影 Consumer 使用稳定 ID，例如 `hub-event-projector`；Session replay 使用独立的 `hub-session-projector` 或明确的独立读游标，不能互相推进。
3. 投影副作用、投影处理账本、Consumer cursor ACK 必须在同一个 SQLite 连接、同一个 `BEGIN IMMEDIATE` 事务中提交。
4. 至少一次投递 + 幂等投影，不宣称跨进程恰好一次。
5. 所有 Agent / Session / Run / Tool / Approval 查询都必须带 Agent 作用域；不能仅凭客户端传入的 ID 查数据。
6. 原生 Provider 私人记忆、Credential、Cookie、Token、隐藏上下文不得进入事件投影或房间共享上下文。
7. 未知版本、非法引用或无法安全解释的事件不得静默丢弃；必须进入隔离/死信状态并保留 Inbox 原文和错误码。

## 4. 数据模型建议（additive migration v7）

### 4.1 `agent_event_projection_receipts`

用途：证明某个 Inbox 项已被指定投影器处理，支持幂等重放与审计。

字段：
- `consumer_id`、`agent_id`、`inbox_seq`、`event_id`、`projected_at`、`outcome`、`error_code`。
- 主键 `(consumer_id, agent_id, inbox_seq)`。
- `outcome` 仅允许 `projected`、`quarantined`；可重试的临时错误不写终态 receipt。
- 唯一性和外键约束必须绑定 Inbox 实际行，禁止客户端自报 receipt。

### 4.2 `agent_session_messages`

用途：独立存储 Agent Session 消息，不写入现有 `messages`。

字段建议：
- `message_id`：Pack 生成的稳定 ID。
- `agent_id`、`session_id`：复合外键或等效事务内归属检查，Session 必须属于该 Agent。
- `provider_message_id`：Provider 消息 ID；有值时按 `(agent_id, session_id, provider_message_id)` 唯一。
- `role`：`user`、`assistant`、`system`、`tool` 白名单；最终允许值由 Adapter 契约确定。
- `status`：`streaming`、`completed`、`interrupted`、`failed`。
- `content`、`created_at`、`updated_at`、`completed_at`。
- 可选 `run_id`，必须引用同 Agent 的 Pack Run 记录；不把 Provider run ID 直接当成 Hub tasks/runs 主键。

### 4.3 `agent_session_message_parts`（按需）

只有当 Provider Adapter 能提供稳定 part ID / 顺序时才单独建表。否则首版在同一消息行内按事件顺序追加 delta，并使用 `agent_event_projection_receipts` 保证同一 Inbox delta 不重复追加。不得仅依赖 Provider 的 sequence，因为不同 Session 或重连后可能重置。

### 4.4 `agent_session_runs`

独立于现有任务 `runs`，表示 Provider Session 内的 Turn/Run。字段包括 Pack `run_id`、Agent、Session、可选 provider_run_id、状态、开始/结束时间、最后活动时间和安全错误码。唯一键应绑定 `(agent_id, session_id, provider_run_id)`；无 Provider ID 时由 Hub 生成 ID。

### 4.5 `agent_session_tool_calls`

记录工具调用的开始、完成、失败与输出摘要。只存经白名单过滤的参数/结果摘要，不存凭证、私有文件内容或未经授权的原始环境数据。工具状态投影不得触发工具执行。

### 4.6 `agent_session_approvals`

记录 `approval.requested` 的待审对象、Agent/Session/Run 归属、动作摘要、风险等级、状态与审计引用。事件只创建待审记录；不得由 `approval.requested` 直接执行或批准操作。后续必须由独立 Permission Service + 人工确认闭环完成决策。

### 4.7 Session 与 Provider ID

`sessionId` 必须明确区分 Pack `agent_sessions.session_id` 与 Provider `provider_session_id`。在兼容期：
- 若 envelope 指向 Pack Session ID，必须校验 `session_id + agent_id`；
- 若 Adapter 只能提供 Provider Session ID，必须通过 `agent_id + provider_session_id` 精确映射，且该组合必须唯一；
- 禁止跨 Agent 回退查找、猜测最近 Session 或自动把未识别事件挂到当前活动 Session；
- 缺失或冲突时进入 quarantine，不得创建错误 Session。

在新增索引/约束前，先审计历史数据中 provider_session_id 的重复情况；不能直接添加会使旧库迁移失败的唯一索引。

## 5. 事件投影规则

| 事件 | 允许的投影副作用 | 明确禁止 |
|---|---|---|
| `connection.changed` | 更新 Agent connection 状态/最近错误码 | 写房间聊天消息 |
| `session.created` | 创建或更新 Agent Session，校验 Agent 归属 | 覆盖另一个 Agent 的 Session |
| `message.delta` | 按稳定消息 ID 创建 streaming 消息并追加 delta | 重复追加、写入 `messages` |
| `message.completed` | 完成指定消息，记录完成时间 | 将未匹配 delta 静默并入其他消息 |
| `run.started/progress/completed/failed/interrupted` | 更新 Agent Session Run 生命周期 | 修改 Hub 任务 `runs` 状态机 |
| `tool.started/completed` | 更新 Tool Call 记录和安全摘要 | 执行工具或自动批准 |
| `approval.requested` | 创建待审记录并写审计关联 | 自动批准、直接执行命令 |
| `usage.updated` | 更新按 Agent/Session/Run 分区的计量快照 | 把 Provider 账单数字视为可信收费事实 |

所有事件 payload 都需要按 event type 做版本化 schema 验证。当前 Gateway 没有顶层 schemaVersion；投影实现前必须在契约中确定兼容规则。未知字段不得直接映射到 SQL 列；敏感字段过滤继续在 Gateway 层执行，投影层还要做二次白名单验证。

## 6. 原子处理与 ACK API 改造（P0）

当前 `ack_agent_events(context,...)` 自己创建连接并提交事务，不能满足投影原子性。下一实现必须拆分为：

- `ack_agent_events_in_transaction(connection, principal, lease_id, lease_token, through_inbox_seq, now)`：只在调用方事务中执行校验与游标推进，不 commit、不 close。
- `claim_agent_events(...)`：保留独立事务 API。
- 外层 `project_agent_event_batch(context, principal, lease, projector)`：
  1. 获取 Hub 锁并打开一个连接；
  2. `BEGIN IMMEDIATE`；
  3. 校验 Lease 与 Principal；
  4. 对每条 Inbox item 检查 receipt；已存在则不重复副作用；
  5. 验证事件 envelope、Agent、Session、Run 引用；
  6. 执行投影写入，写 `projected` receipt；
  7. 对可安全终结但不可解释的 poison event 写 `quarantined` receipt 和错误记录；
  8. 在同一事务内 ACK 到连续处理完成的最后一条 Inbox item；
  9. commit。
- 任一 SQLite 写入、投影或 ACK 校验失败，整笔 rollback；不得推进 cursor。
- 若处理进程在 commit 前崩溃，投影和 ACK 一起回滚；commit 后崩溃则 receipt 与 cursor 一起存在，重试不会重复副作用。

注意：批次可能包含不同 Agent 的全局 inbox_seq 间隔，但一个 Lease 只领取单一 Agent 流。ACK 必须只推进该 Lease items 中实际领取且连续完成的前缀。

## 7. Poison Event 与重放

- 临时故障（数据库忙、IO 错误、事务冲突）不写终态 receipt、不 ACK，等待 Lease 过期后重试。
- 永久格式错误、未知 event type/schema、Agent/Session 引用冲突进入 quarantine；保留 Inbox 原文、错误码、首次/最近失败时间和重试次数。
- quarantine 不是删除：管理员可查看并修复映射，之后以显式 replay 操作重新投影。
- replay 必须创建审计记录，保留原始 Inbox 行与原 receipt 历史；不能静默修改已投影内容。
- 对已 quarantine 的事件，Consumer 可以 ACK 的前提是 quarantine 记录和 receipt 在同一事务成功持久化。

## 8. Session Replay / Read API（后续切片）

Session 读取必须按 `agent_id + session_id` 授权；分页游标使用 Hub 生成的单调序号或稳定 message_id，不使用时间戳作为唯一排序键。API 应支持：
- Session 列表与状态；
- 消息分页与增量 delta；
- Run / Tool / Approval 时间线；
- `after_cursor` 补拉；
- Session 当前投影版本与最后处理 Inbox 序号；
- 明确的 reset/rebuild 状态。

重建流程先创建独立 projection generation，在影子表/临时命名空间完整重放并校验，再原子切换 generation；不允许清空在线读模型后边重放边对用户可见。

## 9. 验收测试

1. 同一 Inbox event 重放两次，消息内容、delta、Run、Tool、Approval 记录只产生一次副作用。
2. 投影写入成功但 ACK 前注入异常，确认投影和 receipt 均 rollback，cursor 不前进。
3. ACK SQL 成功但 commit 前进程异常，确认投影与 ACK 全部回滚。
4. commit 后模拟响应丢失，重试后 receipt 阻止重复投影。
5. 两个 HubContext / 两个 SQLite connection 并发 claim/project 同一 Agent，不出现双重活动 Lease 或 cursor 越级。
6. 同一 provider_session_id 在不同 Agent 下允许存在；跨 Agent 引用必须拒绝。
7. Session ID 不存在、归属不匹配、Run 不匹配、Provider ID 重复均 quarantine，不误挂载。
8. poison event 不阻塞后续事件永久前进，但必须有持久化 quarantine 记录和审计。
9. 重建期间在线读模型保持可读；generation 切换前后分页游标行为明确。
10. 现有 `rooms/messages/tasks/runs/approvals` 旧路径零回归；Agent 私人记忆和 Credential 不进入投影。

## 10. 非目标

- 不在本阶段接入 SwiftUI、Runtime Supervisor 或 Provider 真实登录。
- 不把 Agent Session 消息写进现有 `messages`。
- 不把 Agent Run 写进现有任务 `runs`。
- 不触发 Tool 执行、审批批准、任务创建或群聊广播。
- 不实现多 Agent 协作调度、Workflow DAG 或 Computer Use。
- 不在用户 Mac 的生产数据库上运行迁移。

## 11. 实施顺序

1. 先合入 Foundation / Outbox / Consumer 依赖切片并确认 CI。
2. 审计 Agent Session / provider_session_id 历史唯一性和事件 payload 实际样本。
3. 定稿 event payload schema 与 Session ID 语义。
4. additive migration v7：projection receipts、Agent Session messages/runs/tool calls/approvals/quarantine。
5. 把 ACK 拆成同连接事务内 helper，保留现有独立 ACK 包装 API。
6. 实现单一 Hub Event Projector，先支持 session.created、message.delta/completed、run 生命周期。
7. 加入事务故障注入、重复投影、跨 Agent 隔离、quarantine 和 replay 测试。
8. 独立实现 Session Read / Replay API；通过权限审查后再连接 Runtime / UI。

完成条件：投影副作用与 Consumer cursor ACK 原子提交；Inbox 可完整重放；所有 Agent 业务对象严格按 Agent + Session 作用域隔离；旧 Hub 聊天、任务与审批路径无回归。
