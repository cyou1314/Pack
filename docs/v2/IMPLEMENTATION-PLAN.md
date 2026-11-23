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
- 设计文档已完成：`docs/v2/AGENT-REGISTRY-MIGRATION-DESIGN.md`；当前仅为设计稿，尚未新增表或运行迁移。

回滚：关闭新读路径，保留新增表，不删除旧数据。

### M2：Local Runtime 与事件网关

- 建立 Runtime Supervisor。
- 建立事件 Inbox/Outbox。
- 建立 Session Manager。
- 加入幂等、游标、断线恢复。
- 现有 Muse Runtime 通过兼容 Adapter 接入。

回滚：切回旧 Runtime 与旧 Hub 同步路径。

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
- **M1 Agent Registry：** 兼容映射与 additive schema 设计稿已完成（`docs/v2/AGENT-REGISTRY-MIGRATION-DESIGN.md`）；DDL、migration ledger、legacy backfill、foreign-key enforcement 和 shadow read 均未实施。
- **M2 Runtime / Event Gateway：** 尚未实施。当前 `runtime/runtime.mjs` 仍是 Muse ego v1 主循环；AgentAdapterRegistry/Codex Adapter 未进入生产入口，事件仍为进程内回调，没有 durable Inbox/Outbox、游标恢复和 Hub/App 审批闭环。
- **M3 Provider Adapter：** Codex Adapter 有 mock 测试，但真实 Mac App Server 登录、Session/Turn、断线恢复和审批闭环未验收；ChatGPT、Doubao 等 Adapter 仍按 Provider Matrix 单独验证。
- **安全门槛：** 在 M2 Event Gateway、Hub Permission/Approval/Audit 与 SwiftUI 人工审批闭环完成前，不开放 Codex command/file 高风险工具自动执行。
- **执行顺序：** M1a additive migration → M1b shadow read → M1c read-only API 评估 → M2 Supervisor/Session Manager/Inbox-Outbox/Event Gateway → M3 真实 Provider 验收。真实 Mac 部署继续后置。


### M1a：Agent Registry 增量 schema 与 legacy backfill（候选 PR）

- 已实现版本化 migration ledger、checksum、BEGIN IMMEDIATE 与失败回滚。
- 已实现 providers / agents / connections / sessions 表、外键与索引。
- 已实现旧 member 稳定映射；保留原 members 行、token hash、任务/消息引用与私有记忆。
- 已启用逐连接 SQLite foreign key enforcement，并加入测试和 schema contract snapshot。
- 当前在独立分支等待 CI/审查；未合入 Foundation、未部署。M1b shadow read 未开始。
