# M2 Agent Event Gateway：安全边界与 HTTP 契约

状态：**Agent 专属配对凭证、Hub 事件写入端点和基础测试已实现；尚未接 Runtime 投递 Worker，也不代表端到端事件传输完成。**

## 1. 安全原则

- Agent 事件身份不复用现有成员 Bearer Token、dispatcher Token 或 external Agent Token。
- 调度员使用现有成员 Token 创建配对码、撤销 Agent 事件 Token。
- 配对码是短期、单次使用凭证；兑换后返回独立的 Agent 事件写入 Token。
- Hub 只保存配对码与 Agent Token 的哈希；明文仅在创建/兑换响应中返回一次。
- Agent Token 固定 scope 为 `events:write`，只能提交事件，不能调用 Hub MCP 工具或管理 Agent。
- 每个 Token 绑定一个 Agent；Provider ID 由 Hub Registry 派生，不能由请求体选择其他 Agent/Provider。
- Token 撤销、过期、Agent 禁用/移除或 Provider 停用均 fail closed。
- 所有敏感凭证不得进入事件 payload、审计 detail 或错误回显。

## 2. HTTP 端点

所有路径挂载在 Pack Hub HTTP 服务上。

### 2.1 创建配对码

`POST /agent/pair/create`

认证：现有成员 Bearer Token，且角色必须为 `dispatcher`。

请求：

```json
{"agent_id":"agent-codex-01","ttl_seconds":600}
```

- TTL 范围 60–900 秒，默认 600 秒。
- 每个 Agent 仅保留一个未兑换且未撤销的有效配对码；创建新码会撤销旧码。
- 返回一次性 `pair_code` 与 `expires_at`。
- 数据库只保存 `code_hash`。

### 2.2 兑换配对码

`POST /agent/pair/redeem`

认证：无成员 Token；仅凭配对码兑换。

请求：

```json
{"code":"<one-time-pair-code>"}
```

成功返回一次性 Agent Token：

```json
{"token_id":1,"agent_id":"agent-codex-01","provider_id":"codex","token":"<agent-event-token>","scope":"events:write","expires_at":0}
```

- 配对码只可兑换一次；重复、过期、撤销、锁定或未知码统一返回模糊错误。
- 默认 Agent Token 有效期 90 天。
- 失败计数全局滑窗：300 秒最多 20 次失败；超限返回 429。
- 已知失效配对码累计 5 次失败后锁定。
- Token 只保存哈希，不记录明文。

### 2.3 写入 Agent 事件

`POST /agent/events`

认证：Agent 专属 Bearer Token，scope 必须为 `events:write`。

请求体为一个 Event Envelope：

```json
{
  "eventId":"evt-unique-id",
  "eventType":"run.progress",
  "sequence":12,
  "occurredAt":"2026-11-24T08:00:00.000Z",
  "payload":{"progress":50}
}
```

- `agentId` / `providerId` 可省略；若提供，必须与 Token 绑定身份完全一致。
- Hub 从 Token 绑定关系派生 Agent / Provider 身份。
- Event ID 在 Agent 范围内幂等；相同 ID、相同内容返回 duplicate；相同 ID、不同内容拒绝。
- 默认每 Agent 每分钟最多 1200 条新事件；重复事件不消耗新事件预算。
- 单次请求最大 256 KiB；payload 最大嵌套深度 24。
- 不接受未知顶层字段、未知事件类型、无时区时间戳、非有限数字及凭证/私人记忆字段。
- 写入 Inbox 与 Token last-used 时间在同一事务中提交；超限回滚事件插入。

### 2.4 撤销 Agent Token

`POST /agent/tokens/revoke`

认证：现有成员 Bearer Token，且角色必须为 `dispatcher`。

请求：

```json
{"agent_id":"agent-codex-01","token_id":1}
```

- 指定 `token_id` 撤销单个 Token。
- `token_id` 省略或为 0 时撤销该 Agent 全部事件 Token。
- 撤销操作写审计；不回显 Token。

## 3. Event Envelope 白名单

允许事件类型：

- `connection.changed`
- `session.created`
- `message.delta`
- `message.completed`
- `run.started`
- `run.progress`
- `run.completed`
- `run.failed`
- `run.interrupted`
- `tool.started`
- `tool.completed`
- `approval.requested`
- `usage.updated`

允许顶层字段：`eventId`、`eventType`、`providerId`、`agentId`、`sequence`、`occurredAt`、`payload`，以及可选的 `roomId`、`sessionId`、`runId`。

Hub 递归拒绝 API Key、Token、Authorization、Cookie、Password、Credential、Secret、Private Key、Native/Private Memory 等敏感字段名。业务内容若需要引用凭证状态，只能提交非敏感状态枚举或安全元数据，不提交凭证本体。

## 4. 错误语义

| HTTP | 错误 | 含义 |
|---|---|---|
| 400 | `invalid_agent_pair_code` | 配对码无效/已失效，统一模糊响应 |
| 400 | `invalid_agent_event` / 具体安全错误码 | Event Envelope 校验失败 |
| 401 | `agent_event_unauthorized` | Agent Token 缺失、错误、过期、撤销或身份不可用 |
| 413 | `agent_event_too_large` | 请求体超过 256 KiB |
| 429 | `agent_pair_rate_limited` | 配对失败滑窗超限 |
| 429 | `agent_event_rate_limited` | Agent 新事件写入速率超限 |

响应不包含凭证、SQL 文本、堆栈或任意底层异常文本。

## 5. 当前不承诺的能力

- 没有 Outbox → Hub 网络投递 Worker。
- 没有远端 ACK、批量 ACK、租约、消费游标或断线补拉。
- 没有端到端至少一次/恰好一次传输保证。
- 没有把 Hub Inbox 事件自动转换为 UI 消息、任务、审批或 Provider Session 状态。
- 没有接入 `runtime/runtime.mjs` 生产入口；旧 Muse Runtime 不受影响。
- 没有在用户 Mac 上创建 Token、登录 Provider、运行或部署。

下一阶段先实现 Outbox 投递 Worker 与 Hub Inbox 消费/ACK 状态机，再做断线恢复与 Session Cursor。只有这些语义通过故障注入和重启测试，才允许接生产 Runtime。
