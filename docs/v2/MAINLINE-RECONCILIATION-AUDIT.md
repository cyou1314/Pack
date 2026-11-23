# Mainline / v2 Foundation 基线对账审计

- 日期：2026-11-23
- 仓库：`cyou1314/Pack`
- 目的：在合并 v2 Foundation 前，识别 main 新增的安全、性能、打包与 CI 修复，避免把两个已分叉的开发线直接合并。
- 结论：**禁止直接合并 Umbrella PR #6；采用按风险拆分、逐项移植、每项独立 CI 的方式。**

## 1. 基线事实

| 项目 | Commit / 数量 |
|---|---|
| 共同祖先（merge base） | `3c11de6cf8365c4de9098260ebfea519055f9800` |
| main 当前基线 | `b72203cbbf23c05718316774a77a2c6a05d96422` |
| v2 Foundation 当前基线（本审计时） | `db6a9d75d6a4efab80fcb7c7e353b3d195af73c7` |
| Foundation 相对 merge base | 当前 ahead 18 commits，约 300 个变更文件 |
| main 相对 merge base | ahead 9 commits，12 个变更文件 |
| PR #6 | 当前 343 个变更文件，32,583 additions / 6,823 deletions，仍为 draft，GitHub 报告 dirty |

两个分支并非简单的单向领先关系。PR #6 的 dirty 状态不是通过强行 merge 或关闭冲突检查可以解决的问题；它反映了两个分支在同一批核心文件上继续独立演进。

## 2. main 独有的 9 个提交

| Commit | 主要变化 | Foundation 对账结论 |
|---|---|---|
| `a711330fda` | 增加 staging56 专项 CI | Foundation 已归档 staging56，但 CI 未显式运行归档套件 |
| `fa703dff06` | 审批/任务审计查询索引 | 与结构化 task_id 改造一起评估 |
| `1d03464507` | WAL 从每连接设置改为 Hub init 一次 | 已在 PR #25 移植 |
| `39d88e704d` | Runtime CI 固定 Node 22 | Foundation 已使用 Node 22 |
| `4d7c767d59` | PiBot 成员 token / task 权限说明 | 需要核对文档语义，不直接覆盖现有 PiBot 评估 |
| `cffef379d7` | task 热路径索引、审计 task_id 结构化关联 | 未移植；需适配拆分后的 Hub services 与新增迁移版本 |
| `f13dd29edd` | Hub SQLite migration/query-plan CI smoke | Foundation 尚缺对应 query-plan 测试 |
| `988823a4b8` | clean-clone macOS installer build、App bundle 测试 | Foundation 已有 release bundle 构建，但缺少完整的 no-clobber 路径回归 |
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

- 移植 PR：#25
- 改动：WAL 仅在 Hub 初始化设置；`foreign_keys=ON` 与 `busy_timeout` 仍逐连接设置。
- 测试：fresh DB 的普通连接不改变 DELETE 模式；Hub init 后持久为 WAL，后续连接继承 WAL。
- 合并门槛：PR #25 的最新 head CI 全绿且 Codex review 无未解决问题。

### 3.3 Installer test-mode LaunchAgents 隔离

Foundation 的 `PACK_INSTALL_TEST_ROOT` 已隔离 Hub、Engine、配置、App 和 socket 路径，但原先仍把 `agents` 指向真实的 `~/Library/LaunchAgents`。这使测试模式存在写入真实用户 LaunchAgent 目录的风险。

- 移植 PR：#26
- 改动：test mode 将 LaunchAgents 放到 `<test-root>/LaunchAgents`；生产路径不变。
- 测试：子进程使用临时 HOME，断言 test mode 不创建真实用户 LaunchAgents 路径。

### 3.4 App bundle 构建器

main 的 `app/make-bundle.sh` 增加了显式输出路径校验、拒绝已存在路径、临时目录构建和原子 no-clobber 发布，防止构建期间目标路径被其他进程创建后误写入已有目录。

Foundation 的版本已有额外能力：`PACK_BUILD_CONFIGURATION=debug|release`、Info.plist lint、严格 codesign verify。**不能用 main 文件整体覆盖 Foundation 文件**；下一批应只移植原子发布和路径安全，同时保留 release 构建与签名验证，并新增竞态回归测试。

### 3.5 Task query index 与结构化审计关联

main 的 staging57 / query-index 改动不只是 CREATE INDEX：

