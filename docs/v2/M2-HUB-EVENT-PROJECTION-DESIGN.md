# M2 Hub Event Projection / Session Recovery Design

状态：设计基线；定义 Hub Inbox 到 Agent Session 读模型的投影边界。migration v7 Schema 已在 PR #50 实现且 Hub、Runtime、Staging / Staging56、macOS CI 全部通过，尚未合入；业务 Projector、Session Read API 和生产 Runtime 接线尚未实现。

依赖关系：
- PR #41：已合入 v2 Foundation，提供 Agent Event Inbox / Outbox 基础。
- PR #43：Outbox Delivery Worker，独立投递实现，CI 已通过，PR 尚未合入。
- PR #45：Hub Inbox Consumer Lease / ACK，服务层与 migration v6 已实现，CI 已通过，PR 尚未合入。
- PR #47：M2a Hub-issued Agent Session Binding，提供 Hub Session ID 契约和 Codex Adapter 绑定，当前 Draft；必须先通过最新 CI。
- 本文对应 PR #48，只是设计文档，不引入代码、数据库迁移、用户数据或 Mac 部署。

## 1. 目标与范围

将已持久化的 agent_event_inbox 转换为 Hub 可查询、可恢复的 Agent Session 消息和 Run 状态。Inbox 是事件事实源，投影表是可重建读模型；投影副作用、处理回执和 Consumer ACK 必须原子提交。

首个投影切片只支持当前 Codex Adapter 已实际产生的 Session、Assistant Message 和 Run 生命周期事件。Tool Call、Approval、Usage、Session Read API、完整投影 generation rebuild 均不属于 migration v7 的首批范围。

明确保持隔离：
- 不写入现有 rooms/messages。
- 不修改现有 tasks/runs 状态机。
- 不因事件而执行工具、批准命令、创建任务或广播房间消息。
- 不把 Provider 私人记忆、Credential、Cookie、Token 或隐藏上下文写入 Hub 投影。

## 2. 必须遵守的上游契约

### 2.1 Inbox 顺序与身份

- Agent Event Gateway 从 Agent 专属 events:write Token 派生 agent_id 和 provider_id；投影器不得信任事件自行声明的身份。
- agent_event_inbox 的 inbox_seq 是 Hub 持久化顺序。每个 Consumer 按 Agent 独立维护 cursor；不同 Agent 的全局 inbox_seq 之间允许存在间隔。
- Adapter 的 event.sequence 是进程内诊断序号，重启后可能重置；不能作为数据库排序、去重、ACK 或 Session cursor。
- 相同 (agent_id,event_id) 且 payload 相同是幂等重复；相同 ID 不同 payload 必须拒绝。

### 2.2 Session ID 的唯一语义

事件 Envelope 字段必须按以下语义解释：

| 字段 | 含义 | 投影规则 |
|---|---|---|
| agentId | Token 绑定的 Pack Agent | 以 Gateway 派生身份为准 |
| sessionId | Provider 原生 Session ID；Codex 中为 App Server threadId | 存入 agent_sessions.provider_session_id，不作为 Pack 主键 |
| hubSessionId | Hub 签发的 Pack Session ID；对应 agent_sessions.session_id | 新 Runtime 的首选绑定键，必须校验 Agent 归属 |
| runId | Provider 原生 Turn / Run ID | 存入 agent_session_runs.provider_run_id |
| payload.itemId | Provider 原生消息 Item ID | 存入 agent_session_messages.provider_message_id |
| roomId | 事件附带的上下文字段 | 不用于授权、Room 查询或 Session 绑定 |

Hub Session 必须先由 Hub 控制面通过 create_agent_session 创建，绑定已存在的 Agent 和 Room，初始状态为 creating，provider_session_id 为空。Agent Event 不得创建 Pack Session、改变 Room 归属或覆盖既有 Provider 绑定。

