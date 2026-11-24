# M2：Runtime Supervisor 首个实现切片

- 日期：2026-11-24
- 首个 Supervisor 切片 PR #39 已于 2026-11-24 合入 Foundation（merge commit `d181765a4d624542f95fea305494e2bed7f42021`）；后续 PR #41 已合入 Outbox / Hub Inbox；Agent Event Gateway 在独立后续分支继续实现。
- 状态：**Supervisor、SQLite Durable Outbox、Hub Inbox、Agent 专属配对凭证与事件写入 Gateway 已实现；尚未接入 Outbox 网络投递 Worker、远端 ACK 或生产 Runtime。**
- 目标：在接入更多 Provider 之前，先把 Adapter 生命周期、Session 操作、事件持久化边界和审批 fail-closed 规则收敛到一个可测试的控制对象。

## 1. 本批范围

新增 `runtime/agent-core/supervisor.mjs`，由 `RuntimeSupervisor` 统一管理：

- 通过既有 `AgentAdapterRegistry` 创建 Adapter 实例；
- 连接、断开和状态查询；
- Session create / resume / interrupt；
- 发送消息前检查 Agent 状态和 Session 归属；
- 验证 Adapter Event 的身份、类型与敏感字段，并深度冻结嵌套 payload，避免下游订阅者篡改审计对象；
- 按 Adapter 事件顺序调用注入的 Event Store；
- 只有 Event Store 成功确认后，才向下游订阅者发布事件；
- 事件写入失败时进入 `degraded`，阻止新的消息/工具动作，允许显式重试；
- `approval.requested` 必须经过注入的授权回调，授权完成后重新检查 Agent/Adapter/请求状态；缺少授权服务或授权不明确时拒绝，不自动 accept；
- 同一审批请求只允许一个并发响应；进入 disconnect 后到达的审批事件不会进入可操作 pending 集合。

## 2. Event Store 契约

Supervisor 构造时必须注入 `eventStore.append(event)`。该实现必须：

1. 以 `eventId` 幂等去重；
2. 只有在事件已持久化提交后才 resolve；
3. 返回 `{ inserted: boolean }`，重复事件返回 `inserted: false`；
4. 写入失败时 reject，Supervisor 保留待写队列并进入 `degraded`。

Supervisor 内存队列仍只承担当前进程内的短暂缓冲。新增 `SqliteEventStore` 将每个事件以 `eventId` 唯一键写入独立 SQLite 文件，启用 WAL 与 `synchronous=FULL`，事务提交后才 resolve；重复事件幂等去重，事件 ID 对应不同内容时拒绝；只有显式 `markDelivered()` 才会从 pending 列表移除。关闭并重新打开数据库后可恢复未确认事件。它目前是本机 Outbox 实现，不代表 Hub Inbox、网络投递或端到端至少一次交付已完成。

## 3. 生命周期与失败语义

状态：

- `disconnected`：未创建或已停止 Adapter；
- `connecting`：Adapter 正在创建/连接；
- `ready`：连接正常且事件队列已持久化；
- `degraded`：事件校验或持久化失败，禁止发送消息和响应审批；
- `disconnecting`：正在停止 Adapter；
- `failed`：Adapter 创建或连接失败。

关键约束：

- 同一 Agent 的并发 connect 复用同一个连接 Promise，避免创建多个 Adapter；
- 同一 Agent 的并发 disconnect 复用同一个停止 Promise，disconnect 期间拒绝 reconnect；
- disconnect 先阻止新命令，再等待已开始的命令完成，之后才停止 Adapter；
- Provider 启动失败与 Event Store 降级分别记录；连接尚未完成时，事件恢复只能回到 `connecting`，不能提前提升为 ready；存储恢复也不能把最终启动失败提升为 ready；
- Adapter disconnect 失败时（包括启动失败后的 cleanup）保留实例与事件监听，标记 `disconnectFailed` 并允许显式重试；清理成功前禁止重新 connect；
- shutdown 期间等待正在进行的 connect、command 和 disconnect 生命周期收敛，不能在清理旧实例后再启动新 Provider；
- 每个 Agent 只接受与自身 `agentId` 和注册 Provider 一致的事件；
- 事件按单 Adapter 到达顺序串行 append，不允许后续事件越过失败事件；
- 事件 append 成功前不通知 UI/下游消费者；
- 断开时先停止 Adapter，再持久化最后的连接状态事件；
- 收到并持久化意外的 `connection.changed:disconnected` 后转入 `disconnected`；下一次 connect 先释放旧 Adapter 监听，再创建新实例；
- 成功停止 Adapter 并完成所有有效事件写入后，以 `fatalEventError` 与待写队列状态判断是否可清除 fatal validation latch，不依赖可能被存储故障覆盖的 `lastError`；清理成功后清空旧诊断并允许创建新实例；
- `approval.requested` 与断开状态事件的本地状态副作用按 Event ID 在单个 Agent 生命周期内只应用一次；重复投递仍走持久化幂等检查，但不能重新激活已完成审批或清除更新状态；
- 错误状态只暴露安全错误码，不暴露凭证或任意底层错误文本。

## 4. 审批边界

Supervisor 只接收 Provider 的审批请求，不负责决定是否允许。

`respondToApproval` 必须同时满足：

