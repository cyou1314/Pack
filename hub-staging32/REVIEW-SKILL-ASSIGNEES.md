# staging32 慢工复检：skill_list 调度员视角加 assignees（为 App D16 备的中枢增量）

日期：2026-11-17。范围：仅 skill_list 一处；工具数不变（58）；**未部署**，等 B1-10h 排期到时再走部署流程。

## 改动

- 调度员分支：skills 查询加 id；另查 member_skills ⨝ members，按 skill 聚合 `assignees: [{member, enabled}]` 附加到每条 skill。
- 成员/owner 分支：SQL 与返回形状逐字未动（无 assignees 键）。

## 定向测试（13/13 全过，test_skill_assignees.py）

- publish 两条、assign/unassign 后 assignees 正确跟随；unassign 语义为 enabled=False 保留行 → App 侧须按 enabled=true 过滤才是「已装技能」。
- 成员视角只见自己 enabled 的技能、无 assignees 键、形状与线上一致。

## 对抗复检逐项

- **鉴权**：仍 authenticate()+require(me)；assignees 只在 role=dispatcher 分支生成，成员无法借此枚举他人技能。✔
- **数据暴露**：只有成员名 + enabled 布尔；成员名本就经 member_list 对全体成员可见；不含 token、正文、邮箱类信息。✔
- **注入**：新增 SQL 为静态串、无参数拼接。✔ 写后 grep "redacted" 字面量 = 0、py_compile 通过。✔
- **兼容**：现行 App 解析 skill_list 只取 name/description/version，未知键忽略 → 先部署中枢、后派 App 不会打坏现行包。✔
- **已移除成员**：member_remove 已清技能指派（staging28 语义），正常路径无残留行；纵有残留也只是名字 + enabled 的展示问题，无权限后果。观察项，不处理。
- **规模**：响应增量 = 技能数 × 指派数，当前量级可忽略。✔

## 部署前置（排到 B1-10h 时执行）

全量回归（run-all-suites --hub staging32）绿 + 备份本体 + 覆盖 + 重启 pack-hub + 线上冒烟（调度员 skill_list 见现行三技能的 assignees：absent 应挂 delivery-envelope / egress-discipline / memory-protocol 均为 enabled）。
