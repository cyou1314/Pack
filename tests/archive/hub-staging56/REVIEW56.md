# REVIEW56 — staging56 对抗复检：成员级访问模式预设 access_mode

施工单：`docs/history/PORT-ACCESSMODE-BRIEF.md`（搬运四，OpenBot 对照表最后一项）。
施工区：`tests/hub-staging56/`（隔离副本 + 全新 DB + 端口 18798，迁移套件
自带 18795/18796）。生产本体与生产 DB 未动；未 commit、未部署。

## 1. 改动面（hub.py，生产基线 md5 d54fd360… → 候选）

`diff hub_prod_baseline.py hub.py` = 126 行（增删合计），五处：

1. **迁移**（`init()`，staging53 的 messages.payload 迁移之后）：
   `members` 无 `access_mode` 列则 `ALTER TABLE members ADD COLUMN
   access_mode TEXT`；存量行不回填（保持 NULL）。
2. **模式闸**（`_ext_check` 定义之后）：常量 `ACCESS_MODES`、
   `READONLY_ALLOWED_TOOLS`（20 个）、`WORKSPACE_DENIED_TOOLS`（5 个）；
   `_member_access_mode(me)` 归一（NULL/缺失/脏值 → `full`）；
   `_mode_check(me, tool_name)`：full 放行；readonly 名单外拒；workspace
   只禁名单内；拒绝抛 `forbidden: tool <名> is not available in access
   mode <模式>` 并写审计 `access_mode_denied`、detail=`tool=<名>
   mode=<模式>`。审计写法沿用 `_ext_check` 的自开连接 + commit 范式
   （每调用现查 DB，不走 `_lock`、不缓存）。
3. **接线**（`_gated_tool`）：`_ext_check` 之后、`me is not None` 时加
   `_mode_check(me, fn.__name__)`。顺序即叠加语义：external 白名单闸在前、
   模式闸在后，任一闸拒绝即抛错，后闸不再执行——审计天然单写。
4. **回读**（`member_list`）：SELECT 与行字典加 `access_mode`（归一后
   的三值之一）。external 脱敏视图不带此字段（见 §3 判断 2）。
5. **新工具** `member_access_mode_set(name, mode)`（工具数 74 → 75）：
   调度员专属；`with _lock()` 内校验顺序——mode 非法 → `bad access
   mode`；成员不存在 → `no such member`；已移除 → `member removed`；
   role=dispatcher → `cannot set access mode for the dispatcher`；
   role=owner → `cannot set access mode for the owner`；成功写审计
   `member_access_mode_set`、detail=`<name>: <旧值> -> <新值>`（旧值
   为归一后值），返回 `{name, access_mode}`。

## 2. 口径偏差（1 处，按施工单自带回退条款）

**`external_gate` 未进 readonly 名单。** 施工单 §2 名单含它，但同节给了
回退条款：该工具兼有只读查询形态（`enabled=None`）与写形态，模式闸按
工具名判定、无法按参数区分 → 整工具移出名单并在此记明，本文件即记明
处。补充事实：`external_gate` 本体为调度员专属，而调度员被防自锁规则
禁止设为 readonly——任何合法 readonly 账号都到达不了它，移出名单无
可达行为差异。READONLY_ALLOWED_TOOLS 实装 20 个（施工单原名单 21 个
减此 1 个）。

## 3. 实现判断（2 处，待调度员裁定）

1. **readonly 名单中的 7 个调度员本体门工具**（task_audit、
   task_transfers_list、run_list、rule_list、egress_rule_list、
   template_list、pair_code_list）：模式闸按名单放行后，本体 `require`
   仍以 role 拒绝普通成员——名单收录表达的是「只读形态」语义归类，
   不改变其可达性。测试已按此口径断言（被 require 拒、无
   access_mode_denied 审计）。若日后这些工具的本体门放宽，模式闸会
   自动成为它们的实际闸门。
2. **member_list 的 external 脱敏视图不带 access_mode**：沿用件二脱敏
   口径（external 只见 id/name/display_name/kind），不让 external 看到
   他人访问模式；内部视图每行都带。施工单 §6 字面是「每行带」，此为
   有意收窄，已在此备案。

## 4. 验收对照（施工单 §7 逐条）

