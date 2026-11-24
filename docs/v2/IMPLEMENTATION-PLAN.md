# Pack v2 迁移与实施计划

## 1. 迁移原则

- main 保持可运行。
- 新功能先在独立分支开发。
- 不在第一轮修改生产数据库。
- 不删除现有 members、rooms、messages、tasks、runs、audit、memory 和 attachments。
- 不改变现有外部 Agent 的房间绑定与安全边界。
- 先双读/影子验证，再切换读写路径。
- 每个迁移有备份、前置检查、回滚步骤和验收记录。

## 2. 现有能力保留

| 现有模块 | v2 处理 |
|---|---|
| Hub FastMCP + SQLite WAL | 保留，逐步模块化 |
| members | 作为兼容身份层，逐步映射到 agents |
| rooms / messages | 保留为房间和消息基础 |
| tasks / runs | 保留状态机，增加 workflow 关联 |
| approvals / rules | 保留，统一到 Permission Service |
| egress rules | 保留，扩展到工具和资源权限 |
| member memory | 保留成员私有边界 |
| room summaries | 迁移为共享上下文来源 |
| desktop_lock | 保留，升级为桌面执行租约 |
| Muse ego Runtime | 包装为 LegacyMuseAdapter |
| pack-agent.mjs | 保留为 ExternalMCPAdapter / CLI |
| Muse4AI Bridge | 保留兼容期，按既定 E2 决策单独评估 |

## 3. 实施阶段

### M0：Agent Core Foundation

- 新增 v2 文档。
- 新增 Agent Adapter 契约和注册表。
- 新增 Context Package 白名单构建。
- 新增 Runtime 契约测试并纳入 CI。
- 不改 Hub Schema 和现有 Runtime 主循环。

回滚：删除新目录与文档即可，不影响 v1 数据。

### M1：Agent Registry 数据层

- 设计 providers / agents / connections / sessions。
- 明确 members 与 agents 的稳定 ID 映射。
- 新建增量迁移。
- 为旧成员生成兼容映射，不改变原 token。
- 影子读取比对旧成员与新 Agent 视图。
- M1a 增量 schema/legacy backfill 已通过 PR #21 合入；M1b 只读 Shadow CLI 通过 PR #22 合入，PR #23 补齐并发迁移和 CLI 只读/敏感字段测试。CLI 仅手动读取指定数据库，尚未对用户 Mac 数据库执行。

回滚：关闭新读路径，保留新增表，不删除旧数据。

### M2：Local Runtime 与事件网关

- 建立 Runtime Supervisor。
- 建立事件 Inbox/Outbox。
- 建立 Session Manager。
- 加入幂等、游标、断线恢复。
- 现有 Muse Runtime 通过兼容 Adapter 接入。

**2026-11-24 Supervisor 审查修复：** Codex Review 指出的两个 P2 生命周期问题已修复并补回归：fatal validation latch 清理不再依赖可被持久化异常覆盖的 `lastError`；审批/断开事件状态副作用按 Event ID 在 Agent 生命周期内去重，防止重复 `approval.requested` 恢复已完成审批。两个原始 review thread 已回复并解决；Runtime 测试、Hub 回归和 macOS 构建通过。

**2026-11-24 第五个切片（Outbox Delivery Worker）：** 新增 AgentEventDeliveryWorker，按 Outbox 顺序每次投递一条事件到 /agent/events；新增 SQLite delivery_attempts、next_attempt_at、last_error_code，并以 additive migration 保留旧 Outbox 数据；支持 408/425/429、5xx、网络错误的持久化退避与 Retry-After；401/403、其他 4xx、非法回执和超大事件暂停但不丢弃；仅在 Hub 返回合法 accepted/duplicate + inboxSeq 后标记本地 delivered。增加旧 schema 升级、重试跨重启、严格顺序、重复回执、授权暂停、请求大小、URL/TLS 和并发单飞测试。**当前 Worker 仍为独立 runOnce()，尚未接入 Supervisor、自动调度或生产 Runtime；Hub Inbox 下游消费 ACK/租约与 Session Cursor 仍未实现。** 详细边界见 M2-OUTBOX-DELIVERY-WORKER.md。

