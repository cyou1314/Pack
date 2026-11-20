# REVIEW49 — 中枢三处修复（Gemini 审计 → 调度员核实 → staging49 施工）

隔离环境：`tests/hub-staging49/`（生产 hub.py 副本 + 全新 DB + 端口 18796），
未部署、未重启任何服务、未动生产文件。diff 正本 `staging49.diff`
（相对生产 hub.py：+71 / −21，共 92 行变更行，8 个 hunk，只含下述三类改动）。

## 改动一：member_list() 连接泄漏（staging49 hub.py:2654 起）

生产原实现 `conn = db()` 后整个函数无 `conn.close()`，正常返回与异常路径都漏。
修法（对齐邻近 template_list / task_schedule 的既有风格）：两条查询进
`try:`（2666–2680），`finally: conn.close()`；头像 JSON 解析与 week_done
组装本来就不依赖连接，留在 close 之后原样执行。返回结构与字段一字未动。

## 改动二：写接口缺 removed_at 校验（7 处）

统一口径：按名查到成员但 `removed_at` 非空 → `raise ValueError("member is removed: <name>")`，
且校验点全部位于任何 INSERT/UPDATE 之前（整笔零写入）；成员不存在时保持原有
`no such member: <name>` 不变。

| 落点（staging49 行号） | 工具 | 校验位置 |
|---|---|---|
| 2177 | task_create | assignee 解析处（该函数为手动 close 风格，补 close 后抛错） |
| 2238 | task_schedule | assignee 解析处（role 校验之前） |
| 2279 | template_upsert | 默认 assignee 解析处 |
| 2350–2362 | template_fire | 建卡前对生效指派（override 优先、否则模板存值）再验一次；成员不存在时不在此报错、仍由 task_create 报 no such member |
| 1560 | member_quota_set | 目标成员解析处（_apply_quota 之前） |
| 2887 | task_transfer | to_member 解析处（存在性之后、role 校验之前；SELECT * 本就含 removed_at） |
| 3700 | _skill_assign_set | skill_assign / skill_unassign 共用函数内（见对抗自查第 4 条） |

## 改动三：task_list 补 room（staging49 hub.py:2692 起）

- 签名末尾追加 `room: str = ""`（位置参数 status/assignee_name/offset 兼容不变）。
- SELECT 新增 `rm.name AS room`，FROM 新增 `JOIN rooms rm ON rm.id=t.room_id`
  （tasks.room_id NOT NULL 且 rooms 从不删除，INNER JOIN 不丢行）。
- 给了 room 才拼 `AND rm.name=?`；排序（urgent DESC, id ASC）、LIMIT 100
  OFFSET 子句与原 SQL 逐字一致。不传 room 时除多出 room 字段外行为与旧版
  完全一致；房间名不存在 → 空列表不报错（与 status 过滤口径一致）。

## 按名查 members 全点位审计表（行号为生产 hub.py）

| 行 | 所在工具/函数 | 用途 | 已校验 removed_at | 本轮 |
|---|---|---|---|---|
| 570 | _mem_target（memory_* 的 member 参数） | 解析记忆目标成员 | 否 | 不改：记忆是历史数据，调度员回读已移除成员记忆属审计/追溯路径、非派活；成员侧 target 恒为 token 本人，已移除者 token 已作废无法认证 |
| 1055/1058 | sweeper 转交过期回收 | 按名找接收方/发送方决定回池归属 | 发送方已查（removed → 不回原主） | 不改：内部记账、非派活入口，且关键分支已 removed-aware |
| 1346 | member_register | 同名占用检查 | 是（removed 名字保留占用） | 不改 |
| 1414 | member_remove | 移除目标解析 | 是（already removed 报错） | 不改 |
| 1452 | member_remove 内部 | pending 转交接收方回查 | —（移除事务内部记账） | 不改 |
| 1495 | member_avatar_set | 换头像目标 | 是（"member removed:"，既有文案与本轮新文案措辞不同，未强行统一以免无谓 diff） | 不改 |
| 1554 | member_quota_set | 设额度目标 | 否 → **本轮补** | 改 |
| 1692 | member_login_state_set | 登录状态回写目标 | 是 | 不改 |
| 2165 | task_create | assignee 解析 | 否 → **本轮补** | 改 |
| 2224 | task_schedule | 排班目标解析 | 否 → **本轮补** | 改 |
| 2262 | template_upsert | 模板默认 assignee | 否 → **本轮补** | 改 |
| （无直查） | template_fire | 原先全权委托 task_create | 否 → **本轮补直查再验** | 改 |
| 2677 一带 | task_list | assignee_name 过滤（mb.name=?） | 否 | 不改：只读过滤，已移除成员的历史任务仍应可查（审计口径）；本轮只加 room |
| 2840 | task_transfer | 转交目标解析 | 否 → **本轮补** | 改 |
| 3647 | _skill_assign_set | 派/收技能目标 | 否 → **本轮补** | 改 |
| 959 | _auto_relay 候选查询 | 自动接力候选 | 是（removed_at IS NULL 过滤） | 不改 |
| 727 | _used_combos | 头像组合占用 | 是（只计未移除） | 不改 |

