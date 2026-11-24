# Mainline / v2 Foundation 基线对账审计

- 日期：2026-11-24
- 仓库：`cyou1314/Pack`
- 目的：在合并 v2 Foundation 前，识别 main 新增的安全、性能、打包与 CI 修复，避免把两个已分叉的开发线直接合并。
- 结论：**禁止直接合并 Umbrella PR #6；采用按风险拆分、逐项移植、每项独立 CI 的方式。**

## 1. 基线事实

| 项目 | Commit / 数量 |
|---|---|
| 共同祖先（merge base） | `3c11de6cf8365c4de9098260ebfea519055f9800` |
| main 当前基线 | `b72203cbbf23c05718316774a77a2c6a05d96422` |
| v2 Foundation 当前基线（PR #37 合并后） | `2179177dbca0f7491a22e63d3fa2e004f3209722` |
| Foundation 相对 merge base | ahead 24 commits |
| main 相对 merge base | ahead 9 commits，12 个变更文件 |
| main 与 Foundation 当前比较 | diverged；Foundation ahead 24、behind 9 |
| PR #6 | 348 个变更文件（34,234 additions / 6,916 deletions），仍为 Draft，GitHub 报告 mergeable=false / dirty；不能整包合并 |

两个分支并非简单的单向领先关系。PR #6 的 dirty 状态不是通过强行 merge 或关闭冲突检查可以解决的问题；它反映了两个分支在同一批核心文件上继续独立演进。

## 2. main 独有的 9 个提交

| Commit | 主要变化 | Foundation 对账结论 |
|---|---|---|
| `a711330fda` | 增加 staging56 专项 CI | 已由 PR #29 恢复归档 suite 的独立 CI |
| `fa703dff06` | 审批/任务审计查询索引 | Foundation 已通过 migration v2 具备结构化 task_id 与审批查询索引 |
| `1d03464507` | WAL 从每连接设置改为 Hub init 一次 | 已在 PR #25 移植 |
| `39d88e704d` | Runtime CI 固定 Node 22 | Foundation 已使用 Node 22 |
| `4d7c767d59` | PiBot 成员 token / task 权限说明 | 已由 PR #35 的源码级评估补充 MCP / ACP 接入边界 |
| `cffef379d7` | task 热路径索引、审计 task_id 结构化关联 | 结构化审计已由 Foundation migration v2 实现；热路径索引由 PR #34 以 migration v3 移植 |
| `f13dd29edd` | Hub SQLite migration/query-plan CI smoke | PR #34 增加 Foundation 原生 HubContext / EXPLAIN QUERY PLAN 回归 |
| `988823a4b8` | clean-clone macOS installer build、App bundle 测试 | 已由 PR #28 移植显式输出路径校验、no-clobber 原子发布与 App Bundle 回归测试 |
| `b72203cbbf` | 隔离 installer 测试 LaunchAgents | 已在 PR #26 移植 |

## 3. 关键重叠文件与风险

main 的 12 个文件变更中，有 6 个与 Foundation 直接重叠：

- `.github/workflows/ci.yml`
- `hub/hub.py`
- `app/make-bundle.sh`
- `installer/build-dmg.sh`
- `installer/manager.py`
- `tests/README.md`

Foundation 相对共同祖先的改动覆盖约 300 个文件，不能按 main 的整文件版本覆盖。

### 3.1 Hub 模块边界已改变

Foundation 将 Hub 主模块从约 5,494 行拆至约 2,879 行，并把任务、成员、房间、记忆、附件等逻辑迁入 `hub/services/*`。

因此，main 的 `hub/hub.py` 热路径改动不能直接 cherry-pick 或整文件替换。必须先把查询与写入责任定位到 Foundation 的服务模块，再单独做 schema / migration / query-plan 测试。

### 3.2 SQLite WAL 生命周期

