# M2：本地 Durable Event Store / Outbox

- 日期：2026-11-24
- 状态：实现已提交到独立分支，等待 CI 与代码审查。
- 实现：`runtime/agent-core/durable-event-store.mjs`
- 测试：`runtime/tests/durable-event-store.test.mjs`

## 1. 目标与边界

本切片为 Local Runtime 提供不依赖第三方 npm 包的本地持久化事件 Outbox，解决进程重启后已接收事件只存在内存中的问题。

本切片只负责本地落盘、幂等去重、待发送事件读取和确认记录。它不负责 Hub 网络传输、事件授权、自动重试调度或 Provider 游标恢复。

不修改 Hub 生产 SQLite Schema，不接入旧 Muse Runtime，不在用户 Mac 上部署。

## 2. 存储布局

每个 Runtime 实例使用独立的数据目录：

```text
<runtime-data>/events/
  .pack-event-store
  pending/
    <sha256(eventId)>.json
  acknowledged/
    <sha256(eventId)>.json
```

- 根目录与子目录权限为 `0700`；记录文件权限为 `0600`。
- 事件库的直接父目录必须预先存在，且其父级目录链由 Runtime 启动器负责创建和持久化；Event Store 只创建事件库根目录这一层，并同步该目录及其直接父目录，避免递归创建多个祖先目录却漏掉中间父目录的 fsync。
- 根目录包含版本标记，避免误把任意现有目录当作事件库并修改权限；每次打开都会重新 fsync 已存在的标记文件，确保上次初始化的部分成功可安全重试。
- 文件名由 JSON-escaped `eventId` 的 SHA-256 生成，不使用外部输入拼接路径；该编码保留孤立 UTF-16 surrogate 的身份，避免 UTF-8 替换字符导致不同 ID 哈希碰撞。
- Pending 文件保存事件、内容摘要和单调递增的本地写入序号。
- Acknowledged 文件只保留 eventId、内容摘要、序号和确认时间，不保留已确认的事件正文。

## 3. 写入与确认语义

### append(event)

1. 通过 Agent Event 契约重新校验事件。
2. 拒绝凭证字段、循环引用、非 JSON 类型、稀疏数组和超限事件。
3. 对规范化事件计算 SHA-256 内容摘要。
4. 写入同目录临时文件，设置 `0600` 权限并执行文件 `fsync`。
5. 使用同文件系统硬链接完成 no-overwrite 原子创建。
6. 对 Pending 目录执行 `fsync` 后才返回 `{ inserted: true }`。

同一 eventId 且内容一致时返回 `inserted: false`。同一 eventId 对应不同内容时返回 `event_id_conflict`，不静默覆盖。

默认单事件上限为 4 MiB，可在打开存储时调整，允许范围为 1 KiB 至 64 MiB。Payload 最大嵌套深度为 64，避免恶意或异常深层对象造成递归栈溢出。

### listPending({ limit })

按本地持久化序号返回最早的未确认事件，默认最多 100 条、上限 1000 条。调用方确认当前批次后，再读取下一批；重复读取允许发生，消费端必须按 eventId 幂等。

### acknowledge(eventId)

1. 先写入确认 Tombstone。
2. 对 Acknowledged 目录执行 `fsync`。
3. 再删除 Pending 正文并同步 Pending 目录。

若进程在第 2、3 步之间崩溃，重启后以确认 Tombstone 为准，自动清理残留 Pending 正文。确认记录保留 eventId 和内容摘要，使已确认事件在后续重试时仍能去重。

## 4. 崩溃恢复与错误策略

- 未完成的 `.tmp-*` 临时文件在打开存储时清理。
- 已提交文件必须是完整 JSON 记录；不完整或格式未知的正式记录会使存储打开失败，禁止静默跳过。
- 同一事件同时存在 Pending 与 Acknowledged 文件时，必须校验 eventId、内容摘要和序号一致；一致则以 Acknowledged 为准清理 Pending，不一致则 fail closed。
- 文件写入、目录同步或权限错误会返回安全错误码；Supervisor 应保持 degraded，不得把未确认写入视为成功。
- 若 append/ack 在 fsync 失败时返回错误，调用方应使用同一 eventId 重试；提交结果可能处于不确定状态，但幂等规则会识别已经落盘的记录。发现已有 Pending/Tombstone 时，使用它们之前会再次同步对应目录；已有 Tombstone 在删除 Pending 前必须再次 fsync。

## 5. 并发模型

同一 DurableEventStore 实例内部对 append、listPending、acknowledge 和 close 串行化。文件创建使用原子 no-overwrite 操作，重复 eventId 的并发写入不会覆盖已存在事件。

当前设计约定：一个 Runtime 进程独占一个事件目录。跨进程共享同一目录时，不保证不同 eventId 之间的全局序号顺序；生产 Runtime 必须维持单实例运行约束。Hub 端并发幂等由后续 Inbox 单独实现。

## 6. 安全与隐私

- 不记录 API Key、OAuth Token、CLI Session、Authorization Header 或原始 Provider 凭证。
- 事件继续通过 `createAgentEvent` 和敏感字段扫描。
- 文件权限按私有本地数据处理。
- 不把事件内容写入日志。
- 根目录路径必须是专用目录；根目录不能是文件系统根目录、符号链接或包含未知文件的未初始化目录。

## 7. 明确未完成事项

- 尚未把 DurableEventStore 注入 RuntimeSupervisor；当前 Supervisor 仍使用测试 FakeEventStore。
- 尚未实现 Hub Inbox / Outbox、Event Gateway、身份令牌或网络传输。
- 尚未实现后台发送循环、退避、Hub ACK 协议和死信队列。
- 尚未实现 Provider cursor、resume/replay 或消息 delta 补拉。当前 Codex Adapter 使用本地生成的随机 eventId；若 Provider 在重连后重新投递同一通知，只有后续引入稳定 Provider 事件标识/游标后才能跨重连去重。
- 尚未制定确认 Tombstone 的保留/清理策略；当前 Tombstone 长期保留，避免过早失去幂等能力。
- 尚未在真实 macOS 用户数据目录执行恢复、权限或磁盘故障演练。

## 8. 验收

- [ ] append 完成后，关闭并重新打开存储仍能读到 Pending 事件。
- [ ] 子进程写入并直接退出后，父进程重新打开可恢复事件。
- [ ] 相同 eventId 重复写入不会产生第二条记录。
- [ ] 相同 eventId、不同事件内容必须报冲突。
- [ ] 确认后重启不再返回事件，且 eventId 仍被去重。
- [ ] 确认中途崩溃留下 Pending + Acknowledged 时，恢复以 Acknowledged 为准。
- [ ] 临时文件可清理，正式损坏记录必须 fail closed。
- [ ] 目录/文件权限正确，符号链接路径被拒绝；缺失的直接父目录会被拒绝，不递归创建未同步祖先。
- [ ] Marker fsync、Pending fsync、Acknowledged fsync 失败后重试仍保持可恢复。
- [ ] 非 JSON、敏感字段、超限和过深嵌套事件被拒绝。
- [ ] 运行时语法、单元测试、Hub 回归和 macOS 构建通过。
