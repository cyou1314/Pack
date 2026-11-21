# 搬运二 · 队列显性化（显示部分）施工单（staging54，2026-11-20）

出处：OPENBOT-PORTING.md 第 4 项。本片只做**显示**：让每个未终结任务在
队列里的位置对调度员可见；暂停/恢复等控制动作不在本片。

## 中枢改动（hub.py，工具数不变）

- task_list 返回的每行加 `queue_ahead` 字段：
  - 分组口径：按 assignee 分组（assignee 为 NULL 的归「未指派池」一组）；
  - 队列成员：同组内 status ∈ {open, claimed, working} 的任务；
  - 排序口径：与 task_list 既有排序一致（urgent DESC, id ASC）；
  - queue_ahead = 该任务在本组队列里排在它前面的任务数（队首 = 0）；
  - 终结状态（done/blocked/cancelled/review 之外的一切非上列状态）的
    任务 queue_ahead = null（不在队列里）；review 态已交活待审，不算
    排队，同样 null。
- 实现口径：一次查询取全量活跃任务的 (id, assignee, urgent, status)
  在内存里算名次再映射到本页行，不要逐行查库（防 N+1）；task_list 既有
  过滤/分页/排序行为一律不变，新字段只是附加。
- 其余工具不带此字段（task_audit 等不动）。

## 纪律

- 隔离 staging（tests/archive/hub-staging54/），基线 = 当前生产 hub.py
  （staging53 部署后版本，md5 e40fdb7c…）逐字节复制起步，全新 DB +
  换端口，生产零触碰。
- 测试覆盖：单组多任务的位次（含 urgent 插队改变位次）、多组互不
  干扰、未指派池单独成组、终结/review 态为 null、分页第二页位次
  仍按全队列算（不是页内位次）、task_list 旧字段与排序回归不破、
  工具数仍 74。
- 对抗复检按常规清单过（重点：字段口径与排序一致性、大数据量下
  一次查询的开销），结论写 REVIEW54.md。
- App 任务板展示（位次徽标）是下一片 Mac Codex 的活，本片只定字段。
- 部署等 staging53 部署后回归全绿再按序进行（本片先施工待命）。