能力探测 GET /agent/capabilities 当前返回 event_envelope_version=2、supported_fields=[hubSessionId]、feature=hub-session-id-envelope-v1。该 feature 仅表示 Gateway 能识别并持久化可选 hubSessionId 字段；不表示 Projector、Session Read API、UI 或审批链路已经就绪。

### 2.3 session.created 的绑定语义

Codex Adapter 在 thread/start 成功后发出 session.created：
- Envelope 的 sessionId 是 Provider thread ID。
- Envelope 的 hubSessionId 是 Hub 预先签发的 Session ID。
- payload.providerSessionId 必须与 Envelope sessionId 一致。
- Projector 只能绑定已经存在、agent_id 匹配、status=creating 且 provider_session_id IS NULL 的行，并在同一事务中写入 Provider ID、切换为 active。
- 同一 Provider ID 的重复事件是幂等成功；不同 Provider ID 不得覆盖。
- failed、completed、interrupted、disconnected 等终态 Session 不得被迟到事件复活。
- session.created 不负责创建 agent_sessions 行。没有 hubSessionId 的旧事件不得猜测绑定。

恢复已有 Session 时，Runtime 使用已知的 Provider ID 和 Hub Session ID 恢复 Adapter 内存映射；不得为恢复操作伪造一个新的 Session。

### 2.4 当前 Codex Adapter 的真实事件形态

已核对 M2a 分支 runtime/agent-core/adapters/codex-app-server.mjs：
- message.delta payload 包含 itemId 和 delta；长 delta 会按最多 4000 UTF-16 code units 无损分块，不能拆开 surrogate pair。
- message.completed 包含 itemId、phase、textAvailable、textIncluded、textByteLength、textSha256；正文不超过 24 KiB 时包含 text，超过时只发送长度和 SHA-256，不静默截断。
- run.started、run.completed、run.failed、run.interrupted 使用 Provider Turn ID。
- run.progress 可能由非 Assistant Message 的 item/started 或 item/completed 通知产生。
- approval.requested 当前由 Codex JSON-RPC Approval 请求产生。
- 当前 Adapter 未发布 tool.started、tool.completed 或 usage.updated；不能因为 Gateway 枚举允许这些类型，就假设 Provider 已提供相应事件。
- 当前 Adapter 没有独立的 User Message 事件；首版只投影实际可验证的 Assistant Message，不伪造 User Message。

当前 Envelope 没有独立 payload schemaVersion。首版必须按 provider_id + event_type 使用严格的 payload 白名单校验；未知字段、缺失必需 ID 或不匹配的字段类型进入 blocked。未来不兼容的事件格式变化必须先增加明确的 Envelope / Payload 版本协商，再部署新 Adapter，不能靠猜测兼容。

## 3. 设计原则

1. Inbox 是不可变事实源；投影表只保存可查询状态，不替代原始事件。
2. 使用固定内部 Consumer principal：hub-session-projector。consumer_id 必须由 Hub 内部 issuer 签发，不接受客户端传入。
3. 投影按 Agent + Hub Session 严格隔离；所有查询必须带 Agent 作用域，不能只凭客户端给出的 Session ID 查询。
4. 至少一次投递 + 幂等投影；不宣称跨进程、跨系统 exactly-once。
5. 仅执行确定性的同库 SQL；事务中不得调用 Provider、网络、文件系统、UI 或外部工具。
6. 永久不可解释的事件不能自动 ACK 丢弃；必须 blocked，修复后重试，或由授权操作者显式 skip 并留下审计。
7. 旧 Runtime 事件可以继续被 Gateway 接受，但缺少 hubSessionId 的 Session 事件不得猜测归属。

## 4. migration v7 数据模型

migration v7 只新增 Agent Session 投影所需的最小数据结构，采用 additive migration；不得修改已发布 v1-v6 migration 定义或 checksum。

### 4.1 扩展 agent_sessions