**2026-11-24 Hub Inbox Consumer Lease / ACK（设计与内部实现）：** PR #44 建立协议设计；PR #45 新增 additive migration v6，为每个 Consumer + Agent 建立独立连续 cursor、限时 Lease 与 Lease items，并为 `(agent_id,inbox_seq)` 增加范围索引。内部服务支持有条数/字节上限的顺序 claim、每个 Consumer + Agent 单一 active Lease、部分 ACK、幂等重试、release 与过期回收。所有 claim/ACK/release/cursor 操作都接收 Hub trusted resolver 签发的 issuer-owned ConsumerPrincipal，并从 principal 派生 consumer_id；Lease capability 与 Consumer 身份双重校验。completed ACK 在 24 小时窗口内只允许只读重试。**PR #45 已实现服务层、migration v6、真实 SQLite 测试与合约快照；完整 Hub / Runtime / macOS CI 通过，Codex 复审未发现重大问题，已标记 Ready for review。** 仍不新增公开端点、不做业务副作用、不接生产 Runtime。下一切片先完成可信 Hub Session 绑定与原子 Event Projection 设计，明确 Agent 自报 roomId 不可作为授权依据；见 M2-SESSION-BINDING-PROJECTION-DESIGN.md。

**2026-11-24 Session Binding / Event Projection（PR #46 设计审查中）：** 源码审计确认不能信任 Agent Event 自报的 `roomId`；旧 `messages` 是 member-authored，旧 `runs` 是 task-bound，均不得复用承载 Provider Agent 输出。下一阶段由 Hub 签发 `hubSessionId` 并在数据库中绑定 Agent + Room，Provider `sessionId` / `runId` 独立保留；Agent Session Message / Run 使用专用表；投影副作用、receipt、Lease ACK 与 cursor 同事务提交。PR #46 已补齐 Hub-first 能力协商、混合版本回滚、Provider Session write-once / late-event fencing、blocked event 的 dispatcher/owner 审计 skip，以及 Run 终态消息收尾规则。当前仅为设计 PR，不改代码或生产行为；实现拆分为 M2a Session Binding → M2b Atomic Consumer Apply → M2c Session Projection，之后再单独做 Read API/UI 和 Runtime Wiring。详见 M2-SESSION-BINDING-PROJECTION-DESIGN.md。

**2026-11-24 第四个切片（Agent Event Gateway）：** 新增 Hub migration v5：一次性配对码、Agent 专属 events:write Token、失败计数与索引；新增配对码创建/兑换、Token 撤销、Agent Token 认证与带身份绑定的事件写入服务；新增 /agent/pair/create、/agent/pair/redeem、/agent/events、/agent/tokens/revoke HTTP 路由。明文 Token 只在兑换响应出现一次，数据库只存哈希；Agent / Provider 身份由 Token 绑定关系派生；写入与 Token last-used 在同一事务中提交；事件有 256 KiB 请求上限、1200 条/分钟默认限流、重复事件不消耗新事件预算。新增凭证/配对/限流/幂等测试。**第五个切片已在此 Gateway 之上加入独立 Outbox Delivery Worker，但尚未接入生产 Runtime；下游消费 ACK/租约、Session Cursor 与断线 replay 仍未实现。** 详细协议见 M2-AGENT-EVENT-GATEWAY.md。

**2026-11-24 第三个切片（Hub Inbox 基础层）：** 新增 Hub migration v4 `agent_event_inbox`，以 `(agent_id,event_id)` 唯一键保存远端事件信封；新增 `hub/services/agent_events.py`，验证 Agent/Provider 绑定、事件类型、序列号、时间戳、payload 深度/大小与敏感字段；相同事件幂等返回原 inbox 序号，重复 ID 但内容不同则拒绝。新增隔离数据库测试，覆盖迁移、外键、事务回滚、跨 Agent 去重边界、敏感字段拒绝。**后续 Agent Event Gateway 已加入独立分支：Hub 仅接受 Agent 专属 events:write Token，dispatcher 通过单次配对码签发，仍不复用成员/dispatcher Token；Gateway 的 `accepted/duplicate + inboxSeq` 仅是 Hub Inbox 提交回执，不代表下游消费完成；Gateway 也不代表投递 Worker、下游消费 ACK/租约或生产 Runtime 已完成。**

**2026-11-24 第二个切片（本地 Durable Outbox）：** 新增 `runtime/agent-core/sqlite-event-store.mjs` 的 `SqliteEventStore`，使用 Node `node:sqlite` / SQLite WAL / `synchronous=FULL`，以事务提交作为 append 成功边界；支持 eventId 幂等、同 ID 不同 payload 冲突拒绝、pending 顺序读取、显式 delivered ack、统计与关闭 checkpoint。新增真实临时 SQLite 文件的 reopen/replay/idempotency/ack/closed-state 测试，并从 Agent Core index 导出。**当前仍未接入 Supervisor 默认构造、Runtime 生产入口或 Hub 网络投递 Worker**；调用方仍需显式注入。Node 22.13 起 node:sqlite 无需实验 flag，但 Node 22 中 API 仍标记 experimental，必须继续由 Node 22 CI 和 macOS 构建验证，且将数据库实现封装在独立模块，避免 API 泄漏到业务层。

