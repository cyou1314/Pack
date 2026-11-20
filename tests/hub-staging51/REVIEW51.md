# REVIEW51 — EXTERNAL-AGENT Phase A（中枢底座）施工报告

施工范围：EXTERNAL-AGENT-BRIEF.md 定稿（冻结提交 1fdbda4）正文的 Phase A。
工作区：`tests/hub-staging51/`（hub.py / room_watch.py 均为生产副本上改，
端口 18798、全新 DB、harness 模式）。**未触碰生产、未 commit、未 push。**
基线留档：`hub_prod_baseline.py`（md5 f1a8cff1e920e23e3b1ce0af4ec2cf9e，
与生产逐字节一致）；与基线 diff 共 846 行，绝大部分为新增，删除行逐条
核对均为预期替换点。两文件 py_compile 通过。

## 一、新工具清单（65 → 71）

`grep -c " @mcp.tool"` = **71**。新增 6 个（全部调度员专属）：

| 工具 | 作用 |
|---|---|
| pair_code_create | 发配对码（只存哈希、明文只在返回值出现一次、24h 过期） |
| pair_code_list | 配对码元数据列表（状态推导 revoked>redeemed>locked>expired>unused；无码、无哈希） |
| pair_code_revoke | 吊销未兑换的码（守卫 UPDATE + rowcount 复核） |
| member_token_rotate | 应急轮换任意成员 token（明文只在返回值） |
| external_gate | hub_settings.external_enabled 查询/设置（总开关） |
| member_rename | 只改 display_name（name 不可变） |

> **部署注意：staging15 的工具数硬断言须同步从 65 改为 71**，否则全量
> 回归会在工具数断言处红。本增量上线时请一并修改。

## 二、逐条对照施工单正文的改动点

### 1. 迁移（init() 沿 PRAGMA 幂等范式）
- members 加四列：kind / display_name / bound_room_id / joined_msg_id。
- messages 加 reply_to_id（本期只加列，Phase B 用）。
- 新表：pair_codes（码哈希 UNIQUE、fail_count、locked_at 等全字段）、
  member_read_cursors（PK member_id+room_id）、hub_settings（key/value）、
  message_op_keys（Phase A 只建表）、pair_attempts（全局防扫滑窗数据源，
  独立于 pair_codes——不存在的码无行可计数）。
- hub_settings 幂等写入 external_enabled='1'（默认开）。

### 2. POST /pair 公开路由（FastMCP custom_route，只验码）
- 全程全局锁内执行；顺序：全局失败滑窗（600s 内 ≥30 次失败 → 429 +
  审计 pair_global_throttle）→ 总开关（关 → 403 + pair_rejected_gate_off）
  → 码校验。已知码失败统一走计数闭包：fail_count+1、满 5 次置
  locked_at（另写高优先级审计 pair_code_locked），一切失败统一回
  400 `{"error":"invalid pair code"}`，不回显码/token、不区分失败原因。
- 成功：建 kind='external' 成员，token 只在响应出现一次（中枢只存哈希）；
  joined_msg_id = 绑定房间当时最大消息 id；已读游标初始值 = joined；
  头像由服务端 avatar_for + 未占用组合自动分配。
- reclaim 码：只轮换 token_hash + 刷新 provider，joined_msg_id、游标、
  头像、created_at 一律不动。
- 名字三口径：已移除名 → 保留拒；在册内部成员名 → 拒；在册 external
  名 → 仅 reclaim 码可兑换（普通码在建码与兑换两处都拒）。

### 3. pair_code_create 口径
- member_name 校验（非空、≤64、无空白/斜杠/@）；provider ∈
  {codex, gemini, generic}；display_name ≤80；房间须存在；总开关关时
  拒发（审计 pair_code_gate_blocked）；审计 detail 只含
  code_id/member/provider/room_id/reclaim，**不含码**。

### 4. 成员类型与硬隔离（仅 kind='external' 生效）
- **白名单闸门**：build_server 把 mcp.tool 换成 _gated_tool 包装器，
  每工具入口先 authenticate，external 调用名单外工具即拒绝 + 审计
  external_tool_denied。EXTERNAL_ALLOWED_TOOLS 共 19 个（heartbeat、
  member_list、room_list、room_summary_get、room_read_new、room_post、
  message_edit、message_delete、attachment_put/list/get、task_list、
  task_claim、task_update、task_complete、task_block、run_start、
  run_finish、egress_check）。非 external 零行为变化；日后新工具默认
  对 external 关闭。desktop_*、task_transfer/task_accept_transfer、
  memory/skill/rule/review/egress_rule、全部调度员工具均未改代码，
  靠闸门全禁。
