# Mainline / v2 Foundation 基线对账审计

- 日期：2026-11-24（分支基线差异数据仍以 2026-11-23 审计时点为准）
- 仓库：`cyou1314/Pack`
- 目的：在合并 v2 Foundation 前，识别 main 新增的安全、性能、打包与 CI 修复，避免把两个已分叉的开发线直接合并。
- 结论：**禁止直接合并 Umbrella PR #6；采用按风险拆分、逐项移植、每项独立 CI 的方式。**

## 1. 基线事实

| 项目 | Commit / 数量 |
|---|---|
| 共同祖先（merge base） | `3c11de6cf8365c4de9098260ebfea519055f9800` |
| main 当前基线 | `b72203cbbf23c05718316774a77a2c6a05d96422` |
| v2 Foundation 当前基线（本审计时） | `db6a9d75d6a4efab80fcb7c7e353b3d195af73c7` |
| Foundation 相对 merge base | ahead 14 commits，约 300 个变更文件 |
| main 相对 merge base | ahead 9 commits，12 个变更文件 |
| PR #6 | 340 个变更文件，32,122 additions / 6,817 deletions，仍为 draft，GitHub 报告 dirty |

两个分支并非简单的单向领先关系。PR #6 的 dirty 状态不是通过强行 merge 或关闭冲突检查可以解决的问题；它反映了两个分支在同一批核心文件上继续独立演进。

## 2. main 独有的 9 个提交

| Commit | 主要变化 | Foundation 对账结论 |
|---|---|---|
| `a711330fda` | 增加 staging56 专项 CI | 已在 PR #29 恢复独立 staging56 CI，Hub regression 全绿 |
| `fa703dff06` | 审批/任务审计查询索引 | 审计 task_id 与审批索引已在 PR #31 移植；task 热路径索引仍待 v3 |
| `1d03464507` | WAL 从每连接设置改为 Hub init 一次 | 已在 PR #25 移植 |
| `39d88e704d` | Runtime CI 固定 Node 22 | Foundation 已使用 Node 22 |
| `4d7c767d59` | PiBot 成员 token / task 权限说明 | 已核对 Foundation external member / MCP 边界，PiBot 文档移植 PR #33 |
| `cffef379d7` | task 热路径索引、审计 task_id 结构化关联 | 结构化审计/审批部分已在 PR #31；task 热路径索引和 query-plan 测试进入下一 PR |
| `f13dd29edd` | Hub SQLite migration/query-plan CI smoke | PR #31 已覆盖审计/审批 query-plan；任务队列 query-plan 回归待 migration v3 |
| `988823a4b8` | clean-clone macOS installer build、App bundle 测试 | 已在 PR #28 移植原子 no-clobber 发布、图标尺寸回归和 macOS CI |
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

main 已把 `PRAGMA journal_mode=WAL` 从每次请求连接移至 `hub.init()`。Foundation 通过 PR #25 完成同等调整。

- **状态：PR #25 已合并。**
- WAL 仅在 Hub 初始化设置；`foreign_keys=ON` 与 `busy_timeout` 仍逐连接设置。
- 测试覆盖 fresh DB 普通连接不切换 DELETE 模式、Hub init 后持久为 WAL。
- Hub regression、staging、Runtime 和 macOS Debug/Release CI 全绿；Codex review 无未解决问题。

### 3.3 Installer test-mode LaunchAgents 隔离

Foundation 的 `PACK_INSTALL_TEST_ROOT` 现已将 LaunchAgents 也隔离到 `<test-root>/LaunchAgents`。

- **状态：PR #26 已合并。**
- 生产路径仍指向用户 `~/Library/LaunchAgents`，test mode 不会写真实用户目录。
- 子进程回归使用临时 HOME 验证路径隔离，Hub / Runtime / macOS CI 全绿。

### 3.4 App bundle 构建器

Foundation 通过 PR #28 移植了 main 的 no-clobber 发布保护，同时保留 `PACK_BUILD_CONFIGURATION=debug|release`、Info.plist lint 和严格 codesign verify。

- **状态：PR #28 已合并。**
- 输出路径必须是新的 `.app`；相对路径按调用目录解析；拒绝已有目录和 symlink。
- 在临时目录构建与签名，使用原子目录预留防止构建期间目标路径被其他进程抢占。
- Codex 审查指出 Retina iconset 的 256@2x 文件名冲突，已修复并增加四种尺寸的静态回归。
- macOS installer regression、Swift Debug/Release build、unit tests 和 release app bundle verify 全绿。

### 3.5 Task query index 与结构化审计关联

main 的结构化审计和审批查询优化已按 Foundation 模块边界拆分移植。

