# staging48 复检与自查（REVIEW48）

主题「让卡别躺死」：①新工具 member_quota_report（成员自报额度，含调度员+Gemini
定稿的防伪造四规则补遗）；②sweeper 新增未认领指派卡巡检。
基线：生产 hub/hub.py（64 工具）→ 本目录 hub.py（65 工具）。
状态：**仅 staging，未部署、未 commit/push**，等调度员审。

## 与生产 hub.py 的完整 diff 摘要

统一 diff 共 297 行：新增 268 行、删除/改写 14 行，10 个 hunk（全文见 staging48.diff）：

1. `init()` members 迁移 +4 行：PRAGMA 检查后 `ALTER TABLE members ADD COLUMN quota_source TEXT`（范式同 provider/login_state；老数据留空 = 来源未知）。
2. 自动接力常量区 +72 行：`UNCLAIMED_GRACE=240`、`UNCLAIMED_HEARTBEAT_STALE=300`、`QUOTA_RESET_PAST_TOLERANCE=300`、`QUOTA_RESET_FUTURE_TOLERANCE=40*86400`、`QUOTA_ANOMALY_JUMP=30` 五常量；新函数 `_validate_quota_report()`（自报入参校验）、`_apply_quota(..., source)`（set/report 共用的落库+来源标记）。
3. `sweep_once()` 末尾 +69 行：未认领指派卡巡检段（详见改动二）。
4. `member_quota_set` 改写：落库改走 `_apply_quota(source="dispatcher")`，审计 detail 尾加 `source=dispatcher`，返回行加 `quota_source` 字段。**校验口径一字未动**（pct 0–100、reset_at>0、extra>=0），防回归优先。
5. `member_quota_report` 新工具 +104 行（详见改动一）。
6. `task_block` +10 行：quota 分支落 fact 熔断标记；返回 dict 加 `quota_marked_exhausted` 键（加键不改既有键）。
7. `member_list` SELECT +1 字段：`quota_source`（加字段，App/老调用方不受影响）。

无 schema 变更除 quota_source 一列；无既有工具签名变更；工具数 64→65。

## 改动一：member_quota_report + 防伪造四规则（补遗口径）

- 身份只认 Bearer token 的 me，**无 member_name 入参**，物理上改不了别人；`require(me)` 即可（调度员 token 调用 = 报自己，无害）。
- 字段口径与 member_quota_set 对齐（只更新给了的字段 + 刷新 quota_updated_at）；无「报 0 清空」语义（0 是合法如实值）。
- 校验比调度员通道严：pct 0–100（NaN 拒绝）、extra 须 >=0 整数、week_reset_at 须落在 [now−300s, now+40d]——重置时刻语义上必在未来，只容时钟偏差；**报成过去值 = 人为制造「周期已到期」，直接整笔拒绝**（补遗规则 1 的洗白防线）。
- 规则 1+2+3 合并为一条单调性：**同周期内自报 pct 只升不降**，不论已存值来源是 self/dispatcher/fact。被拦的 pct 字段忽略、同报其他字段照常生效、写 `member_quota_drop_blocked` 审计（detail 带 stored_source）；只报 pct 一项时整笔 ValueError（审计先落库再抛错）。唯一回落例外 = 真周期滚动：已存 reset_at <= now（实际到期）且新报 reset_at > 已存值。
- 规则 1 延伸（自查补强，超出补遗字面、已在代码注释与本文注明）：**自报 reset_at 只许往后移**。回挪 reset_at 能加速「到期」配合下一报洗白，与降幅同罪：回挪字段忽略 + `member_quota_reset_blocked` 审计；真滚动要求新值更大、天然不受限；调度员 set 不受此限。
- 规则 2 落地：task_block(fail_class='quota') 在既有接力逻辑之前，把本人 `quota_week_used_pct=100, quota_updated_at=now, quota_source='fact'`，单独审计 `member_quota_exhausted ... source=fact`。选「置 100」而非新标记列：与既有字段最兼容——接力候选判定（<90%）、巡检条件 B（>=90%）、App 展示（100%）全部自动按耗尽处理；代价是覆盖掉旧 pct 值（审计链可复盘，调度员可纠偏）。
- 规则 3 落地：member_quota_set 无任何单调限制、可高可低随时覆盖（含解封 fact 熔断、纠偏）；其设值受规则 1 保护、自报不得调低。
- 规则 4 落地：自报 pct 单次跳涨 >30 个百分点且「自上次额度写入（quota_updated_at，无则成员 created_at）以来该成员无 task_complete 审计」→ 写 `quota_anomaly` 审计，数值照收。**只做审计、未发房间提示消息**（补遗二选一，审计为必备项；房间消息留待调度员定）。
- quota_source 落点：_apply_quota 每次写入同步刷新（self/dispatcher）；fact 熔断直接 UPDATE 置 'fact'；member_list 与两工具返回行均带此字段。