**2026-11-24 首个切片：** `runtime/agent-core/supervisor.mjs` 已加入 Adapter 生命周期、Session create/resume/interrupt、事件身份/敏感字段校验、注入式 Event Store、事件写入失败降级、审批授权复核、并发生命周期串行化、disconnect 失败保留实例与 shutdown race 防护。当前只用 fake Adapter / fake Event Store 测试，未接入 `runtime/runtime.mjs`，也没有生产 Event Store、Hub Inbox/Outbox、Event Gateway、游标/ack/replay 或自动重连。详细边界见 `M2-RUNTIME-SUPERVISOR-DESIGN.md`。

回滚：切回旧 Runtime 与旧 Hub 同步路径；新 Supervisor 尚未接入生产入口，不影响 v1。

### M3：Provider Adapters

按 Codex、Gemini、OpenAI、Doubao、Muse 的可行性验证结果逐个实现。

每个 Adapter 独立 PR、独立测试，不允许一个 PR 同时引入多个未验证 Provider。

### M4：群聊与 Orchestration

- 新增 Agent 消息路由。
- 分离 @ 回复与任务创建。
- 增加 Workflow / 子任务 / 依赖。
- 现有任务状态机继续负责任务生命周期。

回滚：关闭 Workflow 功能开关，旧任务仍可执行。

### M5：Computer Use

- 先 Browser Controller。
- 再 Desktop Controller。
- 复用桌面锁和审批规则。
- 本机权限与 Hub 权限双重检查。

回滚：关闭 Controller，Agent 群聊与 API 功能不受影响。

### M6：App 产品化

- 新增 Agent 管理。
- 新增添加 Agent 向导。
- 新增 Workflow 与 Activity。
- 保留旧页面作为兼容入口，验收后再移除。

## 4. 任务优先级

### P0
- Codex App Server 本机连接验证。
- Gemini CLI Headless 连接验证。
- OpenAI Responses API 验证。
- Doubao Ark API 验证。
- Muse Custom Connector 双向能力验证。
- 统一 Agent / Session / Event 模型。
- Hub 与 Local Runtime 稳定性。
- API Key 本地 Keychain 管理。

### P1
- Provider Adapters。
- 实时群聊。
- Agent 自动协作。
- 工作流与独立审核。
- Agent 管理页面和添加向导。

### P2
- Browser Controller。
- Desktop Controller。
- 完整 Computer Use。
- 安装、升级、诊断与发布体验。

## 5. 每个阶段的完成条件

1. 有明确范围和非目标。
2. 有代码和测试。
3. 有针对性 CI 证据。
4. 有失败恢复验证。
5. 有权限与数据边界验证。
6. 有文档和迁移说明。
7. 有独立 PR，不直接合并 main。

## 6. 架构决策记录

### ADR-001：产品定位升级

Accepted：Pack 从多 Muse 群聊与任务调度工具升级为专业级 Mac AI 工作台。

### ADR-002：双模式 Agent 接入

Accepted：同时支持原生个人 Agent / 本机 Runtime 和官方 API。添加 Agent 时由用户选择接入方式。

### ADR-003：记忆默认隔离

Accepted：Provider 原生记忆由 Provider 自己管理；Pack 只维护明确授权的 Agent 元数据和房间共享记忆。

### ADR-004：控制平面与执行平面分离

Accepted：Hub 管理协作状态、权限和审计；Mac Local Runtime 负责本地 Agent、浏览器和桌面执行。

### ADR-005：渐进迁移，不重写 v1

Accepted：保留 Hub、SQLite、任务状态机、审批、审计、外部 Agent 安全边界和现有 Muse Runtime。

### ADR-006：能力声明必须基于验证

Accepted：Adapter 只声明经过测试的能力。原生账号、API、MCP Connector 和 Browser Bridge 必须在 UI 中明确区分。

### ADR-007：先建立 Agent Core，再接入 Provider

Accepted：先建立 Descriptor、Registry、Event Envelope、Context Package 和测试入口；真实 Provider Adapter 逐个验证、逐个 PR。

### ADR-008：不在第一轮迁移 Hub Schema

Accepted：首轮 Agent Core 不改生产 SQLite Schema。Agent Registry 数据迁移在后续独立批次实施。

## 7. 验收清单

- [ ] Codex 与 Gemini 可作为独立 Agent 加入同一房间。
- [ ] @ Agent 能触发正确的 Session。
- [ ] 普通聊天不自动生成任务卡。
- [ ] 多 Agent 回复可流式显示。
- [ ] Provider 断线后可恢复或明确标记不可恢复。
- [ ] 私人记忆不会进入 Room Context。
- [ ] API 凭证不进入 Hub、仓库、日志或事件。
- [ ] 自动协作有预算、超时、循环上限和人工中止。
- [ ] 浏览器和桌面操作有独立权限与审计。
- [ ] 旧任务、审批和 external 安全隔离无回归。