- **member_list**：全员视角新增 kind/display_name/bound_room_id（老成员
  kind 回 "internal"、display_name 回 name、bound_room 回 None）；
  external 调用方回脱敏视图，键恰为 {name, display_name, role, kind,
  provider, avatar, last_heartbeat}。
- **房间隔离**：room_list 只回绑定房；room_read_new / room_post /
  room_summary_get / attachment_list 跨房拒；attachment_get 按附件所属
  房校验；message_edit/delete 对 external 加绑定房校验。
- **入群水位**：room_read_new 强制 since_id = max(since_id,
  joined_msg_id)（tail 路径同样生效）；attachment_get/list 拒绝
  created_at 早于成员入群时刻的附件。
- **任务面**：task_list 对 external 强制覆盖入参（assignee=本人、
  room=绑定房）；task_claim 原子认领前预检（绑定房 + 指派本人 +
  非引擎标记，未指派卡也拒）；task_update note ≤4000 字；task_create
  指派 external 时跨房/引擎标记给**永久性拒绝**（错误以 "permanent: "
  开头，供 watcher 区分）；task_complete/block、run_start/finish 对
  external 加绑定房校验。
- **引擎标记 helper** `_is_executor_pinned`（标题 lstrip 后 startswith
  或正文包含 [M4A]/[ENG]/[ENG-image]），四处统一：task_create 指派
  检查、task_claim 预检、_auto_relay 钉死判定、未认领巡检钉死判定
  （后两处口径由「仅标题」统一为「标题或正文」）。
- **接力排除**：_auto_relay 候选查询加 kind<>'external'。
- **巡检宽限**：external 指派卡未认领宽限 2700s（45 分钟，见取舍），
  内部成员仍 240s；升级后只提醒不转派（既有行为）。
- **附件配额（external 独立）**：单文件 ≤5MB、滚动 24h 上传 ≤10 次、
  名下总量 ≤50MB；拒绝写审计 external_attachment_quota。全员既有
  口径（单文件 10MB、房间 500MB）未动。
- **频控**：external 发消息 ≤5/分、≤20/时、≤100/天（messages 表窗口
  计数，超限回 JSON {"error":"rate_limited","retry_after":N} + 审计
  external_rate_limited）；计数与落库在同一把全局锁内串行（room_post /
  task_update 对 external 整体走锁内实现），并发下不越窗（并发探针
  实证 10 路并发恰 5 过）。task_update 的 note 计入同一窗口（空 note
  续租不限流）。approval_request 不计普通窗，独立硬顶：单任务 ≤5、
  单成员 ≤10/时（审计 external_approval_cap，错误 JSON 带 scope）。
  egress_check 对 external ≤60 次/分（按其 egress 审计行计数，超限
  审计 external_egress_throttled）。
- **总开关**：external_gate 关闭时 authenticate 拒一切 external
  （/pair 403、pair_code_create 拒同步生效）；Muse 成员不受影响。
- **移除即失效**：沿用既有 authenticate 的 removed_at 拒绝，测试复核。

### 5. watcher 两道防线（staging51/room_watch.py，生产未动）
- 防线一：@ 转卡时 task_create 抛永久性拒绝（错误串含 "permanent:" /
  "no such member" / "member is removed"）→ 回帖拒因到房间（简短中文：
  跨房绑定 / 引擎标记 / 已移除 / 不存在 / 兜底五类），不进 W4 重试
  循环；瞬时失败保留既有重试。
- 防线二（既有、未改代码、已实测）：watcher 只认 author=owner 的消息，
  调度员 @ 天然不转卡；文件头注记已写明防误改。

## 三、测试结果（run_tests.sh，五套件全绿）

总计 **178 项断言，0 失败**，五套件退出码全 0。完整输出在 run_tests.log。