- 新增 last_inbox_seq INTEGER NOT NULL DEFAULT 0，记录该 Session 最近成功投影的 Hub Inbox 序号。
- 保留 session_id 作为 Hub Session 主键，provider_session_id 保存 Provider 原生 ID。
- 对 (agent_id, provider_session_id) 建立非 NULL 部分唯一索引前，必须先审计历史重复值。
- 迁移前置检查会 fail closed：按 Agent 检查重复 Provider Session ID，并拒绝空值、首尾空白或超过 160 字符的非规范绑定。错误信息只输出冲突组数和必要的 Agent ID，不输出 Provider Session ID；不得自动删除、覆盖、修剪或合并旧 Session。
- 真实 Mac 数据库尚未完成只读 Shadow preflight，因此不能宣称历史唯一性已验证。CI fixture 只能证明迁移逻辑，不代替用户数据库审计。

### 4.2 agent_session_messages

独立保存 Agent Session Assistant 消息，不复用 Hub 房间 messages。

建议字段：
- message_id：Pack 生成的稳定主键。
- session_id：引用 agent_sessions.session_id。
- provider_message_id：Provider Item ID。
- provider_run_id：可空的 Provider Turn ID。
- content：已投影正文。
- status：streaming / completed / failed / interrupted / incomplete。
- phase：经白名单过滤的有限长度阶段标签。
- first_inbox_seq / last_inbox_seq：首次和最近写入该消息的 Hub 序号。
- created_at / updated_at / completed_at。

唯一键为 (session_id, provider_message_id)。首版 role 固定为 assistant，因为当前 Adapter 没有发布独立 User Message 事件。消息内容设置 4 MiB 硬上限；超过上限时进入 blocked，不能截断后继续 ACK。Agent 查询必须通过 agent_sessions 联结并校验 agent_id。

### 4.3 agent_session_runs

独立于现有任务 runs，保存 Provider Turn 生命周期：
- run_record_id：Pack 生成的稳定主键。
- session_id：所属 Hub Session。
- provider_run_id：Provider Turn ID。
- status：running / completed / failed / interrupted。
- started_at / updated_at / ended_at。
- first_inbox_seq / last_inbox_seq。

唯一键为 (session_id, provider_run_id)。事件中的 runId 只写入 provider_run_id，不得当作 Hub tasks/runs 主键。

### 4.4 agent_event_projection_receipts

记录内部 Projector 对每条 Inbox 的处理结果，不复制完整 payload：
- consumer_id、agent_id、inbox_seq、event_id、event_type。
- outcome：applied / ignored / blocked / skipped。
- reason_code：固定错误码，不存 Token、正文或凭证。
- attempt_count、first_attempt_at、last_attempt_at、projected_at。

唯一键至少包含 (consumer_id, agent_id, inbox_seq)，并约束同 Consumer + Agent + event_id 唯一。
- applied / ignored：终态处理结果，可以 ACK。
- blocked：可重试状态，不 ACK；前置条件修复后重新读取 Inbox 原文并更新同一 receipt。
- skipped：仅在不可修复事件经过授权操作者显式处置后产生。
- 不使用自动 quarantine + ACK 语义，避免永久格式错误静默越过。

### 4.5 agent_event_projection_resolutions

记录对 blocked 事件的人工处置：
- resolution_id、consumer_id、agent_id、inbox_seq、event_id。
- action：首版仅允许 skip。
- reason：必填、长度受限的处置说明，不得包含 Token、正文或凭证。
- actor、resolved_at。

唯一键为 (consumer_id, agent_id, inbox_seq)。Resolution 不可覆盖；原始 Inbox 行永久保留。skip 只表示授权 Consumer 明确放弃该事件的业务投影，不删除或改写 Inbox。

### 4.6 首版明确不创建的表

migration v7 不创建 agent_session_tool_calls、agent_session_approvals 或 usage 投影表。当前 Adapter 没有完整 Tool 生命周期事件，Approval 也需要独立 Permission / 人工确认闭环。首版只为这些事件写 ignored receipt，不得自动批准、执行或制造不完整的业务对象。后续每个能力独立设计、独立迁移和验收。

