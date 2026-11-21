# staging54 复检记录（搬运二·队列显性化显示部分）

施工单：docs/history/PORT-QUEUE-BRIEF.md。基线 = 生产 hub.py（staging53 部署后版本，
md5 e40fdb7cdab9675a5cd8f5b834aac080），开工与收工各验一次逐字节一致；
隔离端口 18799、全新 DB，生产文件与生产 DB 零触碰（收工复验生产
hub.py md5 未变）。未 commit、未 push，改动只留本目录待审。

## 改动点（仅 task_list 一处）

- docstring 追加 staging54 口径说明。
- 页行查询之后、关连接之前，新增**一次**查询取全量活跃任务
  `(id, assignee) WHERE status IN ('open','claimed','working')
  ORDER BY urgent DESC, id ASC`，内存里按 assignee 原值分组（NULL =
  未指派池）逐个计数，得 task_id → queue_ahead 映射。
- 行组装时附加字段：状态在队列集合内取映射值，其余状态
  （review/done/blocked/cancelled 等）为 None。
- 签名、过滤、分页、排序、旧字段一律未动；其余工具不带此字段
  （task_audit 等未动，diff 已核只落 task_list）。

diff 规模：与生产基线共 25 行差异（1 行改写 + 24 行新增，净 +23 行）。

## 测试结果（run_tests.sh 全绿，RUNNER_EXIT=0）

| 套件 | 结果 |
|---|---|
| test_staging54（新） | 20/20 |
| test_staging51 | 142/142 |
| test_fixes51 | 18/18 |
| test_concurrency | 8/8 |
| test_watcher | 10/10 |
| test_staging52 | 42/42 |
| test_agent_script | 22/22 |
| test_staging53 | 56/56 |
| test_question_state | 29/29 |
| test_direct | 9/9 |
| **合计** | **356 PASS / 0 FAIL** |

新套件覆盖施工单全部口径：单组位次、urgent 插队改位次（含未指派池
内插队只动本组）、claimed→working 仍在队列且位次不变、多组互不干扰、
未指派池单独成组、review/done/blocked/cancelled 为 null 且不占位次
（队列自动收拢）、旧字段全在 + 排序不变、多状态过滤回归、room 过滤
下位次仍按全组全局算、与独立 SQL 计算的全量交叉核对、分页第二页
位次按全队列算（100..104 而非页内 0..4；无过滤翻页同样保组内位次）、
工具数仍 74。

未纳入：staging53 的迁移/负载套件（针对 question 列迁移与提问链路
负载，本片无 schema 变更、无关），理由同 run_tests.sh 注释。

首跑记录：回归链首跑时 test_staging53 在夹具处报
`member name already exists: alice`——是本 runner 把 51/52/53 套件
排在同一 DB 的编排问题（53 套件夹具成员名与 51/52 同名，其自家
runner 亦独享全新 DB），非中枢回归。已给 53 套件独立分段（fresh DB）
后全量重跑全绿。

## 对抗复检结论

1. **口径与排序一致性**：名次查询的 ORDER BY 与 task_list 既有排序
   逐字一致（urgent DESC, id ASC）；分组键用 assignee 原值（成员 id /
   NULL），与行上的 assignee 名同源。测试 G 以测试侧独立 SQL 重算
   全量名次逐行比对，一致。
2. **N+1**：每调用恒定多一条查询，与页大小、行数无关；名次在内存
   单趟算完。活跃集规模由在飞任务量决定（生产量级为十位数），即便
   万级也是一次扫描加内存计数，相对页查询本身的扫描+排序开销可忽略；
   未新增索引，十万级活跃再议。
3. **过滤/分页不污染口径**：名次取自未过滤的全局活跃集，
   status/assignee/room 过滤与 offset 分页只决定返回哪些行，不决定
   位次——已由分页与 room 两组用例实证。
4. **external 可见性**：external 的 task_list 本就被强制收窄为「本房
   且 assignee == 本人」，其行上的 queue_ahead 只数它自己组内的任务，
   不暴露任何他人任务的存在与数量，无新增泄漏面。
5. **竞态**：页行与名次集是同连接两条查询、无快照隔离；两查之间
   任务状态恰变时，个别行可能出现 active 但 queue_ahead 为 None
   （映射未命中）或位次差一。纯显示字段、无任何认领/调度逻辑消费，
   App 15 秒刷新自愈，接受。
6. **状态集合边界**：review 按施工单不算排队（已交活待审）；blocked
   是调度员处置态不自动回池，不占位次；sched_at 未到的 open 任务按
   状态口径计入队列（与 task_list 展示口径一致，施工单以状态为准，
   字面执行）。
7. **兼容性**：纯附加字段，无签名变更；工具数 74 不变；356 条既有
   断言全过，含并发与 watcher 套件。

结论：可部署候选。App 任务板位次徽标是下一片（Mac Codex）的活，
本片只定字段；部署按施工单等 staging53 部署后回归全绿再按序进行。