main 已把 `PRAGMA journal_mode=WAL` 从每次请求连接移至 `hub.init()`。Foundation 原先在共享 `storage.connect()` 每次打开连接时都执行该 pragma，可能在并发读写时产生不必要的 journal-mode 检查/锁竞争。

- 移植 PR：#25，已合并
- 改动：WAL 仅在 Hub 初始化设置；`foreign_keys=ON` 与 `busy_timeout` 仍逐连接设置。
- 验证：fresh DB 普通连接不改变 DELETE 模式；Hub init 后持久为 WAL，后续连接继承 WAL；Hub、Runtime、macOS CI 全绿。

### 3.3 Installer test-mode LaunchAgents 隔离

Foundation 的 `PACK_INSTALL_TEST_ROOT` 原先仍把 `agents` 指向真实的 `~/Library/LaunchAgents`，存在测试模式触碰真实用户目录的风险。

- 移植 PR：#26，已合并
- 改动：test mode 将 LaunchAgents 放到 `<test-root>/LaunchAgents`；生产路径不变。
- 验证：子进程隔离测试与 CI 全绿。

### 3.4 App bundle 构建器

main 的 `app/make-bundle.sh` 增加显式输出路径校验、拒绝已存在路径、临时目录构建和原子 no-clobber 发布，防止构建期间目标路径被其他进程创建后误写入已有目录。

- 移植 PR：#28，已合并
- 保留 Foundation 的 `PACK_BUILD_CONFIGURATION=debug|release`、Info.plist lint、严格 codesign verify。
- 回归覆盖：既有目录、symlink、相对路径、目标竞态、Retina 图标尺寸；Hub、Runtime、macOS CI 全绿。

### 3.5 Task query index 与结构化审计关联

这里需要区分两层改造，避免误判 Foundation 已完成的工作：

**结构化审计与审批查询（Foundation migration v2，已存在）**

- `audit.task_id` 结构化字段；
- 旧 audit detail 仅对规范前缀做保守回填；
- approval_request 的 `task_id` 回填及旧文本 fallback；
- `idx_audit_task`、`idx_messages_approval_task`、`idx_messages_dispatch_decisions`、`idx_review_log_task_time`；
- `storage.write_audit()` 新写入时同步填充 task_id；
- `services.tasks.get_task_audit()` 使用参数化 task_id 查询。

以上已由 Foundation 的 migration v2、`test_structured_task_audit.py` 与服务测试覆盖；不得修改 v1/v2 migration 定义或 checksum。

**Task queue / lease / unclaimed / transfer 热路径索引（Foundation migration v3，PR #34）**

- `idx_tasks_urgent_id`、`idx_tasks_assignee_urgent_id`、`idx_tasks_room_urgent_id`；
- `idx_tasks_active_queue`、`idx_tasks_lease_expiry`、`idx_tasks_unclaimed_due`；
- `idx_task_transfers_pending_expiry`；
- 以真实服务 SQL 形状和代表性数据验证 `EXPLAIN QUERY PLAN`；
- 为使 assignee / room 复合索引实际承担筛选与排序，`list_tasks()` 先将名称解析为 member / room ID，再对 tasks 表按 ID 过滤；未知名称仍返回空结果；
- 更新 fresh / legacy-migrated SQLite contract snapshots。

PR #34 已合并独立、版本化、带 checksum 的 migration v3；v1/v2 保持冻结。旧库在 `hub.init()` 完成 `sched_at`、`urgent`、`next_claim_at`、transfer `status` / `expires_at` 等兼容列升级后才应用 v3。测试覆盖 fresh DB、缺少这些列的旧 DB、迁移重跑、索引安装与查询计划。PR #36 后续保留 room/member name residual predicates，确保名称在 ID lookup 后并发变化时仍与原单查询语义一致，同时复验复合索引仍被采用。

### 3.6 staging56 专项回归

Foundation 将历史 suite 移入 `tests/archive/hub-staging56`。PR #29 已将 mainline 的可移植 runner 与 Python 解释器选择移植到归档 suite，并恢复独立 CI 步骤；清理失败会导致套件失败，且只终止 runner 自己启动的 Hub PID。CI 全绿。历史 suite 仍留在 archive，不并入默认回归清单。