## 5. 事件投影规则

| Event | 首版投影行为 | 禁止行为 |
|---|---|---|
| session.created | 绑定已存在的 Hub Session；校验 hubSessionId、agent_id、sessionId 和 providerSessionId；write-once | 创建新 Session、覆盖 Room、复活终态 Session |
| message.delta | 按 sessionId + hubSessionId 校验归属；按 itemId 创建 streaming Assistant Message 并追加 delta | 重复追加、跨 Session 合并、写旧 messages |
| message.completed | 校验 itemId 和完成正文元数据；按规则替换或核对累计内容，再设置终态 | 把最终正文再次 append 到 delta、静默截断 |
| run.started | upsert Agent Run 为 running；更新 Session 活动状态和 last_inbox_seq | 修改任务 runs |
| run.completed | Agent Run 置 completed；同 Run 下残留 streaming 消息置 incomplete | 将不完整正文伪装成 completed |
| run.failed | Agent Run 置 failed；同 Run 下残留 streaming 消息置 failed，保留部分文本 | 删除部分输出 |
| run.interrupted | Agent Run 置 interrupted；同 Run 下残留 streaming 消息置 interrupted；Session 置 interrupted | 把中断伪装为完成 |
| run.progress | 首版写 ignored receipt | 创建虚假 Tool Call |
| connection.changed | 首版写 ignored receipt | 批量改写所有 Session 状态 |
| tool.* / approval.* / usage.updated | 首版写 ignored receipt | 自动执行工具、自动批准或视为可信计费事实 |
| 缺少 hubSessionId 的旧 Session 事件 | 写 ignored receipt，reason=legacy_unbound_session | 按 Provider ID、Room 或最近活动 Session 猜测归属 |

### 5.1 Message 完成正文校验

- textIncluded=true：必须存在 text；校验 UTF-8 byte length 和 SHA-256；校验通过后用完整 text 替换 delta 累积缓冲，而不是再次追加。
- textIncluded=false 且 textAvailable=true：使用已累积 delta 校验 textByteLength 和 textSha256；完全匹配才标记 completed。
- 缺少必要正文、长度/hash 不匹配或事件顺序无法恢复时，标记 incomplete 并保留已有内容；不得伪造完整正文。
- 消息内容超过 4 MiB 时进入 blocked，不截断后 ACK。
- 已 completed 的消息遇到重复完成事件必须幂等；不同内容冲突进入 blocked，不覆盖已完成正文。

### 5.2 Session / Run 归属校验

- 有 hubSessionId：必须查到对应 agent_sessions 行，且 agent_id 与 Token 派生 Agent 一致。
- Envelope sessionId 必须与该 Session 的 provider_session_id 一致；session.created 首次绑定时按 write-once 规则校验。
- roomId 不参与授权和归属检查；Room 只取 Hub Session 行中已保存的 room_id。
- 未知 Session、跨 Agent Session、Provider ID 冲突、缺少 itemId / runId、非法 payload 均写 blocked，不推进该事件及后续 cursor。
- 对旧 Runtime 缺少 hubSessionId 的事件，只能 ignored，不能自动回填或猜测。

## 6. 原子投影与 ACK（P0）

当前 ack_agent_events(context,...) 自己创建连接并拥有 BEGIN / COMMIT，不能在投影后再单独调用，否则会产生“投影已提交、ACK 未提交”的重复追加窗口。

必须先将 ACK 逻辑拆分为：
- ack_agent_events_in_transaction(connection, principal, lease_id, lease_token, through_inbox_seq, now)：只在调用方事务中执行 Principal、Lease、Token、过期时间、Lease item 前缀和 cursor 校验；不创建连接、不 commit、不 close。
- release_agent_event_lease_in_transaction(connection, principal, lease_id, lease_token, now)：仅在调用方事务中释放当前合法 Lease；不创建连接、不 commit、不 close。
- 原 ack_agent_events(context,...) 保留为兼容包装器：打开连接、BEGIN IMMEDIATE、调用事务 helper、COMMIT。