- 为 audit 增加 `task_id` 并将旧 detail 中可精确识别的 task ID 保守回填；
- 为 approval_request 消息补充结构化 `task_id`，并保留旧文本解析 fallback；
- task audit 查询由 `detail LIKE` 改为参数化 task_id 查询；
- 为 task queue、lease expiry、unclaimed due、transfer expiry 等热路径添加索引；
- 以 `EXPLAIN QUERY PLAN` 和代表性数据集验证查询计划。

Foundation 当前 audit schema 没有 `audit.task_id`，`inspect_unclaimed_tasks` 仍使用 detail LIKE 查询。该改造必须：

1. 新增独立、版本化、带 checksum 的 migration；禁止修改已发布的 M1a migration 定义或 checksum。
2. 将 Hub handler 的查询迁入对应 service，不把 main 的 monolithic hub.py 逻辑复制回来。
3. 先证明旧 audit / approval 消息回填不会误匹配 task 1 与 task 12、超长数字、普通消息或畸形 detail。
4. 对 fresh DB、旧 DB、迁移重跑、失败回滚、查询计划和 MCP/SQLite snapshots 分别验收。
5. 在性能数据证明收益后再考虑进一步索引；不要为“有索引”而增加无效写放大。

### 3.6 staging56 专项回归

Foundation 将历史 suite 移入 `tests/archive/hub-staging56`，但当前 CI 的 `run-all-suites.sh` 默认清单不含 staging56，CI 也没有显式运行归档脚本。main 已将它作为单独 CI 步骤。

下一步需验证归档的 `run_tests.sh` 是否可在干净 Linux runner 上独立运行；若通过，单独恢复 staging56 migration/access-mode 专项 CI，不要将旧历史 suite 全部重新放回默认回归。

## 4. 当前移植队列

| 顺序 | 项目 | 状态 | 原则 |
|---|---|---|---|
| 1 | WAL init-only | PR #25 已合并 | 不改 schema |
| 2 | Installer test path isolation | PR #26 已合并 | 仅 test-mode 路径变化 |
| 3 | App bundle 原子 no-clobber 发布 | PR #28 已合并 | 保留 Foundation release/codesign 能力 |
| 4 | staging56 专项 CI | PR #29 CI / review 进行中 | 先验证归档脚本，再启用 CI |
| 5 | Task structured audit + query indexes | 待设计/实现 | 新 migration version，service-aware |
| 6 | 其余 PiBot / 文档差异 | 待审查 | 不覆盖现有评估与安全边界 |

## 5. M1c 与真实 Mac 数据库门槛

M1c API 不因本审计完成而自动解锁。仍需对真实 Mac 数据库的备份副本执行手动只读 shadow preflight，并同时满足：

- migration checksum 全匹配；
- `quick_check=ok`；
- `foreign_key_check` 为 0 行；
- mapping mismatch / unmapped / unexpected mapping 均为 0；
- 报告不含 token、profile、消息/任务正文或私人记忆；
- 备份可恢复，记录恢复演练结果。

本次检查到的 Desktop Commander 设备 `AppledeMacBook-Pro-jay.local` 当前 Offline，最后在线约 442 小时前。因此尚未执行任何用户 Mac 数据库读取、备份或 shadow report。设备恢复在线后，也只对备份副本执行只读检查；不会直接对生产数据库运行迁移。

## 6. 合并策略

- 不合并 PR #6 作为一个 340 文件的大包。
- 不以 merge main 覆盖 Foundation 的模块化 Hub。
- 每个 mainline 修复按上表独立移植到 Foundation，独立 PR、独立 CI、独立 Codex review。
- 所有迁移变更使用新增 migration version；旧版本 checksum 冻结。
- Foundation 与 main 完成对账、冲突清单关闭、全套 CI 通过、Mac 备份/恢复与 shadow preflight 完成前，不部署 Mac。
- 只有当 Foundation 的增量 PR 全部验收后，才创建专门的 mainline integration branch 处理最终合并；该 branch 仍需独立 CI，不直接推动 main。

## 7. 本次审计结论

当前最优路径不是继续扩大 v2 变更面，而是先把 main 已经验证的安全/性能修复逐项移植到模块化 Foundation。PR #25、#26、#28 已完成；PR #29 正在验证 staging56 专项回归；后续再按 service-aware 方案移植结构化任务审计与查询索引。真实 Mac 数据库离线门槛仍然独立存在。