## 4. 当前移植队列

| 顺序 | 项目 | 状态 | 原则 |
|---|---|---|---|
| 1 | WAL init-only | PR #25 已合并 | 不改 schema |
| 2 | Installer test path isolation | PR #26 已合并 | 仅 test-mode 路径变化 |
| 3 | App bundle 原子 no-clobber 发布 | PR #28 已合并 | 保留 Foundation release/codesign 能力 |
| 4 | staging56 专项 CI | PR #29 已合并 | 独立运行归档回归，不污染默认 suite |
| 5 | Task hot-path indexes 与过滤语义 | PR #34 / #36 已合并 | migration v3；v1/v2 冻结；真实服务 SQL、并发重命名语义与 query-plan tests |
| 6 | PiBot MCP / ACP 评估 | PR #35 已合并 | 推荐 external member MCP POC；不把 ChatGPT OAuth 误称为 App 原生记忆 |
| 7 | 其余文档 / 产品差异 | 待审查 | 不覆盖现有评估与安全边界 |

## 5. M1c 与真实 Mac 数据库门槛

M1c API 不因本审计完成而自动解锁。仍需对真实 Mac 数据库的备份副本执行手动只读 shadow preflight，并同时满足：

- migration checksum 全匹配；
- `quick_check=ok`；
- `foreign_key_check` 为 0 行；
- mapping mismatch / unmapped / unexpected mapping 均为 0；
- 报告不含 token、profile、消息/任务正文或私人记忆；
- 备份可恢复，记录恢复演练结果。

本次检查到的 Desktop Commander 设备 `AppledeMacBook-Pro-jay.local` 当前 Offline，最后在线约 456 小时前。因此尚未执行任何用户 Mac 数据库读取、备份或 shadow report。设备恢复在线后，也只对备份副本执行只读检查；不会直接对生产数据库运行迁移。

## 6. 合并策略

- 不合并 PR #6 作为一个 348 文件的大包；它仍是 Draft/dirty，不作为集成入口。
- 不以 main 的整文件版本覆盖 Foundation 的模块化 Hub，也不直接把 Foundation 分支强行合入 main。
- 已识别的 mainline 代码修复已通过 PR #25、#26、#28、#29、#34、#36 分批移植；PiBot 评估由 PR #35 补齐，Mac/M1c 部署门槛由 PR #37 补齐。以上 PR 均已合并到 Foundation。
- 当前已满足“Foundation 增量 PR 验收完成”的前置条件。下一步应从最新 main 创建专门的 integration branch，逐项核对 main 的 12 个文件差异（其中 6 个与 Foundation 核心文件直接重叠），再把经过验证的 Foundation 状态集成进去。
- 集成 branch 必须独立运行 Hub、staging56、Runtime、Swift Release/App Bundle 与 SQLite contract CI；未解决差异或 CI 不完整时，不合并 main。
- 所有迁移变更使用新增 migration version；旧版本 checksum 冻结。
- 真实 Mac 备份/恢复与 shadow preflight 仍是部署门槛；代码集成完成不代表可以部署。

## 7. 本次审计结论

截至 2026-11-24，Foundation 最新 HEAD 为 `2179177dbca0f7491a22e63d3fa2e004f3209722`（PR #37 已合并），main 最新 HEAD 为 `b72203cbbf23c05718316774a77a2c6a05d96422`。当前两条分支仍 diverged（Foundation ahead 24 / behind 9），PR #6 仍为 348 文件 Draft/dirty。已识别的 mainline 修复已通过独立 PR 移植，但最终 mainline integration 尚未执行；必须先建立独立 integration branch 并完成文件级核对与全套 CI。真实 Mac 数据库 shadow preflight 仍是独立门槛，Mac 设备离线期间不读取或迁移生产数据库。