新增固定内部服务 process_agent_event_lease(context, principal, lease_id, lease_token)：
1. 要求 issuer-owned principal 为 hub-session-projector。
2. 打开一个 Hub SQLite 连接并 BEGIN IMMEDIATE。
3. 校验 Agent、Principal、Lease、Lease Token、Lease 状态和 expires_at。
4. 读取该 Lease 中从已 ACK 前缀之后开始的未确认 item，按 inbox_seq 排序。
5. 对每条事件执行固定 Projector；已有 applied / ignored receipt 不重复执行副作用。
6. 成功处理的事件写 applied / ignored receipt；只有连续成功前缀可以 ACK。
7. 遇到 blocked：写入或更新 blocked receipt，停止处理后续事件；只 ACK 该 blocked item 之前的成功前缀，并在同一事务中释放当前 Lease，使修复后可以立即重新 claim。
8. 使用 ack_agent_events_in_transaction 推进 Lease items、acked_through 和 Consumer cursor；不能越过 blocked item。
9. 投影表、receipt、Lease item、Lease cursor 和 Session last_inbox_seq 在同一事务 COMMIT。

任何 SQLite 写入失败、ACK 校验失败或未预期程序异常都必须整体 ROLLBACK。可预期的 blocked 业务结果可以提交 blocked receipt 和之前成功的连续前缀，但不得确认 blocked item。事务内不得访问 Provider、网络、文件系统或 UI。

## 7. Blocked 修复与人工处置

- 临时错误（SQLite busy、IO 错误、事务冲突）不写终态 receipt、不 ACK；事务回滚后由 Lease 过期或释放机制重新投递。
- 可修复的 blocked（例如 Hub Session 绑定尚未完成）在可信前置条件修复后，重新读取原 Inbox JSON 并重试；receipt 从 blocked 更新为 applied / ignored。
- 不可修复的 blocked（例如跨 Agent hubSessionId、无法补齐的 Provider ID、内容冲突）不得自动越过。
- 新增内部 resolve_blocked_agent_event(context, actor, agent_id, inbox_seq, reason, action='skip')，只允许 Hub 身份解析层确认的 dispatcher / owner。
- skip 必须确认该事件是该 Consumer + Agent 当前 cursor 之后最早未确认事件；在一个事务中写不可变 resolution、将 receipt 置 skipped、写 Hub audit，并推进对应 Lease item / cursor。不得跨过前序事件。
- 重复 skip 返回既有 resolution，不重复审计；原始 Inbox JSON 不得编辑或删除。

## 8. Session Read / Replay 与完整重建的边界

Session Read API 是读模型查询，不是 Inbox Consumer；读取分页不得推进 Inbox cursor。后续 API 必须按 agent_id + session_id 授权，分页使用 Hub 稳定游标或 message_id，不使用时间戳作为唯一排序键。

完整 projection generation rebuild 不属于 migration v7：
- 当前 receipt 唯一键和 Consumer cursor 是单一 live projection，不支持直接清空投影表后从头重放。
- 后续重建必须为 generation 引入独立 receipt、独立 cursor / Consumer 身份和隔离的影子投影表；完成校验后原子切换 generation。
- 不得复用旧 receipt 让重建跳过历史事件，也不得在在线读模型上边清空边重放。
- 在 generation 机制单独设计和实现前，不宣称支持完整投影 generation rebuild。

## 9. 验收测试