范围外（非按名查，一并记录）：authenticate 按 token_hash（841）；按 role 查
dispatcher（301/465/748/977/1169/2822/2879）；按 id 查（410/1099/1565/1600/
1663/2818/2864）；init/迁移全表扫描（745/812，含已移除行属有意）；
member_quota_report 按 token 本人 id 查、无名字入参，已移除者无法认证。

## 对抗自查

1. **鉴权边界没变**：diff 内零 authenticate/require 改动；六条路径的角色门
   （dispatcher 专属 ×5、task_transfer 的持有人/调度员校验）原样保留，且
   removed 校验一律在鉴权与存在性检查之后执行——未授权调用者在到达查名
   之前已被拒，错误面无变化。新错误只让本就有权处置成员的调度员（及自报
   目标名的转交持有人）区分「不存在/已移除」，不构成新的存在性侧信道。
2. **task_list 向后兼容**：room 参数追加在签名末尾且有默认值，App 与运行时
   的无参/位置参数调用不受影响；新增 room 键是行字典的纯增字段，消费方按
   键读取、未知键忽略；测试 3 实证无参调用行数、排序、status/assignee_name/
   offset 行为与改前一致。
3. **member_list 异常路径关连接的证据**：两条查询整体在 try 内、finally
   无条件 close；close 之后的代码只用已 fetch 的 rows，抛任何异常都与连接
   无关。FD 实测（/proc/<pid>/fd 按 DB 路径计数）：热身后基线 0，连调 50 次
   后仍 0（本机连接为按调用开关，修复后关闭是确定性的，不再依赖被遗忘
   对象的 GC 回收时机——原实现异常时若 traceback 被框架留存，帧内 conn
   会被钉住更久）。
4. **skill_unassign 的连带变化（如实披露）**：校验落在共用函数
   _skill_assign_set，unassign 已移除成员同样改为报错。判定可接受：
   member_remove 时已整表删除该成员的 member_skills 行，移除后再 unassign
   本无行可动；对在册成员的「从未指派又收回」幂等返回路径未动。
5. **零写入证据**：七个被拒操作逐项跑后核 DB——tasks / task_transfers /
   task_templates 计数与快照一致、已移除成员额度字段与 quota_updated_at
   未变、member_skills 无新行、转交目标任务的 assignee/status 未变（测试 2）。
   唯一例外说明：task_transfer 的接力上限分支在目标解析之前有既有的审计+
   房间消息写入（达上限才触发），本轮未动其顺序，非本次被拒路径。
6. **template_fire 双保险不改变语义**：预检只拦 removed；不存在的成员预检
   放行、由 task_create 按原口径报 no such member；生效指派的计算
   （override 优先）与原调用点逐字一致。

## 测试结果（test_staging49.py，全新 DB 重跑）

35/35 全过（日志 test_run.log）：

- 0：工具数仍 65；task_list 入参 schema 含 room。
- 1：member_list 字段集合与生产 SELECT+week_done 口径逐字一致；连调 50 次
  全成功且 DB 句柄数 0 → 0 不增长。
- 2：gone49 注册→（先建模板/设额度/持卡）→移除后，task_create /
  task_schedule / template_upsert / template_fire / member_quota_set /
  task_transfer / skill_assign 七路逐个被拒且报错含
  "member is removed: gone49"，DB 零新增（任务/转交/模板/技能行/额度）；
  ghost49 仍报 "no such member"；对照组 pal49 七路全部正常。
- 3：两房间建卡，无参 task_list 每行带正确 room；room= 过滤精确；
  不存在房间名返回空列表；status / assignee_name / offset 行为不变。
- 4：py_compile 通过；与生产 hub.py 的 diff 只有上述三类改动（+71/−21）。

首跑曾有一条断言 FAIL（第 1 节成员数 ≥2）：全新 DB 的 init 只种 dispatcher
（owner 非 init 种子），属测试假设错误、非代码问题；已按实况修正断言为 ≥1
并在全新 DB 整套重跑全过，字段集合另行逐成员核验一致。