## 8. 2026-11-23 实施状态校准

- **M0 Agent Core Foundation：** Descriptor / Registry / Event Envelope / Context Package / Codex App Server Adapter 与 mock contract tests 已存在；这只代表基础骨架完成，不代表已装配到生产 Runtime。
- **M1 Agent Registry：** M1a/M1b 已合入 v2 Foundation（PR #21/#22/#23）；并发迁移、只读 CLI 和敏感字段输出测试通过 CI。PR #24 已将 SQLite quick_check / foreign_key_check 加入 Shadow readiness；PR #34 已增加 task hot-path migration v3，M1c Shadow CLI 会核对当前全部 migration checksum。真实 Mac 数据库尚未检查，M1c API 尚未开放。
- **M2 Runtime / Event Gateway：** 首个 Supervisor 切片已实现，负责 Adapter 生命周期、Session 操作、事件校验/串行 append 和审批 fail-closed；但 `runtime/runtime.mjs` 仍是 Muse ego v1 主循环，Supervisor 未接入生产入口。Event Store 目前只有注入式接口，没有生产 durable Inbox/Outbox、Hub Gateway、游标/ack/replay、自动重连或 Hub/App 审批闭环。
- **M3 Provider Adapter：** Codex Adapter 有 mock 测试，但真实 Mac App Server 登录、Session/Turn、断线恢复和审批闭环未验收；ChatGPT、Doubao 等 Adapter 仍按 Provider Matrix 单独验证。
- **安全门槛：** 在 M2 Event Gateway、Hub Permission/Approval/Audit 与 SwiftUI 人工审批闭环完成前，不开放 Codex command/file 高风险工具自动执行。
- **执行顺序：** M1a additive migration → M1b shadow read → M1c read-only API 评估 → M2 Supervisor/Session Manager/Inbox-Outbox/Event Gateway → M3 真实 Provider 验收。真实 Mac 部署继续后置。


### M1a：Agent Registry 增量 schema 与 legacy backfill（PR #21 已合并）

- 已实现版本化 migration ledger、checksum、BEGIN IMMEDIATE 与失败回滚。
- 已实现 providers / agents / connections / sessions 表、外键与索引。
- 已实现旧 member 稳定映射；保留原 members 行、token hash、任务/消息引用与私有记忆。
- 已启用逐连接 SQLite foreign key enforcement，并加入测试和 schema contract snapshot。
- M1a 已合入 v2 Foundation（PR #21）；M1b shadow CLI 已合入（PR #22），并发迁移/只读 CLI 安全测试已通过 PR #23。PR #24 已合并 SQLite 完整性与 FK 预检；PR #34 已合并 migration v3 task indexes；尚未在用户 Mac 执行。


### M1a/M1b 已合入 / M1b 完整性预检补强

- M1a PR #21 已 squash 合入 `codex/pack-v2-foundation-20261121`，提交 `51128b954764b3e9e59d0e00eb802d24fea2684e`。
- M1a PR 的 Hub regression、SQLite/MCP snapshots、staging regression、Runtime tests、macOS installer/Swift release build/unit tests/app bundle 验证全部通过；Codex 最新代码审查无发现。
- M1b PR #22/#23 已合入：手动只读 Shadow CLI 不加 MCP 工具、不接入 member_list、不启用新读路径、不自动修复。PR #24 已补齐 quick_check / foreign_key_check；PR #34 已增加 task hot-path indexes；M1c API 仅完成评估草案，等待真实数据库备份副本上的 shadow report 通过后再实施。


### 2026-11-23 分支集成状态

- v2 Foundation 持续位于 codex/pack-v2-foundation-20261121；M1a/M1b 与后续安全、安装器、SQLite WAL、staging56、task index 等整改已分批合入，具体以该分支当前 HEAD 和各 PR CI 为准。
- Umbrella PR #6 仍是指向 main 的大范围 draft，GitHub 当前报告 mergeable_state=dirty。因此 Foundation 上已完成的整改尚未全部进入 main，禁止把 PR #6 当作可直接合并的发布包。
- 后续需单独做 main 基线差异/冲突审计和分批集成；在该审计完成前继续只在 Foundation 分支迭代，不部署 Mac。
- 注意：M1a 由 hub.init() 在更新后首次 Hub 初始化时自动应用；DMG 安装器本身不执行迁移。真实 Mac 发布前必须完成数据库备份、恢复演练和 shadow preflight。
