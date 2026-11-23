# Agent Registry 数据层迁移设计

- 状态：M1a 实施候选；已在独立分支加入增量 schema、版本化迁移与 legacy 映射，等待 CI/代码审查；尚未合入 Foundation 或部署到用户 Mac
- 日期：2026-11-23
- 目标基线：Pack v2 M1（Agent Registry 数据层）
- 约束：保持 v1 members / rooms / messages / tasks / runs / audit / member_memory 的现有语义

## 1. 决策摘要

M1 采用**兼容映射 + 只读影子验证**，不把现有 `members` 替换为 `agents`。

- `members` 继续作为 Hub 认证、角色、任务归属、外部成员房间绑定和既有权限的事实来源。
- 新 `agents` 表为 Pack v2 提供稳定 Agent ID，并通过唯一 `legacy_member_id` 指回旧成员。
- Provider、连接和 Session 元数据分开存储；API Key、OAuth Token、CLI 凭据、Cookie 和个人记忆不写入 Hub。
- 首轮迁移只插入新表，不更新或删除旧行、不轮换 token、不重写 message/task 外键。
- 未能从旧数据可靠推断的连接模式保持 `legacy_unclassified`，不根据 provider 名称猜测 native/local/API。
- 先双读比对，再由独立开关启用新读路径；旧写路径继续运行到后续切换批次。

## 2. 现有数据事实与兼容边界

当前 Hub 的 `members` 表在 fresh schema 中包含：

- 身份/认证：`id`、`name`、`role`、`token_hash`、`created_at`、`removed_at`
- 展示：`profile`、`avatar`、`display_name`
- 在线/额度：`last_heartbeat`、`quota_*`
- Provider 登录容器：`provider`、`login_state`
- 外部成员：`kind`、`bound_room_id`、`joined_msg_id`
- 权限：`access_mode`

现有任务、消息、技能、阅读游标等表仍使用 member ID 或 name 关联。成员私有记忆保存在 `member_memory` / `member_memory_versions`，以 member name 为边界；不能因 Agent 映射而复制到房间上下文。

**不可改变：** `members.token_hash` 的认证职责、外部成员配对与房间绑定、member role/access_mode gate、task assignee/run member_id、历史消息 author_id、member skills/read cursors、member memory 归属。

## 3. 新表建议

所有表使用显式字段、外键和索引。当前 `hub/core/storage.py` 已配置 WAL 与 busy_timeout，但**尚未启用 `PRAGMA foreign_keys=ON`**；M1a 必须在共享连接工厂逐连接启用并增加 FK enforcement 测试，不能把它描述成现有行为。以下是逻辑字段，不是已经执行的 DDL。

### providers

| 字段 | 约束/用途 |
|---|---|
| provider_id | TEXT PRIMARY KEY，稳定小写 slug |
| display_name | TEXT NOT NULL |
| status | TEXT NOT NULL，active / deprecated |
| created_at / updated_at | REAL NOT NULL |

Provider 目录描述厂商，不代表该厂商的所有连接方式均已支持。能力清单仍由经过验证的 Adapter Descriptor 提供，不在 Hub 维护一份可能漂移的能力副本。

### agents

| 字段 | 约束/用途 |
|---|---|
| agent_id | TEXT PRIMARY KEY，Pack 生成的稳定 ID |
| provider_id | TEXT NOT NULL，FK providers |
| legacy_member_id | INTEGER UNIQUE NULL，FK members(id)，ON DELETE RESTRICT |
| display_name | TEXT NOT NULL |
| connection_mode | TEXT NULL；仅接受已验证模式 |
| status | TEXT NOT NULL，pending / active / disconnected / disabled / removed / legacy_unclassified |
| legacy_provider_label | TEXT NULL，原样保留旧 members.provider |
| created_at / updated_at | REAL NOT NULL |

一个旧 member 最多映射一个 Agent。owner/dispatcher 不映射为 Agent；只映射 role='member' 的行，包括已软删除成员（status=removed），以保留历史引用。ID 采用可持久化、不可因重跑迁移而变化的生成规则；具体编码在 DDL PR 中冻结。

### agent_connections

