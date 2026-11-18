# staging28 慢工复检 — register 指定头像组合 + member_remove

日期：2026-11-16 晚。增量：工具 56 → 57（新增 member_remove；member_register 加可选 avatar_combo 入参；members 加 removed_at 迁移列）。

## 写路径与锁覆盖
- member_register：名字预检、组合校验/占用检查、INSERT 全部在全局 _lock 内（沿用既有范式）。
- member_remove：整笔在 _lock 内单事务完成——任务回收（含 _close_run、_desktop_release_for_task）、open 任务解指派、pending 转交过期+limbo 回池、桌面锁残留清理、技能指派删除、软删+token 作废、审计，一次 commit。
- 其余改动均为只读路径：authenticate（removed 直接拒）、member_list（WHERE removed_at IS NULL）、_used_combos（只计在册成员）、自动接力候选查询（加 removed_at IS NULL，防把任务接力给已移除成员）。

## 并发探针（定向套件内，实测）
- 双路并行 register 抢同一指定组合：恰一成一败（败方 combo already taken）。连跑含在 29 项套件内、全绿。
- 双路并行 member_remove 同一成员：恰一次成功，另一次 already removed。

## 鉴权与伪造面
- 普通成员 token 调 member_register / member_remove 均被拒（dispatcher 专属，require 把关）。
- 被移除成员的原 token：authenticate 返回 None，全工具不可用（套件实测 member_list 被拒）；token_hash 同时被换成随机哈希，双保险。
- 无新增消息/决议路径，无 kind 伪造面变化。
- 错误信息只回显调用方自己给的名字/组合，不泄露他人数据。

## 口径决定（有意为之，记录在案）
- 软删：成员行保留，消息/审计/记忆历史不动（记忆随 token 作废不可达）；名字保持占用，同名再登记明确拒绝（reserved）——防身份冒名顶替。
- review 中任务不回收：成果已交付，等调度员验收，assignee 指向已移除成员仅作历史标注。
- 头像组合随移除释放，可被新成员复用（套件实测）。
- dispatcher / owner / 自己不可移除，四种拒绝均有套件覆盖。

## 残留观察
- authenticate 假定 members 已迁移（serve 启动必先 init，成立）；CLI 直连未迁移库不在支持面。
- H3 回填在极端情况下（存量成员缺 combo 且已有移除记录）可能把已释放组合回填给存量成员——组合仍全员唯一，无实际冲突。

## 验证
- 定向套件 test_member_admin.py：29/29 全绿（两轮重跑一致）。
- 全量回归 run-all-suites.sh --hub hub-staging28/hub.py：见 regression 记录（staging15 工具数断言同步 56→57）。
