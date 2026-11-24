# M2 Hub Inbox Consumer Lease / ACK Design

状态：设计基线；Hub 内部服务层与 migration v6 已在 PR #45 实现，正在进行完整 CI 与审查验收。当前仍未合入 Foundation、未暴露 HTTP/MCP 消费端点、未接入业务副作用或生产 Runtime。

## 1. 目标与边界

为 Hub 已持久化的 `agent_event_inbox` 增加可恢复的下游消费协议，使每个逻辑 Consumer 能独立、按 Agent 顺序读取事件，并通过有期限的 Lease 和显式 ACK 推进自己的连续消费游标。

本阶段只定义 Hub 内部服务与 SQLite 契约。除非另有独立安全审查，不新增公开 HTTP/MCP 消费端点，不复用 dispatcher/member Token 或 Agent 的 `events:write` Token，不接入 UI 消息、任务、审批或 Provider Session 的业务副作用。

不承诺跨不同 Consumer 的全局顺序，也不承诺恰好一次业务处理。目标是至少一次交付给 Consumer + 幂等业务处理：Consumer 可能重复收到事件，业务副作用必须以稳定的 `(consumer_id, agent_id, event_id)` 或 inbox_seq 幂等。

## 2. 核心数据模型

在现有 Hub migration v5 后新增 additive migration v6，保留现有 Inbox 行和 v4/v5 migration checksum。

建议新增三张表：

### `agent_event_consumer_cursors`

主键：`(consumer_id, agent_id)`

字段：
- `consumer_id`：Hub 内部注册的稳定逻辑消费者标识，非任意客户端自报后即获授权。
- `agent_id`：被消费 Agent。
- `acked_inbox_seq`：该 Consumer 对该 Agent 已确认的最大连续 Inbox 序号，初始为 0。
- `updated_at`：游标更新时间。

游标按 Consumer + Agent 隔离。不同 Consumer 互不推进对方游标。游标以 Hub 的 `inbox_seq` 为准，不以 Agent 提供的 `event.sequence` 为准，避免 Agent 重启或序号跳变破坏 Hub 消费连续性。

### `agent_event_consumer_leases`

字段：
- `lease_id`：随机不可预测的 Lease ID，主键。
- `consumer_id`、`agent_id`：绑定唯一消费范围。
- `lease_token_hash`：Lease capability 的哈希；明文只在成功 claim 响应中返回一次。
- `from_inbox_seq`、`through_inbox_seq`：本次领取批次的最小/最大 Inbox 序号。
- `expires_at`、`created_at`、`closed_at`：Lease 生命周期。
- `acked_through_inbox_seq`：该 Lease 已确认的最大前缀位置。
- `completed_at`、`ack_retry_until`：完成时间与 ACK 响应丢失后的只读幂等重试截止时间。
- `status`：active / completed / expired / released。

同一个 `(consumer_id, agent_id)` 最多有一个 active Lease，使用部分唯一索引强制。过期 Lease 可被新 claim 原子回收；expired/released Lease 不再允许推进 cursor。completed Lease 保留 Token 哈希和 ACK 项状态，只允许对已确认前缀做无副作用的幂等 ACK 重试。

### `agent_event_consumer_lease_items`

主键：`(lease_id, inbox_seq)`，并为 `inbox_seq` 建索引。

增加 `acked_at REAL`，ACK 后保留已确认的 item 行，不立即删除。显式记录本次 Lease 实际领取的 Inbox 行，避免全局自增序号中其他 Agent 的记录形成空洞后，被误判为本 Lease 已领取内容。Lease items 只引用已有 Inbox，不复制事件 payload；保留 ACK 标记用于审计和完成态 ACK 重试幂等。

## 3. 服务接口契约

建议拆为独立服务模块，不把 SQL 和事务细节继续堆入 `hub.py`：

- `claim_agent_events(context, principal, agent_id, *, limit, lease_seconds, max_bytes)`
- `ack_agent_events(context, principal, lease_id, lease_token, *, through_inbox_seq)`
- `release_agent_event_lease(context, principal, lease_id, lease_token)`
- `get_agent_event_consumer_cursor(context, principal, agent_id)`

所有写操作使用 Hub 的同一连接锁与 `BEGIN IMMEDIATE`，由服务函数完整拥有事务；异常必须 rollback。

v6 首版不增加动态 Consumer 注册表：Consumer 使用 Hub 代码内定义的稳定 ID 常量。`principal` 是不可变、不可由请求数据反序列化构造的内部身份对象，只能由 Hub 可信调用边界的 resolver/factory 创建；对象绑定唯一 `consumer_id`，不能由调用方在函数参数中另传或覆盖。claim、ACK、release 和 cursor read 全部接收该 `principal`，并在读取事件或修改状态前重新执行 principal → consumer_id → agent_id 的授权检查。claim 从 principal 派生 consumer_id，绝不接受调用方自报 consumer_id；ACK/release 也不得从 Lease 行反向推断调用者身份。Lease Token 只是第二层 capability，不能替代独立 Consumer 身份认证。服务函数只允许 Hub 内部模块调用，不暴露 MCP/HTTP 入口；未来若确有动态 Consumer 管理需求，再单独设计注册、撤销、审计与迁移。