| 字段 | 约束/用途 |
|---|---|
| connection_id | TEXT PRIMARY KEY |
| agent_id | TEXT NOT NULL，FK agents |
| mode | TEXT NOT NULL，native_account / local_runtime / official_api / mcp_connector / external_agent / browser_bridge |
| auth_kind | TEXT NOT NULL，none / oauth / api_key / cli_session / mcp_connector / local_process |
| credential_ref | TEXT NULL，只允许 Keychain/本机凭据库引用，不允许密钥值 |
| status | TEXT NOT NULL，pending / connected / disconnected / revoked / error |
| safe_metadata_json | TEXT NOT NULL DEFAULT '{}'，经白名单过滤的非敏感配置 |
| created_at / updated_at / revoked_at | 时间戳 |

允许保留历史连接记录；活跃连接唯一性通过 partial unique index 实现。连接轮换只新增/停用连接行，不覆盖历史审计。任何明文 token、API Key、refresh token、Cookie、私有目录或完整环境变量都禁止进入该表。

旧成员的 `token_hash` 不复制到 `credential_ref`。旧 external agent 仍由 members 认证；在独立兼容 Adapter 能安全复用现有认证前，不伪造新连接记录。

### agent_sessions

| 字段 | 约束/用途 |
|---|---|
| session_id | TEXT PRIMARY KEY，Pack 内部 ID |
| agent_id | TEXT NOT NULL，FK agents |
| room_id | INTEGER NOT NULL，FK rooms |
| provider_session_id | TEXT NULL，Provider 原生 Thread/Session ID |
| status | TEXT NOT NULL，creating / active / interrupted / completed / failed / disconnected |
| last_event_sequence | INTEGER NOT NULL DEFAULT 0 |
| created_at / updated_at / last_used_at | 时间戳 |

首版 Session 固定绑定一个 room，禁止默认跨房间复用 Provider session；这样不会因原生会话已有私人上下文而把一个房间的共享上下文泄露给另一个房间。Provider 不支持恢复时，创建新 Session 并保留旧 Session 状态。Provider Session ID 只用于恢复，不等于可读取 Provider 私人记忆。

## 4. 保守回填规则

迁移在一个事务中执行，并以 `legacy_member_id UNIQUE` + `INSERT ... ON CONFLICT DO NOTHING` 保证可重复运行。M1a 需新增版本化 migration ledger（例如 `hub_schema_migrations(version, checksum, applied_at)`），并用 `BEGIN IMMEDIATE` + 单条 DDL/DML `execute` 执行迁移；不要把现有 `executescript(SCHEMA)` 当成事务迁移器。

1. 仅选取 `members.role='member'`；排除 owner/dispatcher。
2. 每行保留原 member ID 映射；旧名字、展示名、创建时间和 provider 原始标签按白名单复制。
3. `kind='external'` 只映射连接模式 `external_agent`，不得覆盖厂商身份；Provider 按显式白名单独立映射。`login_state=pending/logged_in` 映射 Agent status=pending，`expired` 映射 disconnected；无状态或未知状态映射 `legacy_unclassified`。这些状态不推断 native/local/API 接入模式。
4. `removed_at IS NOT NULL` 的行映射为 removed；不激活、不重新配对。
5. 首轮不自动创建 agent_connections 或 agent_sessions。旧 token hash、私有记忆、原生会话和凭据均留在旧系统/本机。
6. `bound_room_id` / `joined_msg_id` 暂时仍由 members 外部成员 gate 使用；不自动推导新的多房间 room_agents 关系。
7. 不修改 messages.author_id、tasks.assignee、runs.member_id、member_skills、member_read_cursors、member_memory 或其历史记录。

provider_id 规范化必须使用显式映射表。未知/空 provider 统一映射到 `legacy`，原始字符串写入 `legacy_provider_label`，禁止动态创建任意 provider slug。

## 5. 影子读取与切换

- Shadow read：从 members 和 agents 映射分别生成视图，按 legacy_member_id 比对身份、展示名、provider、active/removed 状态。
- 不在影子阶段改变 member_list、authenticate、task assignment、room access 或 App 数据源。
- 记录不匹配计数和原因，不记录 token、个人记忆或凭据。
- 只有所有活动/软删除成员均可解释、历史引用无 orphan、重复迁移幂等、并发 init 安全时，才允许单独 PR 启用新读路径。
- 新读路径必须可通过 feature flag 立即关闭；members 兼容入口至少保留一个完整发布周期。