| 验收项 | 结果 |
|---|---|
| 工具数 75、新工具在列 | PASS（tools == 75） |
| 迁移：生产基线建老库 → 候选起服补列、老行读 full 且可干活、新闸在迁移库可用 | PASS（test_migration56 10/10，含 pair 链在迁移库跑通） |
| set 后立即生效（set readonly 后下一次 claim 即被拒） | PASS（carol 链：full 认领 → set → 拒 + 审计 detail 精确 → set 回 full → 认领成功） |
| readonly：名单内读工具可用、名单外写工具逐一被拒且各写一条 access_mode_denied | PASS（13 个成员可达读工具全可用（名单 20 个逐一有着落：13 可用 + 7 本体门）；7 个本体门工具由 require 拒、模式闸不写审计；task_claim/room_post/inbox_mark_read/attachment_put/task_complete 五个逐一被拒、审计恰 5 条且 detail 含 tool+mode；被拒 room_post 零落库） |
| readonly 例外：heartbeat 可用 | PASS |
| workspace：日常写可用、外发/技能面 5 工具被拒 | PASS（claim/post 可用；egress_check/skill_publish/skill_assign 被拒且审计 mode=workspace；skill_unassign/skill_rollback 同名单未逐一打——三者已验、名单同一 frozenset，见 §6 注） |
| 防自锁：set dispatcher / set owner / 对已移除 / 不存在 / 非法 mode 全拒且不落库不写成功审计 | PASS（六种拒绝全验；拒绝调用零审计；alice 模式未被成员自调用改动） |
| external + readonly 叠加：白名单内工具被模式闸拒只写模式审计；非白名单工具被白名单闸拒只写白名单审计 | PASS（task_claim → access_mode_denied +1、external_tool_denied +0；room_summary_set → 反之；inbox_pull/heartbeat 双闸通过；恢复 full 后 claim 成功） |
| 脏数据：access_mode 被直改 'weird' / NULL 均读作 full 且行为 full | PASS（bob 直改 DB 两轮） |
| 既有回归全绿 | PASS（见 §5） |

## 5. 测试计数

`run_tests.sh` 八段（每段全新 DB）：

| 套件 | 结果 |
|---|---|
| test_staging56（新套件） | **51/51 PASS** |
| test_migration56 | **10/10 PASS** |
| test_staging51 | 142/142 PASS |
| test_fixes51 | 18/18 PASS |
| test_concurrency | 8/8 PASS |
| test_watcher | 10/10 PASS |
| test_staging52 | 42/42 PASS |
| test_agent_script | 22/22 PASS |
| test_staging53 | 56/56 PASS |
| test_question_state | 29/29 PASS |
| test_staging54 | 20/20 PASS |
| test_staging55 | 11/11 PASS |
| test_direct | 9/9 PASS |
| test_load53 | 5/5 PASS |

默认回归链（`run-all-suites.sh --hub <候选>`，unit pack-reg56cand）：
**16 套件中 15 套件原样全绿**（16/17/18/19/23/24/38/39/40/41/42/43/44/
45/46，日志 regression-staging56-candidate.log）；唯一例外 staging15：
其 test_skills.py 硬编码断言「工具数 74」，候选为 75 故该行 FAIL、整段
被判 FAIL——这是每次工具数变更的例行伴随项，前两次变更（65→71、
71→74）均在部署提交里同步改此断言（见 c3a7634、0450733 的提交说明）。
本单边界不动 staging56 之外文件，故未改它；已在一次性副本（/tmp，
按运行器同款路径重写 + 断言改 75）单独复跑 staging15 全套验证：
test_skills/test_review/test_functional/test_more/test_concurrency/
test_decision_kind 六个子套件 rc 全 0、零 FAIL。部署时须随 hub 一并把
该断言改 74→75，改后默认链应为全绿。

注记：复制套件（test_staging51/53/54/55）的工具数断言按 staging52 复制
test_staging51 的前例由 74 改 75；harness/room_watch/pack-agent.mjs 抄
staging53/55 现行版未改。首轮全链曾因新套件一条断言口径错误（把 7 个
本体 dispatcher 门工具当成员可用）出现 49/50，断言按 §3 判断 1 修正后
新套件复跑 51/51；其余各段在同一轮全链中已全绿，计数取自该轮。

## 6. 对抗复检（adversarial_hub_review 清单）

- **锁与并发**：模式闸不走 `_lock`（与 `_ext_check` 同范式：短连接现查
  现写审计）；set 工具走 `_lock` 全程串行。并发探针 2 轮（每轮 3 个
  setter 线程 × 15 次交替 set + 3 个 caller 线程 × 20 次 room_post /
  task_list 混压）：set 成功恒 45、task_list 成功恒 60、room_post 成功
  + 被拒恒 60、无任何意外错误、终局 mode 为合法值、服务保持健康。
  「set 与调用同刻到达」的中间态语义 = 调用按其 authenticate 时刻的
  DB 现值判定，无缓存窗口——「立即生效」由构造保证，不靠失效通知。
- **授权**：模式闸只收紧不放宽——它在 authenticate/require 之后（包装
  器层在本体 require 之前，但拒绝方向单调：多一层拒、不会把本体要拒
  的放过去）；full 模式零行为变化（闸直接返回）。成员无法自改模式
  （set 本体 dispatcher 门 + 测试实证成员调用被拒且模式未变）。
- **防自锁**：dispatcher/owner 双拒 + mode 白名单校验在写库前；六种
  拒绝路径均零审计零落库（测试实证）。dispatcher 恒 full 是构造性
  的：唯一写入口拒写 dispatcher 行。
- **双闸叠加**：闸序固定（白名单 → 模式），任一拒即抛、后闸不执行，
  审计单写由测试双向实证（+1/+0 互斥）。
