# Pack v2 主线与 Foundation 分支重新对账

审计日期：2026-11-26  
仓库：cyou1314/Pack  
本审计替代 2026-11-24 旧版 MAINLINE-RECONCILIATION-AUDIT.md 中已经过期的提交数量与分叉结论。

## 1. 当前基线

| 项目 | 当前值 |
|---|---|
| main | 8458a500f458779e93b0f800b6cd97c5e70ea411 |
| Foundation | 7d839d9f0abcea3e03bbfa57193f717f716a2b8e |
| main 与 Foundation | diverged |
| Foundation 相对 main | ahead 29 / behind 121 |
| main 相对 Foundation | ahead 121 / behind 29 |
| main 侧差异文件 | 57 |
| Foundation 侧差异文件 | 300 |
| 双方直接重叠文件 | 17 |

Foundation 不是 main 的简单前进分支。不能直接 merge、rebase 后不审查，或用 Foundation 整文件覆盖 main。

## 2. 17 个重叠文件

以下文件在 main 和 Foundation 两侧都发生过变化：

1. .github/workflows/ci.yml
2. README.md
3. app/Package.swift
4. app/Sources/PackApp/HubClient.swift
5. app/Sources/PackApp/PackApp.swift
6. app/make-bundle.sh
7. app/test-make-bundle.sh
8. hub/hub.py
9. installer/README.txt
10. installer/build-dmg.sh
11. installer/manager.py
12. installer/test_manager_paths.py
13. runtime/l2-handoff/engine.cjs
14. runtime/l2-handoff/engine.test.cjs
15. runtime/muse4ai-bridge/bridge.mjs
16. runtime/runtime.mjs
17. tests/README.md

这些文件必须按功能和提交逐项对账，不能整文件覆盖。重点风险：

- Hub：Foundation 已拆分 Hub 领域模块，main 继续演进单体 Hub；任务权限、消息读取、SQLite migration 和 Agent Gateway 需要定位到最终服务边界。
- CI：合并时必须保留 main 的 staging56、Hub regression、Runtime、Browser isolation、macOS build/tests 等有效门禁。
- Installer / App Bundle：保留 main 已有的 no-clobber 原子发布、测试目录隔离、签名与 clean-clone 检查，同时吸收 Foundation 的版本化迁移与模块化能力。
- Runtime：核对 Supervisor、Outbox/Inbox、Engine handoff 与 Muse Bridge 的生命周期、事件持久化和取消语义，不能只解决文本冲突。

## 3. Foundation 上的 M2 PR 栈

PR #42 已合并到 Foundation 分支，不代表已进入 main。以下 PR 仍处于独立的 Foundation 开发栈：

| PR | 主题 | Base |
|---|---|---|
| #43 | Durable Outbox delivery worker | Foundation |
| #44 | Inbox consumer leases / ACK 设计文档 | #43 分支 |
| #45 | Inbox consumer leases / ACK 实现 | #44 分支 |
| #46 | Trusted Agent Session binding 设计 | #45 分支 |
| #47 | Hub-issued Agent Session binding | #46 分支 |
| #48 | Atomic event projection / Session recovery 设计 | #45 分支 |
| #49 | Transaction-scoped Inbox ACK / release helpers | #47 分支 |
| #50 | Agent Session projection schema migration v7 | #49 分支 |
| #51 | Atomic Agent Session Inbox projector | #50 分支 |

各 PR 当前 head 的 GitHub Actions 检查记录均显示 Hub、Runtime、macOS 构建通过；这些检查是在各自 Foundation base 上运行，不能代替在当前 main 上的兼容性测试。

依赖关系不是单条直线：#48 是基于 #45 的兄弟设计分支，不在 #47 → #49 → #50 → #51 的提交链中。合并或移植时必须单独处理 #48 的设计结论，不能按 PR 编号机械合并。

## 4. 与当前 PR #52 的关系

当前集成 PR：