## 改动二：sweeper 未认领指派卡巡检

- 搭现有 sweeper 60 秒周期，位于 sweep_once() 租约回收段之后、同一事务提交；不新起线程。
- 扫描：status='open' 且 assignee 非空且指派人未移除，且 (next_claim_at IS NULL OR <= now)。
- 排除：标题开头或正文含 [M4A]/[ENG]/[ENG-image] 的钉死卡（_auto_relay 的既有口径是标题前缀，本段取并集——标题前缀 OR 正文包含——只扩排除面、不扩动作面，宁可少动不可误动钉死卡）；已有 pending 转交的卡（正在等人接受，不重复处置）。
- 时机：eligible_at = max(updated_at, next_claim_at or 0, sched_at or 0)，eligible_at + 240s <= now 才判断。
- 触发：A 指派人 last_heartbeat IS NULL 或距今 >300s；B 指派人 quota_updated_at 在 900s 内且 week_used_pct >= 90（常量复用 RELAY_QUOTA_FRESH / RELAY_QUOTA_HEADROOM）。额度过期/缺失绝不触发 B；心跳新鲜且额度未见底绝不动。
- 动作：`with _lock` 内复检（仍 open、同指派、无 claim_token）后调既有 `_auto_relay`（自带 TRANSFER_CAP=3、候选口径、转交行 actor='auto-relay' 留痕与房间通知）。无合格候选 → 不清 assignee，只在首次命中时写 `task_unclaimed_escalate` 审计 + 房间一条调度员提示消息（kind='chat'、作者 dispatcher，与 _auto_relay 的通知同形态）。
- 去重实现（自选，已按简报授权）：按「任务 id + 指派人 id」查审计 detail 精确前缀（detail 由本段写死格式 `task <id> assignee_id=<aid> ...`，前缀带空格分隔、不会串到 task 50 这类同前缀 id）；同一卡换指派人后可对新指派人再升级一次。未新增状态列。

## 慢工复检清单逐条自查

- **鉴权边界**：report 无 member_name 入参、只写 me 行（测试 1 以传参伪造实证无此路径）；member_quota_set 仍 require dispatcher（测试 1 成员 token 调用被拒）；巡检与熔断均为服务端内部动作、无新外部入口；已移除成员 authenticate 直接 None。
- **并发原子性**：report 的「读已存→单调/异常判断→写入→审计」整段走 _lock（自查中发现初版未加锁、同成员并发自报可绕单调性，已补并复验）；巡检记账段走 _lock + 状态复检（与既有接力段同纪律）；task_block 熔断与 block 本身同事务。
- **限额口径**：条件 B 的新鲜窗 900s 与 >=90% 直接复用 RELAY_QUOTA_FRESH / RELAY_QUOTA_HEADROOM 常量，与接力候选 <90% 严格互补；失联 300s 按简报独立常量（严于候选口径的 180s，避免把慢启动误判失联）；宽限 240s 按简报。
- **数据完整性**：quota_source 迁移幂等（PRAGMA 检查）；非法自报整笔拒绝且库未变（含 quota_updated_at 不刷新，测试 2/A1 双证）；被拦字段不落库、只落审计；熔断置 100 的旧值丢失已在上文如实注明（审计可复盘）。
- **审计**：report（source=self）、set（source=dispatcher）、exhausted（source=fact）、drop_blocked、reset_blocked、anomaly、unclaimed_escalate、auto_relay 既有审计——本增量每条写路径都有痕且带 source/cond 标记。校验类拒绝（非法值）不写审计，与既有工具口径一致（如 member_login_state_set 的坏状态拒绝）。
- **与既有纪律一致性**：member_quota_set 校验与行为零改动（仅审计 detail 尾标与落库函数复用）；_auto_relay 本体零改动、巡检只是新调用方；sweeper 既有两段（转交过期/租约回收）零改动；钉死卡排除沿用执行体标记口径；升级消息形态与既有接力通知一致；常量全部写死代码（与「改参数 = 改代码 + 群主批准」的既有纪律一致）。