- 对应 `approval.requested` 事件已通过 Event Store；
- 请求仍处于当前 Agent 的 pending 集合；
- 调用方提供明确的 `accept / decline / cancel` 决策；
- 注入的 Permission/Approval 授权回调明确返回 `true`。

缺少授权回调、授权返回 false 或授权服务异常时均 fail closed。当前没有接入 Hub Permission Service 或 SwiftUI 审批 UI。

## 5.1 本地 SQLite Outbox 与 Hub Inbox 基础层（2026-11-24）

- Runtime：`SqliteEventStore` 使用 Node `node:sqlite`，WAL + `synchronous=FULL`；事务提交后 append 才 resolve；eventId 幂等、冲突检测、pending replay、显式 delivered ack。
- Hub：migration v4 新增 `agent_event_inbox`，唯一键为 `(agent_id,event_id)`，外键绑定 Agent/Provider；内部 `append_agent_event(connection,event,received_at=...)` 复核身份、事件类型、时间戳、序列号、payload 深度/大小与敏感字段，要求调用方负责事务提交。
- 测试：Runtime 使用真实临时 SQLite 文件验证 close/reopen、pending replay、幂等、冲突与 ack；Hub 使用隔离数据库验证 migration、FK、幂等、回滚与恶意 payload 拒绝。
- Agent Gateway：新增 migration v5（配对码、Agent 专属 Token、失败计数），由调度员现有成员 Token 创建配对码，Agent 使用一次性兑换得到的独立 events:write Token；新增 /agent/pair/create、/agent/pair/redeem、/agent/events、/agent/tokens/revoke HTTP 路由。Hub 只保存 Token 哈希；事件身份从 Token 派生，重复事件幂等，速率限制与 Inbox 写入同事务。详细契约见 M2-AGENT-EVENT-GATEWAY.md。
- 传输回执：`POST /agent/events` 返回 `accepted/duplicate + inboxSeq`，只表示 Hub Inbox 事务已提交，是 Runtime Outbox 的传输级 ACK；不表示 Hub 下游消费者已处理。
- 边界：当前仍未实现 Outbox 网络投递 Worker、下游消费 lease/ACK、Session cursor/replay；不得把现有存储与入口描述为端到端可靠传输。
- Node 兼容：Node 22.13+ 才无需 `--experimental-sqlite` 启动参数；Node 22 的 `node:sqlite` 仍为 experimental API，需持续 CI 验证，未来可替换底层实现而不改变 Outbox 接口。

## 5. 非目标与当前限制

本批不做：

- 不修改 `runtime/runtime.mjs` v1 主循环，不替换现有 Muse Runtime；
- 不启动真实 Codex/Gemini/OpenAI/Doubao Provider；
- 不实现自动重连、指数退避、Session 游标、事件 replay；
- 不实现 Outbox 网络投递 worker、远端 ack、消费租约、Session cursor/replay；
- 不接入 Agent Registry 读 API，不解除 M1c 真实数据库 shadow gate；
- 不接入 SwiftUI Activity/Approval 页面；
- 不在用户 Mac 上登录、运行或部署。

当前测试使用 fake Adapter 与 fake Event Store，只验证 Supervisor 的本地生命周期和边界行为。

## 6. 验收与后续顺序

本批验收：

- Adapter 事件先持久化再发布；
- Session 未登记时拒绝发送；
- 事件写入失败进入 degraded，阻断命令，恢复后可重试；
- 错误 Agent/Provider 身份事件被拒绝；
- 审批没有授权服务时拒绝；
- 并发 connect 只创建一个 Adapter；
- 并发 disconnect 只执行一次停止操作；
- disconnect 等待正在执行的消息命令完成后再关闭 Adapter；
- disconnect 失败时保留 Adapter 和监听，第二次 cleanup 可成功；
- 启动尚未完成时恢复事件只能保持 connecting；启动失败与事件存储失败同时发生时，重试写入不会错误恢复为 ready；
- shutdown 与 reconnect cleanup 重叠时，不会创建未被 shutdown 管理的新 Adapter；
- 审批授权等待期间 Agent 降级时，不向 Provider 发送审批决定；
- startup event drain 期间 Provider 意外退出时，connect 不会返回 ready；
- 成功清理无效事件来源后可重新连接，不会被旧 validation latch 永久锁死；
- 意外断开后可显式重新连接，旧 Adapter 监听被清理；
- disconnect 结束前持久化最终状态事件。

后续 M2 顺序：

1. 实现 Runtime Outbox 投递 Worker：按 Outbox 顺序发送，持久化失败次数与退避时间；只在 Hub 返回有效的 accepted/duplicate 持久化回执后标记本地事件 delivered。401/403 暂停并要求重新配对，429/5xx/网络错误按退避重试，协议/事件校验错误保持阻塞且不丢弃事件；
2. 实现 Hub Inbox 下游消费租约与 ACK：按 consumer + Agent 维护游标，限制批量拉取，租约到期可重新领取，ACK 只能推进已领取范围内的连续游标；
3. 加入崩溃、超时、重复投递、ACK 丢失、租约过期和重启故障注入，验证至少一次投递与消费语义；幂等键保持稳定；
4. 以 feature flag 将 Supervisor 接入新的 Runtime 入口，旧 Muse Runtime 保持回退；
5. 再接 Hub Permission/Approval Service 与 SwiftUI 人工审批闭环；
6. 完成 mock 断线/重启测试后，才进行真实 Provider 和 Mac 验收。