| 套件 | 结果 | 覆盖 |
|---|---|---|
| test_staging51.py（主） | 142/142 | 工具数 71、配对全链、水位、巡检宽限、白名单 52 工具拒绝矩阵 + 审计计数 + 无副作用、隔离、任务全链、伪造探针（task_card/decision 双向、借 claim_token 双向）、频控三窗口、approval 双硬顶、note 上限与共享窗口、附件次数配额、egress 频控、接力排除、配对码过期/吊销/锁定/reclaim、rotate/rename、总开关两态、移除失效、内部成员回归、秘密卫生（码/token 不出现在审计/消息/服务日志；库里只存哈希） |
| test_concurrency.py | 8/8 | 同码 8 路并发兑换 ×2 轮恰 1 成功（失败方 fail_count=7）；双码抢同名恰 1 成功；并行认领恰一胜；同消息 4 路并发删除恰 1 成功；external 10 路并发发帖恰 5 过 5 限流 |
| test_watcher.py | 10/10 | 跨房 @ → 拒因回帖恰 1 条且不重试；引擎标记 @ → 拒因回帖；正常 @ → 转卡成功且指派正确；调度员 @ → 不转卡（防线二） |
| test_migration.py | 10/10 | 基线 65 工具旧库 → 新版 init 迁移：四列 + reply_to_id + 五张新表 + 默认开关；旧成员 kind=NULL 读作 internal、旧 token 可用、消息不丢；新版 71 工具 |
| test_direct.py | 8/8 | 进程内直调验 hub 侧精确口径：external >5MB 被 hub 拒绝、恰 5MiB 放行、10×5MiB=50MiB 后总量拒绝 + 审计、内部 6MB 放行 / 11MB 被全员 10MB 上限拒绝 |

## 四、歧义与取舍

1. **巡检宽限取 2700s**：施工单给 30–45 分钟区间，取上限（最保守，
   最晚打扰群主）。
2. **MCP 传输层 4MiB 请求体上限（重要，既有限制、非本期引入）**：
   MCP SDK Streamable HTTP 默认 DEFAULT_MAX_REQUEST_BODY_SIZE=4MiB，
   FastMCP 4.1.8 未暴露覆盖参数，生产中枢同样受限——经 HTTP 的
   attachment_put，文件 >~3MB（base64 后超 4MiB）会在传输层被 413、
   到不了工具层。后果：本期 external 5MB 单文件上限在 HTTP 路径上
   实际不可达（hub 侧检查本身已由 test_direct.py 进程内直测精确
   验证）；全员 10MB 上限同理长期实际不可达。**是否在部署时一并处理**
   （如换传输配置/升级 FastMCP/加 REST 上传通道）请调度员与群主定夺，
   本期未动生产 run() 调用。
3. **egress 频控按审计行窗口计数**：并发瞬间可有少量越窗（量级
   ≤并发数），施工单未要求强一致，取简单实现。
4. **/pair handler 在 async 中持线程锁做同步 DB**：操作均为毫秒级短
   事务，staging 与预期生产负载下可接受；与全体工具共用同一把全局
   锁，语义一致。
5. **全局防扫滑窗数据源单设 pair_attempts 表**：不存在的码在
   pair_codes 无行可计数，只能独立记账；表结构已含 success 标记与
   ts 索引。
6. **approval 计数口径**：按 messages.task_id 计数——该字段由既有
   _approval_gate 在落库后回填，计数发生在后续请求，口径成立（已核
   代码顺序：回填 UPDATE 在 poster 校验之前执行）。

## 五、未做项

- **task_schedule 未加 external 校验**：施工单只点名 task_create。
  兜底已闭合——external 经 task_claim 三重限制（绑定房 + 指派本人 +
  非引擎标记）领不到违规卡；如需调度侧也给永久性拒绝，Phase B 或
  部署评审时补一行即可。
- Phase B 全部（inbox_pull/inbox_mark_read/inbox_send、message_op_keys
  启用、pack-agent.mjs、接入文档、App 两行微修）不在本期范围；
  message_op_keys 与 messages.reply_to_id 本期只建结构。
- 生产部署、全量回归、staging15 断言修改均归调度员（见第一节注记）。

## 六、文件清单（tests/hub-staging51/）

hub.py（改）、room_watch.py（改）、hub_prod_baseline.py（基线留档）、
harness.py、test_staging51.py、test_concurrency.py、test_watcher.py、
test_migration.py、test_direct.py、run_tests.sh、run_tests.log（全绿
输出）、REVIEW51.md（本文件）。mig_old/、mig_new/、direct_env/ 为测试
证据目录，run_tests.sh 每次运行会重置。