1. 同一 message.delta 重投或重复处理，不重复追加文本。
2. session.created 只绑定已有 creating Session；相同 Provider ID 幂等，不同 ID 不覆盖。
3. failed / completed / interrupted Session 不被迟到 session.created 复活。
4. Agent A 不能投影到 Agent B 的 hubSessionId；事件自报 roomId 不改变 Hub Session room_id。
5. Provider ID 历史重复检测在 migration v7 前执行；发现重复时迁移 fail closed，旧数据不变。
6. 小于等于 24 KiB 的 message.completed 校验完整正文 hash 并替换 delta 缓冲。
7. 大于 24 KiB 的 message.completed 校验累计 delta 的 byte length / SHA-256；不匹配时标记 incomplete。
8. 跨块 emoji、代理对和多字节 UTF-8 文本无损；超过 4 MiB 的消息进入 blocked，不截断后 ACK。
9. run.completed / failed / interrupted 正确终结残留 streaming 消息，保留部分文本，不改写已 completed 消息。
10. Tool / Approval / Usage 首版只写 ignored receipt，不产生工具执行或审批副作用。
11. 投影写入后、ACK 前注入异常，投影和 receipt 全部 rollback，cursor 不前进。
12. ACK SQL 执行后、COMMIT 前注入异常，投影、receipt、Lease item 和 cursor 全部 rollback。
13. COMMIT 后模拟响应丢失，重试不重复追加消息。
14. 批次第 N 条事件 blocked 时，只 ACK 前 N-1 条连续成功前缀；N 及之后保持未 ACK。
15. blocked 修复后重试可更新同一 receipt；不可修复事件只能经 dispatcher / owner 审计 skip。
16. 两个 HubContext / SQLite connection 并发 claim/project 同一 Agent，不出现双重活动 Lease 或 cursor 越级。
17. v6→v7 保留 Inbox、Lease、cursor、Agent、旧 Session 行和 v1-v6 checksum。
18. 现有 rooms/messages/tasks/runs/approvals 旧路径零回归；不把私人记忆和 Credential 投影到 Hub。

## 10. 实施顺序

1. 等 PR #43 Outbox Worker、PR #45 Inbox Consumer 依赖切片完成最终审查并按依赖顺序合入 Foundation。
2. 先让 PR #47 M2a Session Binding 最新 CI 全绿，再合入 Foundation；确认 hubSessionId / sessionId 语义和能力协商契约稳定。
3. 对可访问的数据库副本执行 provider_session_id 重复值只读审计；真实 Mac 数据库必须在备份副本和 Shadow preflight 中单独核验。
4. 定稿 Codex event payload validators 和 Session / Provider ID 映射规则。
5. PR #50 已实现 additive migration v7：last_inbox_seq、Agent Session Messages、Agent Session Runs、Projection Receipts、Projection Resolutions；完整 CI 已通过，待按依赖顺序合入。
6. PR #49 已拆分 ACK / Release 事务 helper，保留旧包装 API；后续 Projector 必须复用事务内 helper。
7. 实现 hub-session-projector，先支持 session.created、message.delta / completed 和 run.started / completed / failed / interrupted。
8. 加入真实 SQLite 事务故障注入、重复投影、跨 Agent 隔离、blocked / skip 和 migration 回归。
9. 后续独立设计 Session Read API、Tool / Approval 投影与 generation rebuild；通过权限和安全验收后再接 SwiftUI。
10. 最后才考虑生产 Runtime 接线；必须可关闭、可回退，并先完成用户 Mac 数据库备份、恢复演练和只读 Shadow preflight。

完成条件：投影副作用与 Consumer cursor ACK 原子提交；Hub Session 与 Provider Session ID 绑定 write-once；Inbox 可重试且不可解释事件不会被静默丢弃；旧 Hub 聊天、任务、审批路径无回归；没有在真实 Mac 数据库上未经备份地执行迁移。

## 11. 明确非目标

- 不把 Agent Message 写进旧 messages，不伪造 member author。
- 不把 Agent Run 写进旧 tasks/runs。
- 不自动执行 Tool、批准命令或文件修改。
- 不在本阶段创建 Tool Call / Approval / Usage 读模型。
- 不新增公网 Consumer HTTP / MCP API。
- 不接入生产 Runtime Supervisor，不改变 Muse v1 主循环。
- 不新增 SwiftUI 页面，不部署或迁移用户 Mac 数据库。
- 不宣称跨 Provider / 外部系统 exactly-once。