- PR：[ #52 ](https://github.com/cyou1314/Pack/pull/52)
- Base：main
- 当前 head：ce6e2e8431a6cd5d8c40e956e1cdda06548019e4
- Draft，43 次提交、73 个文件、约 9.5k 行新增。

PR #52 与 M2 PR #47 只有两个直接文件重叠：

- .github/workflows/ci.yml
- hub/hub.py

PR #52 与 #43、#44、#45、#46、#48、#49、#50、#51 的当前文件列表没有直接重叠；但 Foundation 与 main 本身有 17 个重叠文件。后续将 M2 移植到 main 时，仍需重新审查 PR #52 已加入的 Hub 工具、身份门禁、协作 schema 和 CI 变化。

PR #52 当前 CI 通过，只证明它相对于 main 的这条独立分支通过现有测试；不能证明它与 Foundation 的 M2 Event Gateway / Session Projection 组合后仍然正确。

## 5. Agent Registry 的概念性数据模型重叠

除文件级重叠外，Foundation M1 与 PR #52 还存在需要在合并前统一的概念性数据模型：

**Foundation M1（Hub 侧）：**

- providers：Provider 厂商目录；
- agents：Pack 稳定 Agent 身份，通过 legacy_member_id 兼容旧 members；
- agent_connections：连接模式、认证类型、凭证引用和连接状态；
- agent_sessions：按 Agent + Room 绑定的 Provider Session 元数据；
- Hub 只保存非敏感元数据和本机凭证引用，不保存 API Key、Cookie 或私人记忆。

**PR #52（设备本地 + Hub 协作侧）：**

- 本地 AgentAccountManager / AgentProfile：以 UUID 标识设备上的账号档案；
- AgentCredentialVault：将 API Key 保存在 device-only Keychain；
- collaboration_executors：以 member_id 作为 Hub 执行身份，并单独保存 provider、access_kind、account_ref、owner_id 和 revoked；
- account_ref 当前与本机 AgentProfile UUID 对应，但它不是 Foundation agents / agent_connections 的外键。

**需要统一的边界：**

1. 本机 AgentProfile 应继续作为本机账号与凭证的事实来源；Hub 不应复制本机密钥或私人会话数据。
2. Hub Agent Registry 应作为跨房间协作身份和连接元数据的事实来源；members 继续承担兼容认证、角色和任务归属。
3. collaboration_executors 不应长期成为第二套 Agent Registry。需要决定它是临时兼容映射，还是改为引用 agents.agent_id / agent_connections.connection_id，并保留 member_id 作为兼容执行身份。
4. account_ref、agent_id、connection_id 三者需要明确且稳定的映射规则；不能仅靠 provider + UUID 字符串约定而无数据库约束。
5. collaboration_presence 应明确绑定到一个 Agent Connection / 本机执行进程，而不是把成员在线状态等同于 Provider 登录状态。
6. Agent Session 应使用 agent_sessions 的 room 绑定和状态机；不得因为原生账号登录成功就推断 Session 已创建、恢复或具备 Provider 私人记忆。
7. 新旧注册表切换必须遵守 M1 shadow-read 门槛；真实 Mac 数据库没有通过只读 shadow preflight 前，不启用新读路径。

因此，PR #52 的 collaboration_executors 可以作为当前受控执行身份的过渡层，但在 Foundation M1 移植前必须形成明确的迁移/映射方案。不得同时维护两套互不关联的 Provider / Agent / Connection 主数据。

## 6. 推荐集成策略

**禁止直接合并 Foundation 分支或把 PR #43–#51 全部按顺序点 Merge。**

推荐建立一个新的 integration branch，起点必须是最新 main，然后按以下顺序移植：

1. **冻结 main 基线**：记录 SHA、SQLite schema/migration checksum、Hub MCP contract、CI job 清单和 clean-clone App/DMG 构建结果。
2. **先对账 Hub 模块边界**：比较 Foundation 的 services/core 拆分与 main 的最新 Hub 权限、消息、任务、附件和审计路径。保留 main 的安全修复，按领域移植服务代码，不整文件替换。
3. **移植 Agent Event Gateway**：保留独立配对凭证、token hash、scope、事件 envelope 校验、幂等、速率限制与 TLS 边界；新 migration 使用新增版本，不改写已发布 migration checksum。
4. **移植 Outbox / Inbox**：先实现可恢复的投递与消费租约，再做 ACK、cursor、replay 和 Session projection。明确 transport receipt 与 consumer ACK 的区别，不提前宣称端到端 exactly-once。
5. **整合 Session Binding / Projection**：核对 Hub-issued Session 身份、事件顺序、幂等键、游标、断线恢复和并发 ACK；设计文档 #48 必须作为单独输入审查。
6. **整合 PR #52**：重点复核 Hub 工具包装、协作身份、专用 executor allowlist、私有上下文授权、迁移脚本和 CI workflow；不得让新 M2 路径绕过这些门禁。
7. **每个切片单独验证**：Hub 默认回归、staging56、SQLite fresh/legacy migration、contract snapshots、Runtime tests、Browser isolation、Swift tests、macOS App/Release build、Installer clean-clone。
8. **完整集成后再做真实 Mac 验收**：先备份、恢复演练和只读 shadow preflight；未经用户明确确认，不迁移生产数据库。

## 7. 不允许的集成捷径

- 不把 Foundation 的 300 文件差异作为一个整体 merge 到 main。
- 不用旧 Foundation Hub 文件覆盖 main 当前 Hub。
- 不修改已发布 migration 的 SQL 或 checksum；只能新增 migration version。
- 不为了让 CI 变绿而删除 main 现有测试 job 或归档专项回归。
- 不将 HTTP transport receipt 说成消费者已处理或端到端至少一次。
- 不在 Session 身份、cursor、ACK、恢复与幂等语义未闭环时启用生产自动投递。
- 不在真实 Mac 离线期间读取、复制或迁移用户生产数据库。

## 8. 本次结论

当前 Pack v2 有两条不同基线：

- **main-based 产品集成线**：PR #52，集中在 Agent 双模式、API 文本 Adapter、群聊协作和受控执行基础。
- **Foundation-based M2 架构线**：PR #43–#51，集中在 Agent Event Gateway、Outbox/Inbox、Session Binding 与 Projection。

两条线不能简单互相替代，也不能在未对账时直接合并。下一步应先建立 main-based integration branch，按上面的领域切片逐项移植与回归；真实 Mac 部署继续后置。