- **结构化审计/审批部分：PR #31 已合并，新增 migration v2。**
- 新增 `audit.task_id`；历史审计仅按 canonical `task N` 前缀回填，拒绝前导零、伪前缀和 SQLite 64 位整数溢出。
- 历史 `approval_request` 消息按 `任务#N` 回填；新审批消息现在持久化 `messages.task_id`。
- task history 和 unclaimed escalation 去重改用结构化 task_id；approval_state 优先走索引，并保留只扫描 NULL task_id 的兼容 fallback。
- 增加 `idx_audit_task`、`idx_messages_approval_task`、`idx_messages_dispatch_decisions`、`idx_review_log_task_time`。
- v2 checksum 覆盖 parser、正则、SQLite 整数上限和 backfill helper；Agent Registry v1 checksum 未改变。
- CI 覆盖 task 1 / task 12 碰撞、前导零、超范围 ID、历史审批回填、NULL fallback、query plan 和 SQLite contract snapshots。

**尚待移植：** mainline 的 task 热路径索引（task list sort / assignee / room、active queue、lease expiry、unclaimed due、pending transfer expiry）及代表性数据集 query-plan 回归。下一步使用独立 migration v3；不修改 v1/v2 checksum，也不把 monolithic `hub.py` 整体复制回 Foundation。

### 3.6 staging56 专项回归

Foundation 将历史 suite 保留在 `tests/archive/hub-staging56`，并通过 PR #29 恢复独立 CI。

- **状态：PR #29 已合并。**
- Runner 已移植可移植 Python 选择、监听就绪检查、清理和失败报告；迁移测试不再依赖开发者机器上的绝对 venv 路径。
- staging56 access-mode / migration regression 已在 Hub CI 独立运行并通过；未把历史套件重新塞回默认 staging runner。

## 4. 当前移植队列

| 顺序 | 项目 | 状态 | 原则 |
|---|---|---|---|
| 1 | SQLite WAL init-only | PR #25 已合并 | 不改 schema |
| 2 | Installer test path isolation | PR #26 已合并 | 仅 test-mode 路径变化 |
| 3 | Mainline/Foundation reconciliation audit | PR #27 已合并 | 禁止整包合并 PR #6 |
| 4 | App bundle 原子 no-clobber 发布 | PR #28 已合并 | 保留 Foundation release/codesign 能力 |
| 5 | staging56 专项 CI | PR #29 已合并 | 独立专项回归 |
| 6 | Structured task audit + approval indexes | PR #31 已合并 | 新 migration v2，v1 checksum 冻结 |
| 7 | PiBot external-member evaluation | PR #33 文档移植中 | 不 vendoring，不在 Mac 验收前跑 POC |
| 8 | Task hot-path indexes + query-plan regression | 下一项 | 新 migration v3，代表性数据集验证 |


## 5. M1c 与真实 Mac 数据库门槛

M1c API 不因本审计完成而自动解锁。仍需对真实 Mac 数据库的备份副本执行手动只读 shadow preflight，并同时满足：

- migration checksum 全匹配；
- `quick_check=ok`；
- `foreign_key_check` 为 0 行；
- mapping mismatch / unmapped / unexpected mapping 均为 0；
- 报告不含 token、profile、消息/任务正文或私人记忆；
- 备份可恢复，记录恢复演练结果。

本轮 Desktop Commander 设备 `AppledeMacBook-Pro-jay.local` 仍为 Offline。因此尚未执行任何用户 Mac 数据库读取、备份、shadow report 或部署。设备恢复在线后，也只对备份副本执行只读检查；不会直接对生产数据库运行迁移。

## 6. 合并策略

- 不合并 PR #6 作为一个 340 文件的大包。
- 不以 merge main 覆盖 Foundation 的模块化 Hub。
- 每个 mainline 修复按上表独立移植到 Foundation，独立 PR、独立 CI、独立 Codex review。
- 所有迁移变更使用新增 migration version；旧版本 checksum 冻结。
- Foundation 与 main 完成对账、冲突清单关闭、全套 CI 通过、Mac 备份/恢复与 shadow preflight 完成前，不部署 Mac。
- 只有当 Foundation 的增量 PR 全部验收后，才创建专门的 mainline integration branch 处理最终合并；该 branch 仍需独立 CI，不直接推动 main。

## 7. 本次审计结论

mainline 已验证的 WAL、安装器隔离、App Bundle、staging56 和结构化 task audit / approval 修复均已分批移植到 v2 Foundation，且各 PR 独立通过 CI 与 Codex review。

剩余代码对账重点是 task 热路径索引和 query-plan 测试（migration v3）。PiBot 仅作为 Pack external member 的可选集成方向保留，不替代原生 Agent / 官方 API 双模式，也不接管 Pack Hub、权限或审计。

**Mac 部署仍后置：** 真实设备当前离线，M1c shadow preflight、备份恢复演练和 Mac 本机验收尚未执行。PR #6 仍不得作为整体发布包直接合并。