- **迁移**：补列幂等（init 可重跑）；存量 NULL 不回填、读时归一——
  回滚到旧 hub.py 时多出一列 TEXT 对旧代码无影响（旧 SELECT 不引用
  该列），迁移可逆性好。
- **脏数据**：归一函数对 NULL/空串/任意脏值一律 full，且 set 的审计
  detail 用归一后旧值（脏值行被 set 时 detail 记 `full -> ...`，不
  把脏值写进审计）。
- **副作用面**：被拒调用除一条审计行外零落库（room_post 被拒后房间
  消息数不变，测试实证）；模式闸审计自开连接，若审计写失败会冒泡
  为调用失败——与 `_ext_check` 既有范式一致（fail-closed），不新增
  风险面。
- **未覆盖**：workspace 名单中 skill_unassign/skill_rollback 未逐一实
  打（与已验的 skill_publish/skill_assign 同一 frozenset 分支、且本体
  另有 dispatcher 门，成员调用必先被模式闸以同一代码路径拒绝）；App
  侧不显示 access_mode（施工单 §5 明确本片不做 App）。

## 7. 75 工具归类表

本体门 = 工具本体 require 的角色（any = 任意已登录成员；dispatcher =
调度员专属；skill_assign/skill_unassign 的 require 在其 helper
`_skill_assign_set` 内）。readonly 名单「是」= readonly 模式下模式闸
放行（本体门另行决定可达性，见 §3 判断 1）。

| 工具 | 本体门 | readonly 名单 | workspace 禁名单 |
|---|---|---|---|
| attachment_get | any | 是 | — |
| attachment_list | any | 是 | — |
| attachment_put | any | — | — |
| desktop_acquire | any | — | — |
| desktop_release | any | — | — |
| desktop_status | any | 是 | — |
| egress_check | any | — | 是 |
| egress_rule_delete | dispatcher | — | — |
| egress_rule_list | dispatcher | 是 | — |
| egress_rule_upsert | dispatcher | — | — |
| external_gate | dispatcher | — | — |
| heartbeat | any | 是 | — |
| inbox_mark_read | any | — | — |
| inbox_pull | any | 是 | — |
| inbox_send | any | — | — |
| member_access_mode_set | dispatcher | — | — |
| member_avatar_set | dispatcher | — | — |
| member_list | any | 是 | — |
| member_login_state_set | dispatcher | — | — |
| member_quota_report | any | — | — |
| member_quota_set | dispatcher | — | — |
| member_register | dispatcher | — | — |
| member_remove | dispatcher | — | — |
| member_rename | dispatcher | — | — |
| member_token_rotate | dispatcher | — | — |
| memory_read | any | 是 | — |
| memory_rollback | any | — | — |
| memory_versions | any | — | — |
| memory_write | any | — | — |
| message_delete | any | — | — |
| message_edit | any | — | — |
| pair_code_create | dispatcher | — | — |
| pair_code_list | dispatcher | 是 | — |
| pair_code_revoke | dispatcher | — | — |
| review_file | dispatcher | — | — |
| room_archive | dispatcher | — | — |
| room_create | dispatcher | — | — |
| room_list | any | 是 | — |
| room_post | any | — | — |
| room_read_new | any | 是 | — |
| room_rename | dispatcher | — | — |
| room_summary_get | any | 是 | — |
| room_summary_set | dispatcher | — | — |
| rule_delete | dispatcher | — | — |
| rule_list | dispatcher | 是 | — |
| rule_upsert | dispatcher | — | — |
| run_finish | any | — | — |
| run_list | dispatcher | 是 | — |
| run_start | any | — | — |
| skill_assign | ? | — | 是 |
| skill_list | any | 是 | — |
| skill_publish | dispatcher | — | 是 |
| skill_read | any | 是 | — |
| skill_rollback | dispatcher | — | 是 |
| skill_unassign | ? | — | 是 |
| task_accept_transfer | any | — | — |
| task_audit | dispatcher | 是 | — |
| task_block | any | — | — |
| task_cancel | dispatcher | — | — |
| task_claim | any | — | — |
| task_clear_backoff | dispatcher | — | — |
| task_complete | any | — | — |
| task_create | dispatcher | — | — |
| task_list | any | 是 | — |
| task_release | dispatcher | — | — |
| task_review | dispatcher | — | — |
| task_schedule | dispatcher | — | — |
| task_set_urgent | dispatcher | — | — |
| task_transfer | any | — | — |
| task_transfers_list | dispatcher | 是 | — |
| task_update | any | — | — |
| template_delete | dispatcher | — | — |
| template_fire | dispatcher | — | — |
| template_list | dispatcher | 是 | — |
| template_upsert | dispatcher | — | — |

## 8. 部署建议（不在本单执行范围）

按既有增量流程：备份生产 hub.py → 覆盖 → restart pack-hub →
member_list 冒烟（全行带 access_mode、新工具在列、工具数 75）→
preflight → 默认回归 + --full。迁移为纯补列、旧行 NULL，老版本回滚
无障碍。