## 6. 迁移与回滚验收

### 必须覆盖

- fresh DB、当前 schema、最早 legacy fixture、已移除成员、external member、空 provider、未知 provider。
- 同一 DB 连续运行迁移两次，行数和映射 ID 不变化。
- 两个 HubContext 并发运行迁移，不产生重复 Agent。
- 外键完整性、SQLite integrity_check、原 75 MCP tool schema 快照和旧 24 表结构快照无意外变化；新表需单独批准快照差异。
- 迁移前后 members/token_hash、messages/tasks/runs 关联、member memory 和 external room binding 字节级/语义级不变。
- shadow read 结果逐行比对；所有 mismatch 有分类和可解释处理。
- 失败注入验证事务回滚；回滚只关闭新读路径/删除新增表（仅在无新写数据时），不回写或重建旧表。

### 禁止事项

- 不在安装器升级期间隐式执行 Agent Registry 迁移。
- 不把 provider 登录状态当成 OAuth 成功或个人记忆可用的证据。
- 不把 API Key/refresh token 放入 Hub、SQLite、事件 payload、日志或测试 fixture。
- 不以迁移成功替代 Mac Keychain、Codex App Server、Gemini CLI 的真实本机验证。

## 7. 实施拆分

1. **M1a（独立 PR）：** 加入新表 DDL、版本化迁移 runner、legacy 映射和隔离 fixture；不接新读路径。
2. **M1b（独立 PR）：** Shadow read 服务与差异报告、并发/幂等/故障注入测试。
3. **M1c（独立 PR）：** 仅在 shadow read 稳定后，评估 Agent detail/read-only API；不移除 members。
4. **M2：** 再做 Runtime Supervisor、Session Manager、Inbox/Outbox 和 Event Gateway；不得把 Provider Adapter 直接暴露给 Hub MCP 工具执行。

**当前状态：** 仅完成设计文档。没有 DDL、数据库迁移、影子读取或生产数据操作。


## 8. M1a 实施记录（候选 PR，2026-11-23）

- 新增 `hub/core/migrations.py`：版本号 + SHA-256 checksum migration ledger；checksum 同时覆盖 DDL、Provider 映射表及数据回填函数 AST；所有 DDL/DML 在 `BEGIN IMMEDIATE` 中执行，失败整体 rollback；重复执行验证 checksum 后跳过。
- 新增 providers / agents / agent_connections / agent_sessions 表与外键、唯一索引、查询索引。首版不生成 connection/session 行，不复制凭据或私人记忆。
- 对现有 `members.role='member'` 逐行建立稳定 ID `legacy-member-{id}` 映射；dispatcher/owner 不映射。已软删除成员标记 removed；external kind 只映射为 external_agent 连接模式，不覆盖 codex/gemini 等厂商身份；pending/logged_in/expired 登录状态分别保守映射为 pending/pending/disconnected。Provider 使用显式白名单映射，未知值归入 legacy 并保留原始标签。
- `hub/core/storage.py` 每个连接启用 `PRAGMA foreign_keys=ON`；现有旧表不添加/重写外键，旧 token、任务、消息、记忆和房间绑定不改。
- `hub.init()` 在既有兼容列迁移提交后运行独立版本化迁移；不由 DMG 安装器执行，不读取 Keychain，不触碰 Provider 账号。
- 新增迁移单测覆盖 legacy 映射、重复执行、外键拒绝非法引用和失败回滚；CI 增加该测试，SQLite fresh/legacy snapshots 更新。
- **M1a 验收仍需 CI 与代码审查通过。** M1b shadow read、差异报告和并发 init 专项测试尚未实施；此阶段没有启用新读路径。

- Codex 审查修正：Provider 身份与 external connection mode 独立归一；迁移 checksum 已纳入回填逻辑 AST 与 Provider 映射表，防止数据迁移规则变化却复用旧 checksum。