## 测试实测结果（本目录日志：test_run.log / test_antiforge.log）

主套件 test_quota_unclaimed.py（对应简报测试 1–8）：**28/28 PASS**
1. 自报只能改自己 + set 仍调度员专属、两边 source 标记 — PASS（伪造 member_name 入参不存在、成员调 member_quota_set 被拒、审计 source=self/dispatcher 各就其位）
2. 非法自报（150 / −1 / 负 extra / 荒谬时间戳 ×2 / 空报）整笔拒绝且库未变 — PASS
3. quota block 置 100+fact 语义前置：熔断者不再被选为接力候选、调度员纠偏后恢复候选资格 — PASS（三段接力链实证）
4. 巡检不误伤：心跳新鲜+额度正常、过宽限期纹丝不动 — PASS
5. 条件 A：停心跳 >300s → _auto_relay 转给合格候选（转交行 actor='auto-relay'）；无候选 → 连跑两轮 sweeper 只升级一条、assignee 不变 — PASS
6. 条件 B：额度新鲜 95% → 接力；quota_updated_at 改到 901s 前 + 95% → 不触发 — PASS
7. 钉死卡（标题+正文带 [M4A]）在条件 A 下不动 — PASS
8. 宽限期边界：eligible_at 未过 240s 不触发；带退避的卡以 next_claim_at 为起点算宽限、退避过后才接力 — PASS

补遗套件 test_quota_antiforge.py（对应补遗补测 ①–⑥）：**14/14 PASS**
① 同周期报低被拒、库未变、drop_blocked 审计有痕 — PASS
② 伪造未来 reset_at 洗白：pct 降幅被拦（blocked_fields 回显）；reset_at 回挪另行被拦（reset_blocked 审计）— PASS
③ 真到期（已存 reset_at 已过）+ 新 reset_at 更大 → 允许回落 — PASS
④ quota 熔断后自报低值无效；调度员 set 解封（source=fact→dispatcher）；解封后自报再调低仍被拦（规则 3）— PASS
⑤ 跳涨 35 点无交活 → quota_anomaly；跳涨 35 点但期间有 task_complete → 不触发 — PASS
⑥ 正常小幅上涨（+5）照收、无异常审计 — PASS

回归（简报测试 9）：`run-all-suites.sh --hub tests/hub-staging48/hub.py` 默认全套
16 套件跑完（日志 regression.log）：**15 套 PASS**（16/17/18/19 接力/23/24/38/39/
40/41/42/43/44 watcher/45/46），唯一 FAIL 是 staging15，且其全部输出里只有一行
失败——`FAIL 工具数 64 got 65`（test_skills.py 的硬断言），即新增工具的预期
计数漂移，非行为回归；部署时同步改断言为 65 即可（见文末待办）。

## 事故如实记录（与本增量代码无关，但发生在本次施工期间）

清理 staging 进程时误用 `pkill -f "hub.py serve"`（命令行自匹配的老坑），
连带杀掉生产 pack-hub 进程。发现后立即 `systemctl start pack-hub` 恢复：
service active、8794 恢复监听、生产 pack.db integrity_check=ok（成员 5、
任务 54，与事故前台账一致）。生产 hub.py 与 pack.db 内容未被写入/改动，
仅有数分钟级进程中断。此后一律按 ss 查 PID 精确杀 staging 进程。

## 未做 / 待调度员定

- quota_anomaly 只写审计、未发房间提示（补遗二选一的必备项已做，可选项留定）。
- 未 commit/push、未部署；staging 进程（18795）已关闭，本目录保留 hub 副本、
  测试脚本、日志与 pack.db 供审。
- 部署时注意：既有套件 staging15 的 test_skills.py 硬断言「工具数 64」，
  上线时须随增量同步改为 65（与 staging47 加工具时的同例处置）。