### Claim

1. 验证 principal 是 Hub trusted resolver/factory 创建的 ConsumerPrincipal；从 principal.consumer_id 派生 Consumer 身份，验证其有权读取 agent_id，再校验 Agent 存在、批量条数和字节上限；拒绝 bool 伪装整数。请求参数中不存在可供调用方伪造的 consumer_id。
2. 原子读取或创建 cursor。
3. 若存在有效 active Lease，返回明确的 lease_busy，不得创建第二个并发 Lease。
4. 若旧 Lease 已过期，原子标记 expired 并使旧 Token 失效。
5. 按 `inbox_seq ASC` 读取该 Agent 中 `inbox_seq > acked_inbox_seq` 的最早事件。
6. 批量限制同时受条数与 UTF-8 编码后总字节数约束；单条事件超过批次上限时返回明确阻塞错误，不跳过该事件。
7. 在同一事务写入 Lease 与 Lease items，再返回事件、Lease Token、过期时间和当前 cursor。
8. 空批次不创建空 Lease，返回 `empty=true` 和当前 cursor。

Lease 时长采用有上下限的配置，初始建议 30 秒默认、5–300 秒范围。批量条数初始上限 100；默认批次总字节上限 1 MiB。完成态 ACK 的只读重试窗口初始固定为完成时间后 24 小时（`ack_retry_until = completed_at + 24h`），实现时可配置但必须有明确上限与测试。最终常量以实现和测试为准。

### ACK

1. 先校验可信 `principal` 已被授权为该 Lease 所属的 `consumer_id`，且仍可读取该 `agent_id`；再校验 Lease Token 哈希及 Lease 状态。不得仅凭 Lease ID + Token 推断 Consumer 身份。
2. `through_inbox_seq` 必须是当前 Lease items 中实际存在的序号，且严格大于当前 cursor；不能 ACK 未领取的序号或跨 Lease 推进。
3. ACK 表示该 Lease 中从当前 cursor 之后到指定事件（含）已全部完成；Consumer 不得在前序事件未处理完成时 ACK 后续事件。
4. 在一个事务内把当前 Lease 中 `inbox_seq <= through_inbox_seq` 的所有 item 标记为已 ACK，并将 cursor 与 `acked_through_inbox_seq` 推进到该序号。由于目标必须是本 Lease 的实际 item，且 claim 只取该 Agent 在旧 cursor 之后的连续前缀，这不会跨过该 Agent 尚未领取的 Inbox 事件。
5. 全部 Lease items 已确认时关闭 Lease；部分 ACK 时保留未确认 items，原 Lease 继续有效，直到完成、释放或过期。
6. 同一 ACK 的网络重试必须幂等：若目标 item 已 ACK 且不大于当前 cursor，返回当前 cursor，不重复推进或执行副作用。completed Lease 只接受这种已 ACK 前缀的只读重试；即使原 `expires_at` 已经过期，也允许在独立的 `ack_retry_until` 窗口内通过 principal + Token 双重认证返回只读结果。该路径绝不允许推进 cursor。
7. active Lease 到期或 Lease 被释放后，Token 不得推进 cursor；调用方应读取 cursor 并重新 claim。新 Lease 创建后，旧 Lease Token 不能确认新 Lease 的 item。completed Lease 的只读 ACK 重试是唯一例外，且仅对已 ACK 前缀有效。

### Release / Expiry

- Release 只释放当前 Lease，不推进 cursor；未 ACK 事件可被重新领取。
- Lease 到期不自动推进 cursor；下一次 claim 才回收旧 Lease。
- completed Lease 的已 ACK item 与 Token 哈希必须保留一段明确的幂等窗口；本阶段不做自动清理。未来增加保留期清理时，必须先确定客户端最大 ACK 重试窗口，并保证清理不影响 cursor。
- 任何失败或进程崩溃都不得隐式 ACK。
- 不自动跳过毒性事件；后续可增加人工隔离/死信流程，但必须有审计、显式授权和可恢复路径。

## 4. 顺序、重复与业务副作用

- 每个 Consumer + Agent 按 `inbox_seq` 顺序消费；后续事件不能越过当前 Lease 中未 ACK 的前序事件。
- 同一个事件可能因 Lease 过期、Consumer 崩溃或 ACK 响应丢失而重复交付。
- Hub 的传输 ACK 与 Consumer 的业务完成 ACK 是两个不同阶段。
- Consumer 必须先以 Event ID / inbox_seq 做业务幂等，再 ACK；不得先 ACK 再执行业务副作用。
- 若业务副作用与 cursor 更新落在同一个 Hub SQLite 数据库，应尽可能同事务提交；若副作用在外部系统，必须使用其幂等键或本地事务 Outbox，不能声称跨系统 exactly-once。
- 多 Agent 之间不保证顺序；同一 Consumer 的不同 Agent 可独立 claim，以后再决定是否需要全局调度器。

