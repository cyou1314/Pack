# M2：Runtime Supervisor 首个实现切片

- 日期：2026-11-24
- 状态：**Supervisor 骨架与 mock 测试已实现；尚未接入生产 Runtime。**
- 目标：在接入更多 Provider 之前，先把 Adapter 生命周期、Session 操作、事件持久化边界和审批 fail-closed 规则收敛到一个可测试的控制对象。

## 1. 本批范围

新增 `runtime/agent-core/supervisor.mjs`，由 `RuntimeSupervisor` 统一管理：

- 通过既有 `AgentAdapterRegistry` 创建 Adapter 实例；
- 连接、断开和状态查询；
- Session create / resume / interrupt；
- 发送消息前检查 Agent 状态和 Session 归属；
- 验证 Adapter Event 的身份、类型与敏感字段；
- 按 Adapter 事件顺序调用注入的 Event Store；
- 只有 Event Store 成功确认后，才向下游订阅者发布事件；
- 事件写入失败时进入 `degraded`，阻止新的消息/工具动作，允许显式重试；
- `approval.requested` 必须经过注入的授权回调，授权完成后重新检查 Agent/Adapter/请求状态；缺少授权服务或授权不明确时拒绝，不自动 accept；
- 同一审批请求只允许一个并发响应。

## 2. Event Store 契约

Supervisor 构造时必须注入 `eventStore.append(event)`。该实现必须：

1. 以 `eventId` 幂等去重；
2. 只有在事件已持久化提交后才 resolve；
3. 返回 `{ inserted: boolean }`，重复事件返回 `inserted: false`；
4. 写入失败时 reject，Supervisor 保留待写队列并进入 `degraded`。

本批只定义并验证接口契约，**没有实现生产 Event Store、Hub Inbox/Outbox 或网络 Event Gateway**。因此不能将当前内存待写队列描述为崩溃安全的 durable outbox；生产接入前必须补齐真正的持久化实现。

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
- Provider 启动失败与 Event Store 降级分别记录；存储恢复不能把未成功连接的 Adapter 提升为 ready；
- 每个 Agent 只接受与自身 `agentId` 和注册 Provider 一致的事件；
- 事件按单 Adapter 到达顺序串行 append，不允许后续事件越过失败事件；
- 事件 append 成功前不通知 UI/下游消费者；
- 断开时先停止 Adapter，再持久化最后的连接状态事件；
- 收到并持久化意外的 `connection.changed:disconnected` 后转入 `disconnected`；下一次 connect 先释放旧 Adapter 监听，再创建新实例；
- 错误状态只暴露安全错误码，不暴露凭证或任意底层错误文本。

## 4. 审批边界

Supervisor 只接收 Provider 的审批请求，不负责决定是否允许。

`respondToApproval` 必须同时满足：

- 对应 `approval.requested` 事件已通过 Event Store；
- 请求仍处于当前 Agent 的 pending 集合；
- 调用方提供明确的 `accept / decline / cancel` 决策；
- 注入的 Permission/Approval 授权回调明确返回 `true`。

缺少授权回调、授权返回 false 或授权服务异常时均 fail closed。当前没有接入 Hub Permission Service 或 SwiftUI 审批 UI。

## 5. 非目标与当前限制

本批不做：

- 不修改 `runtime/runtime.mjs` v1 主循环，不替换现有 Muse Runtime；
- 不启动真实 Codex/Gemini/OpenAI/Doubao Provider；
- 不实现自动重连、指数退避、Session 游标、事件 replay；
- 不实现生产 Inbox/Outbox、Hub Event Gateway 或新增 SQLite migration；
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
- 启动失败与事件存储失败同时发生时，重试写入不会错误恢复为 ready；
- 审批授权等待期间 Agent 降级时，不向 Provider 发送审批决定；
- 意外断开后可显式重新连接，旧 Adapter 监听被清理；
- disconnect 结束前持久化最终状态事件。

后续 M2 顺序：

1. 实现崩溃安全的本地 Outbox / Hub Inbox 存储与事件幂等键；
2. 定义 Runtime ↔ Hub 的受限身份与 Event Gateway，不复用宽权限 dispatcher token；
3. 加入 Session 游标、ack、断线补拉和重复事件 replay；
4. 以 feature flag 将 Supervisor 接入新的 Runtime 入口，旧 Muse Runtime 保持回退；
5. 再接 Hub Permission/Approval Service 与 SwiftUI 人工审批闭环；
6. 完成 mock 断线/重启测试后，才进行真实 Provider 和 Mac 验收。
