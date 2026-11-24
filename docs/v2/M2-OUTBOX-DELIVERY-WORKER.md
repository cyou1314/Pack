# M2 Outbox Delivery Worker

状态：独立 Worker 与持久化重试已实现；尚未接入 RuntimeSupervisor、后台调度或生产 Runtime。

## 1. 传输流程

1. 从本地 SQLite Outbox 读取最早一条未 delivered 且已到重试时间的事件。
2. 按事件身份调用 Token Provider 获取 Agent 专属 events:write Token；Token 不写入 Outbox 数据库、不写日志。
3. 向 Hub 的 POST /agent/events 发送单条事件。
4. 只有收到合法的 accepted=true、duplicate 为布尔值且 inboxSeq 为正整数的回执，才将本地事件标记 delivered。
5. 若 Hub 已提交事件但 HTTP 响应丢失，Worker 重试同一 eventId；Hub Inbox 的 (agent_id,event_id) 唯一键返回 duplicate 回执，Worker 再完成本地 ACK。

这提供 at-least-once 投递尝试与 Hub 端幂等接收的基础，不等于恰好一次，也不代表下游消费者已处理事件。

## 2. 可靠性与重试

- Outbox 保存 delivery_attempts、next_attempt_at、last_error_code。
- 退避状态写入 SQLite，进程重启后保留；旧 Outbox schema 通过 additive migration 升级，不删除历史事件。
- 408、425、429、5xx 与网络/超时错误按指数退避重试；429 等响应优先遵循 Retry-After，最长 24 小时。
- 401/403 暂停 Worker，等待凭证恢复；其他 4xx、无效 Hub 回执、本地事件过大或无效事件暂停并保留事件，不自动跳过。
- 严格按 outbox_seq 发送；最早未完成事件尚未到重试时间时，后续事件不能越过，避免同一 Agent 的事件乱序。
- Worker 每次 runOnce() 最多发送一条，不内置定时器；调用方负责调度与生命周期。
- 同一个 Outbox 数据库文件只运行一个 Worker 实例；当前没有跨进程 claim lease。

## 3. 安全边界

- 远端 Hub URL 必须 HTTPS；仅 localhost、127.0.0.1、::1 允许 HTTP。
- 目标路径必须以 /agent/events 结尾，避免将 Agent Token 发往 MCP 或其他 Hub 路由。
- 禁止 HTTP 重定向，避免 Bearer Token 被转发到其他地址。
- 默认请求超时 15 秒，可配置为 100 ms 至 120 秒。
- 请求体最大 256 KiB，与 Hub Gateway 上限一致。
- Token 由调用方通过 getToken(event) 注入，只接受可打印 ASCII 值，不记录、不持久化。

## 4. 当前边界

- 目前仅有独立 Worker 和 SQLite Store；没有接入 RuntimeSupervisor 或旧 Muse Runtime。
- 没有自动后台调度、断线恢复策略、Session cursor/replay。
- Hub Inbox 下游消费 lease/ACK 尚未实现；HTTP accepted/duplicate 只是 Hub 持久化回执。
- 尚未完成进程崩溃、Hub 提交后响应丢失、Mac 睡眠/唤醒、凭证重新配对等端到端故障注入。
- 本阶段不在用户 Mac 上登录 Provider、创建凭证或部署服务。

## 5. 验收

- 有效 accepted 回执后才标记 delivered。
- duplicate 回执可完成本地 ACK。
- Hub 5xx、429、网络错误会持久化退避，重启后仍遵守重试时间。
- 最早事件退避期间，后续事件不能越过。
- 401/403 暂停且保留事件；resume 后可继续。
- 无效回执、非法 Token、超大事件不会被标记 delivered。
- 远端 HTTP、错误路由和重定向均不能泄露 Agent Token。
- 旧 Outbox schema 升级后，原 pending 事件仍可读取。
- 并发 runOnce 调用只触发一次实际 HTTP 请求。