## 5. 安全与资源限制

- Lease Token 使用密码学安全随机数，数据库只存哈希；不得写入日志、审计 detail 或错误消息。
- Consumer 身份授权与 Lease capability 分离；Lease Token 不是新的通用 Hub API Token。
- 服务层拒绝未注册 Consumer、未授权 Agent、跨 Agent Lease 操作和类型混淆输入。
- 批次同时限制条数、总 UTF-8 字节数和单条大小；深层 JSON 与事件敏感字段仍沿用现有 Inbox 验证。
- 读取和 ACK 不修改 Agent Event Token 的 last_used，也不接受 Agent `events:write` 凭证。
- 不新增任意文件路径、URL fetch 或外部网络调用。

## 6. 测试与故障注入验收

### Migration / 数据兼容
- v5 数据库升级到 v6 后原 Inbox、Agent、Provider、Token 和历史 migration checksum 不变。
- v6 重复初始化与并发初始化安全；失败时整批回滚。
- 外键、唯一约束、索引和 SQLite query plan 有测试。

### Lease 生命周期
- 空 Inbox 不创建 Lease。
- 两个并发 claim 只有一个成功。
- Lease 过期后可重新领取同一未 ACK 事件；旧 Token 失效。
- Release 不推进 cursor。
- 部分 ACK 后只重新交付未确认的剩余事件。
- 不同 Consumer 的游标相互隔离；不同 Agent 的 Lease 相互隔离。
- Consumer A 的 principal 不能通过伪造 consumer_id claim Consumer B 的事件；Lease Token 不正确、principal 与 Consumer 不匹配、跨 Agent、active Lease 过期或 Lease 释放后推进 ACK 均失败；completed Lease 在 `ack_retry_until` 内仅允许相同已确认前缀的只读幂等重试。

### ACK 连续性
- 只能 ACK 当前 Lease 实际领取的事件序号。
- 不能 ACK 未领取、未交付、超出 Lease 范围或不属于该 Agent 的 inbox_seq。
- ACK 不能倒退；重复 ACK 幂等。
- 同一批次中只允许按前缀确认，不能越过未完成事件。
- 事务中途异常不产生半推进游标。

### 故障注入
- Consumer 处理事件后、ACK 前崩溃：Lease 过期后事件重新交付，业务幂等避免重复副作用。
- ACK 已提交但 HTTP/调用响应丢失（包括原 Lease 的 expires_at 已过）：在 ack_retry_until 内重试返回只读 cursor 结果，不倒退、不重复业务副作用；窗口外重试要求重新读取 cursor。
- Consumer A 的 principal 即使持有 Consumer B 的 Lease ID/Token，也不能 ACK 或 release B 的 Lease。
- ACK 事务提交前崩溃：cursor 不变，事件可重新领取。
- 多进程/多连接并发 claim、ACK、expiry 回收无双重有效 Lease。
- 超大单条事件阻塞时不跳过、不推进 cursor。

### CI / 部署门槛
- Hub 单元与真实 SQLite 文件集成测试。
- Migration / SQLite contract snapshots。
- 默认 staging 与 staging56 回归。
- macOS 原生 CI 的 SQLite migration/query-plan smoke test。
- 本阶段不改生产数据库、不创建真实 Consumer Token、不连接用户 Mac。

## 7. 分批实施顺序

1. 先实现 v6 migration、cursor/lease 服务与隔离 SQLite 测试。
2. 加入 claim / partial ACK / release / expiry 的 HTTP 级或服务级并发测试；本批仍不暴露公开 API。
3. 加入进程重启、ACK 丢失、Lease 过期和业务幂等故障注入。
4. 明确首个内部 Consumer 的身份、事件映射和事务边界。
5. 只有消费协议通过验证后，才设计受限读取端点或 Supervisor 集成。
6. 最后再接 Session cursor/replay、UI 消息/任务/审批状态映射。
7. 生产 Runtime feature flag 与旧 Muse Runtime 回退单独立项；真实 Mac 验收继续后置。

## 8. 明确不做

- 不在本批把 Inbox 事件自动变成 UI 消息、任务、审批或 Session 状态。
- 不在本批接入生产 Runtime、旧 Muse Runtime 或 Provider 登录。
- 不新增公开 MCP 工具或把消费凭证暴露给 Agent。
- 不清理/压缩历史 Inbox 数据，不做自动死信跳过。
- 不声称 exactly-once；只在幂等前提下提供可恢复的至少一次消费交付。
