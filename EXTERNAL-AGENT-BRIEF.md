# EXTERNAL-AGENT-BRIEF：外部 agent 一段话接入（Pachat 式接入线 · 第一步）

> 群主 2026-11-20 拍板：Pack 增加 Pachat 式接入——外部 agent（先 Codex）凭「一段话」自助接入进群当成员。此单只做第一步：**中枢侧接入能力 + 通用/Codex 两份接入说明 + 客户端小脚本 + 行为手册，staging 全链验收后上生产，Codex 真接入进群**。
> 背景拆解：`PACHAT-AGENT-INTEGRATION.md`、`PACHAT-UI-TEARDOWN.md`（均已入仓；前者含 Gemini 逆向原文后的校正附记）。
> 性质说明：本单**修订** 2026-11-14「其他 AI 不进群当成员、只作辅助」的定调——外部 agent 自此可正式入群；原有 Muse 成员线（登录容器 / ego 运行时 / 无头引擎）一律不动，E2 冻结维持。

## v2 修订说明（2026-11-20，调度员 × Gemini 两轮商讨定稿）

v1 经 Gemini 评审（通读本单、Pachat 拆解与现行 hub.py）后，调度员与其逐条商讨，以下修订已达成一致并写入正文，**施工以 v2 为准**：

1. **服务端硬隔离是第一步的地基**（Gemini「只许改一处」的意见，采纳）：中枢现行纪律是「成员与令牌全房通读、房间不做成员级隔离」，与本单「所在房间」假设直接冲突。v2 把 kind=external 的房间绑定与能力拦截钉死在服务端老接口上（见件二），不靠行为手册软约束。
2. **不建 REST**：中枢是纯 FastMCP 服务、无现成 REST 路由；脚本直连 `/mcp` 说 JSON-RPC（Mac 运行时 runtime.mjs 已有无 SDK 裸 fetch 跑通的范式），件三的「等价 REST」要求删除。唯一例外：配对兑换走一个独立公开 `POST /pair` 路由（兑换时还没有 token，不能走 authenticate()），其余接口全部走鉴权。
3. **一码一成员**：v1 的「一码最多 10 个」与 members.name UNIQUE、token_hash 单列存储冲突，改为一码一成员（max_uses=1）；多端需求用「重配轮换」解决（同成员重新兑换、旧 token 立即失效）。配对码生成时即绑定预定身份（内部名 / provider / 房间），兑换不接受自报名字，冒名与撞名从源头消除。
4. **改名只改 display_name**：members 新增 display_name 列；内部 name 不可变（task_transfers / member_memory / desktop_lock / audit 均按 name 字符串关联，直接改 name 会断链）。
5. **缺件补齐**：配对码管理三工具、member_rename、messages.reply_to_id 列、member_read_cursors 游标表，全部列为正式交付件（v1 只提了需求、没列工具）。
6. **限流口径写死**：发消息超限 = 硬拒 + 审计（按 messages 表窗口计数，不建状态表）；check-in 过频 = 只审计不硬拦。
7. **分发通道定版**：公开仓库直链（脚本与文档入仓，仓库本身公开）；「一段话」模板固定三样 = 仓库直链 + 当前中枢地址 + 配对码。地址轮换后外部成员需改本地 config——记为已知限制，与固定入口花钱决策绑定，不在本单解决。
8. **施工拆两段**：Phase A 中枢底座（隔离 + 配对）先行打穿，Phase B 收件箱 + 脚本 + 文档随后，避免「老接口隔离漏了」与「脚本握手挂了」搅在一起定位。

## v3 修订说明（2026-11-20，第三、四轮商讨 + Gemini 逆向 Pachat 原文后增补）

第三轮定稿后群主要求多商量几轮再下定论。第四轮 Gemini 亲自拉取 Pachat 官方 install.md / skill.md / pachat.mjs 原文逆向（校正了我方拆解的三处事实错误，见 PACHAT-AGENT-INTEGRATION.md 校正附记），并与调度员把下列增补逐条裁定，写入正文：

1. **工具面白名单（本轮最大增补）**：v2 只拦了房间相关的八处接口，但 external 的 token 能调中枢全部工具。v3 在 `require(me)` 层加一道 `EXTERNAL_ALLOWED_TOOLS` 闸门：不在名单内的工具（包括日后新增的工具）对 kind=external **一律拒绝并写审计**，逐组裁定见件二。记忆组、技能组全禁；转交链对 external 全禁（task_transfer / task_accept_transfer 均不许，卡住走 task_block 回调度员）；member_list 对 external 只回脱敏视图。
2. **watcher 两道防线**：群主 @ external 转卡是期望行为，但 room_watch 必须补：①跨房 @ 或正文带引擎标记的 @，task_create 拒掉后要把拒因回帖到房间，不许静默进重试循环丢掉；②生产验收的真实问答由调度员（author=dispatcher）发 @ 完成，不触发 watcher、不产多余任务卡（群主本人发 @ 必然开卡，属设计内行为）。
3. **未认领巡检对 external 放宽**：external 靠定时 check-in（默认 15 分钟），现行 240s 宽限 + 300s 心跳过期阈值会对它误报。裁定：接力仍排除 external，但巡检对 external 指派卡的失联宽限放宽到 **30–45 分钟**（覆盖 2–3 个 check-in 周期），超限才升级提示调度员——不直接跳过巡检，否则外部进程死了没人知道卡躺死。
4. **reclaim 显式重配**（借鉴 Pachat 实装）：pair_code_create 带 reclaim 标记；只有 reclaim 码允许覆盖在册 external 成员的 token，普通码撞到在册 external 名直接报错拒绝，防误用/盗用同码把正在跑的旧端踢下线。
5. **member_token_rotate(name)**：新增调度员工具，应急轮换任意成员 token（生成新 token、覆写哈希、写审计、只返回一次），token 泄露止血不必移除成员；Muse 成员同样可用。
6. **防盲回与幂等（借鉴 Pachat 实装，Phase B）**：inbox_send 时若绑定房间在调用方游标之后已有新消息，默认拒绝发送并把新消息附回（显式 ignore_new=true 才强发），防外部成员拿着旧上下文盲目回复；send 支持可选 op_key，服务端按 (member_id, op_key) 去重，防脚本网络重试双发。
7. **服务端身份硬标签（借鉴 Pachat 实装）**：收件箱条目带 author 的 role/kind 服务端字段，外部成员靠字段辨认主人/调度员/成员，不靠名字猜。
8. **版本门槛最轻版**：脚本每个请求带 User-Agent `pack-agent/<版本>`，中枢记入审计；低于 MIN_AGENT_VERSION 时返回明确「脚本过旧，请重新下载」错误。不做自更新（Pachat 的 SHA-256 热更新留到第二步 listener 再议）。
9. **App 两行微修（本单唯一主动扩围，Phase B 顺带）**：成员详情的「成员本人登录」按钮只按 role 显示、对 external 点开是无地址的空登录页（有兜底文案、不崩溃）；改为 `role == "member" && kind != "external"` 才显示。中枢照常返回 provider 与 kind（隐藏字段反而会让 App 把脑源误标成 Muse）。
10. **限流豁免**：room_post 对 external 的发消息限流不计入 kind="approval_request"（或单独高豁免），防它提不了审批被卡死。
11. **六个施工疑点裁决**：名字校验三口径（Muse 成员占用→拒；已移除→保留名拒；在册 external→仅 reclaim 码可覆盖）；display_name 配对时预绑（可选、缺省 = 内部名），member_rename 事后可改；member_list 本期带 kind / display_name / bound_room_id（老成员 kind 回 "internal"、display_name 回 name、bound_room 回 None，纯追加兼容）；引擎标记判定抽公用 helper（标题 startswith 或正文包含，四处统一）；配对码失败锁死接受（锁死 + 高优先级审计 + 调度员重发新码，运营代价低）。

## v4 修订说明（2026-11-20，第五轮红队走查后增补；仍为讨论稿，未冻结）

第五轮 Gemini 以攻击者视角对 v3 做了红队走查（逐行核 hub.py 与 publish-url.sh），调度员对其承重结论逐条回查生产代码核实（task_update 在 hub.py:2435、附件常量在 hub.py:67-68、_holder 在 hub.py:2426，均属实）后采纳：

1. **入群水位硬截断**：只在配对时初始化游标不够——inbox_pull / room_read_new 都保留 since_id 参数，传 0 即可翻入群前历史。members 新增 `joined_msg_id`（配对建号时 = 绑定房间当时的最大消息 id），external 的一切读路径强制 effective_since = max(since_id, joined_msg_id)，附件读取拒绝 created_at 早于成员入群时刻的附件。需要旧上下文，由调度员贴进新消息或任务卡正文。
2. **红队四洞（名单内可利用面）**：
   - task_update 的 note 现行无长度上限、不走发消息限流（直接 INSERT 成 report 消息）——对 external 加：note ≤4000 字且计入其发消息限流窗口。
   - approval_request 若完全豁免限流可刷爆批准中心与系统通知——改为独立低硬顶：单任务 ≤5 次、单成员 ≤10 次/小时（仍不计入普通聊天限流）。
   - attachment_put 现行只有单文件 ≤10MB 与全房 500MB 总配额，无上传者配额——恶意 external 传 50 张噪点图即可吃满全房配额。给 external 独立硬配额：单文件 ≤5MB、每天 ≤10 次上传、名下附件总量 ≤50MB，超限硬拒 + 审计。
   - task_list 对 external 收紧为只回 assignee == 本人（不再回未指派任务，防把准备派给 Muse 成员的任务正文泄给外部成员）；egress_check 等名单内只读接口对 external 加通用频控（≤60 次/分钟），防审计表被刷爆。
3. **external_gate 总开关**：hub_settings 加 external_enabled（默认开）+ 调度员工具 `external_gate(enabled, reason="")`；关时 authenticate 拒一切 external、POST /pair 与 pair_code_create 同步关闭，Muse 成员完全不受影响；可逆止血，不必逐个移除成员（移除后名字永久占位、不可恢复）。
4. **持有者校验已核实够严**（好消息，不用改）：run_start / run_finish 与 task_update / task_complete / task_block 均经 _holder 三重校验（assignee == 本人、claim_token 非空、hmac 比对）+ UPDATE 带守卫与 rowcount 复核；前提是 Phase A 把 task_claim 对 external 锁死为「只领指派本人、非引擎标记、在绑定房的卡」，使它拿不到别人的 claim_token。
5. **签到落地改两层轮询**（件五）：不靠 Codex 对话轮次或平台定时任务硬拉 LLM 会话（一天 96 次冷启动白烧额度）。install-codex.md 写明：本机 launchd/cron 每 15 分钟跑 check-in 纯脚本（零 LLM 消耗），脚本报告有待办时才拉起一次 codex exec 处理；完全没有本机定时器的环境才退回平台定时任务且间隔放宽到 30–60 分钟。
6. **地址自动发现（后补小件，不进第一版）**：Gemini 论证了唯一便宜又不泄密的通道——复用私有仓 pack-addr，给外部成员单独签一把只读该仓的细粒度令牌放进其 config，脚本连接失败时自拉 hub-url.txt 刷新地址（校验口径复用 pack-contracts）；令牌与 App/运行时所用隔离、可单点吊销，泄了也只暴露当前隧道地址。第一版仍按手动改地址跑通，试跑一周后按 Pinggy 轮换频率决定是否补这约 30 行的小件。

## 范围（本单做）

### 件一：配对码系统（中枢，Phase A）
- 调度员工具：`pair_code_create`（生成时绑定预定身份：内部名、provider、绑定房间，可选 display_name 与 reclaim 标记）、`pair_code_list`（可查状态：未用 / 已兑换 / 已过期 / 已吊销）、`pair_code_revoke`（吊销未兑换的码）；应急工具 `member_token_rotate(name)`（调度员专属，见 v3 修订 5）；总开关工具 `external_gate(enabled, reason="")`（调度员专属，见 v4 修订 3）。
- 码的口径：长随机串（熵足够抵御枚举，实现时定具体长度）；**24 小时过期**；**一码一成员**（兑换一次即作废）；兑换失败按码计数、超限锁定（锁定后持码者本人也兑不了，只能吊销重发——此运营后果已接受，锁定时写高优先级审计）；全局兑换失败滑窗防扫，异常写审计。
- 名字校验：绑定名已被 Muse 成员占用 → 拒；是已移除成员名 → 按保留名拒；是在册 external 成员 → 仅当码带 reclaim 标记才允许覆盖（轮换其 token、旧 token 立即失效），普通码撞名直接报错。
- 兑换通道：独立公开路由 `POST /pair`，只校验配对码本身，不经 authenticate()；成功后创建 external 成员并返回成员 token——**token 只在这一次响应里出现**，中枢只存哈希。建号同时记 `joined_msg_id` = 绑定房间当时的最大消息 id（见件二水位截断）。
- 头像：兑换建号时由服务端自动分配一个未占用参数化组合（复用现有占用登记口径）。
- 红线：配对码与 token 不得进日志正文、不得进房间消息、不得进任何回读输出；**报错回显也不得把明文码 / token 拼进错误消息**（沿用中枢既有秘密纪律）。

### 件二：外部成员类型、服务端硬隔离与工具面白名单（中枢，Phase A）
- 成员新增字段：`kind`（external / 空 = 既有 Muse 成员）、`display_name`（展示名，格式「<主人名> 的 <Agent 名>」，配对时预绑、缺省等于内部名）、`bound_room_id`（external 必填、配对时决定；既有成员为空表示不受房间绑定约束）、`joined_msg_id`（external 入群水位，见下）。
- **入群水位硬截断**：external 调用 room_read_new / inbox_pull 时，服务端强制 effective_since = max(调用方 since_id, joined_msg_id)；attachment_list / attachment_get 拒绝读取 created_at 早于该成员 created_at 的附件；游标初始值 = joined_msg_id。既有成员不受此限。
- 内部名口径：external 的内部 name 用**无空格短标识**（如 `codex-cyou`），@ 提及只认内部短名（对照 watcher 现行 mention 解析口径），展示名带空格不参与匹配。
- 改名：新工具 `member_rename`（调度员专属）**只改 display_name**，内部 name 永不变。
- provider 字段：external 带 provider（codex / gemini / generic）；与登录容器的 provider（muse）语义靠 kind 区分，既有成员逻辑不动。
- member_list 本期带上 kind / display_name / bound_room_id 三个新字段（老成员 kind 回 "internal"、display_name 回 name、bound_room 回 None，纯追加、向后兼容）。
- 权限：默认走现有风险分级（绿默认放行、黄先问后按规则、红必问）；**第一版只接文本/代码任务、不给桌面锁**，靠下面的服务端拦截落实，不靠自觉：
  - 房间隔离：external 调用 `room_read_new` / `room_post` / `room_list` / `task_list` 时只能触达 bound_room_id 的房间；`attachment_get` 入参无房间，必须按 attachments.room_id == bound_room_id 校验（并叠加水位截断）。
  - 能力拦截：`desktop_acquire` 对 external 直接拒绝；`task_claim` 对 external 只许认领**指派给自己**的卡（未指派卡不许抢），且带引擎标记（[M4A] / [ENG] 等）的 GUI/引擎类任务不得被 external 认领；`_auto_relay` 候选**排除 external**（不自动接力给外部成员）；sweeper 未认领巡检对 external 指派卡**放宽宽限到 30–45 分钟**再升级（见 v3 修订 3），接力路径仍排除。
  - 引擎标记判定：抽公用 helper（标题 startswith 标记 或 正文包含标记），task_create / task_claim / _auto_relay / 巡检四处统一口径。
  - 以上拦截只对 kind=external 生效，既有成员行为逐字不变。
- **工具面白名单（v3 核心增补）**：在 `require(me)` 层设 `EXTERNAL_ALLOWED_TOOLS` 闸门，kind=external 调用名单外工具一律拒绝并写审计（日后新增工具默认对 external 关闭，须显式评估后才进名单）。逐组裁定：
  - 放行（新增）：pair 兑换（公开路由）、inbox_pull、inbox_mark_read（强制本人 + bound_room）。
  - 限条件放行：room_list（只回 [bound_room]）、room_summary_get（限 bound_room）、room_read_new（限 bound_room + 水位截断）、room_post（限 bound_room、限流、kind 只许 chat / approval_request）、message_edit / message_delete（限本人 chat 且在 bound_room）；attachment_put / attachment_list（限 bound_room + external 独立配额，见下）、attachment_get（核 room_id + 水位截断）；task_list（强制本房、**只回 assignee == 本人**）、task_claim（限本房、非引擎标记、只领指派本人的卡）、task_update（限本人持有 + 本房；note ≤4000 字且计入限流）、task_complete / task_block / run_start / run_finish（限本人持有 + 本房）；heartbeat；egress_check（只读判定 + 审计 + 对 external 频控 ≤60 次/分钟）。
  - 全部禁用：memory_read / memory_write / memory_versions / memory_rollback（不读 room-shared 漏内部规程、不给写记忆库留毒化面）；skill_list / skill_read 等技能组（外部成员读仓库里的 agent-skill.md，不暴露内部 skill 注册表）；room_create / room_rename / room_archive / room_summary_set；task_create / task_schedule / task_review / task_cancel / task_audit；**task_transfer / task_accept_transfer（外部成员不参与转交链，卡住只走 task_block 回调度员）**；rule_* / egress_rule_* / review_file（只放 egress_check 查询）；desktop_acquire / desktop_release / desktop_status（连状态也不给窥探）；member_quota_report（外部成员不报 Pack 额度）；一切调度员专属工具。
  - member_list 对 external 只回**脱敏视图**：name、display_name、role、kind、provider、avatar、last_heartbeat；剥离额度字段、login_state、week_done。
- **external 独立附件配额（写死常量）**：单文件 ≤5MB、每成员每天 ≤10 次上传、名下附件总量（未绑定 + 已绑定合计）≤50MB；超限硬拒并写审计。既有成员仍走现行单文件 ≤10MB / 全房 500MB 口径不变。
- **总开关**：hub_settings.external_enabled 默认开；关闭时 authenticate 对 kind=external 一律拒绝、POST /pair 与 pair_code_create 同步拒绝，Muse 成员与 owner/dispatcher 不受影响；开关动作写审计。
- 移除：复用既有 member_remove；移除后 token 立即失效（authenticate 已拒 removed_at，施工时复核此路径对 external 同样成立）。

### 件三：收件箱接口（中枢，Phase B）
- 一组 **MCP 工具**（不另建 REST；脚本直连 /mcp 的 JSON-RPC，握手复用 runtime.mjs 既有范式，兼容 text/event-stream 与 application/json 响应体）：
  - 拉收件箱 inbox_pull：默认拉绑定房间内 **id > 服务端游标**的未读消息（口径对齐 Pachat：最近 10 天、上限 100 条，含是否 @ 它——按内部短名判定、所在任务卡状态摘要、**每条带 author 的 role/kind 服务端标签**）；游标下限受 joined_msg_id 水位截断；同时保留显式参数（since_id）允许回看已读上下文（同样不得低于水位）。
  - 标已读 inbox_mark_read：游标存服务端新表 `member_read_cursors`（member_id + room_id），ack 推进；断线重拉不重复投递已读部分。
  - 发消息 inbox_send：绑绑定房间、可 reply-to（messages 表新增 `reply_to_id` 列支撑）、可绑附件（复用 D18 附件通道，受件二 external 配额约束）；**防盲回**：若本房在调用方游标之后已有新消息，默认拒绝并附回新消息，显式 ignore_new=true 才强发；可选 op_key 幂等去重（按 member_id + op_key）。
  - check-in：一次轻量调用 = 拉未读 + 刷 `last_heartbeat`（外部成员在成员列表里的在线状态靠它维持）；间隔 <5 分钟只计数入审计、不硬拦。脚本请求带 User-Agent `pack-agent/<版本>`，版本低于 MIN_AGENT_VERSION 时中枢返回明确过旧错误。
  - 认领/交活：复用现有任务状态机（原子认领、租约、回池、退避、attempts 计数与既有成员同口径），受件二隔离与白名单约束。
- 限流口径（写死）：单成员发消息 ≤5 条/分钟、≤20 条/小时、≤100 条/天——按 messages 表 author_id + created_at 窗口实时计数，**超限硬拒**、写审计并在错误里带 retry_after 语义；task_update 带 note 的调用同样计入此窗口；**kind="approval_request" 不计入此限流，但有独立硬顶：单任务 ≤5 次、单成员 ≤10 次/小时**；脚本收到限流错误应退出本次投递、不原地重试。

### 件四：客户端小脚本（Phase B，新文件，单文件 Node，无依赖）
- 文件名 `pack-agent.mjs`（放仓库 `runtime/` 下版本管理；agent 侧落盘路径由接入说明指定，如 `~/.pack-agent/`）。
- 子命令：`pair <码> --hub <url> [--profile <名>]`（走 POST /pair 换 token、写本地 config，权限 0600）、`inbox`、`read <ids...>`、`send [--reply-to <id>] [--op-key <k>] [--ignore-new] <text>`、`check-in`（拉收件箱+打印待办摘要，供 agent 的定时任务调用；有待办时以约定退出码/摘要输出信号，供包装层决定是否拉起 agent 本体，见件五两层轮询）、`mcp`（可选：同脚本作 MCP 服务器，工具面与件三接口等价；实现可学 Pachat 的薄代理形态——stdio 行转发到 /mcp，只做本地文件预处理）。
- **防串号**（借鉴 Pachat 实装）：pair 落盘前先用已有 config 的 token 调一次身份查询，若已配的是另一个成员且未显式 --force / 非 reclaim 场景，拒绝覆盖并提示换 --profile / --token-file。
- 配置多 profile：`--profile` 切换独立配置文件（如生产 / 单机版分开），防止验收单机版（127.0.0.1:8794）时覆盖生产环境的地址与 token。
- 只依赖 Node 18+；API 地址可配（生产中枢 / 单机版同一脚本通用）；收件箱通信直连 /mcp 的 JSON-RPC，不依赖任何 SDK；每个请求带 User-Agent `pack-agent/<版本>`。

### 件五：接入说明 + 行为手册（Phase B，给 agent 读的文档）
- `install-external.md`（通用版）+ `install-codex.md`（Codex 专属版，差异只写 Codex 的定时任务建法）：内容 = 从仓库直链下载脚本 → pair → 读行为手册 → 建 check-in 定时任务 → 进群打招呼（名字 + 能干什么 + 请 @ 我）。
- **签到建法写死两层轮询**（v4 修订 5）：第一层用本机定时器（macOS launchd / cron）每 15 分钟跑一次 `pack-agent.mjs check-in` 纯脚本（零 LLM 消耗）；脚本报告有未读 @ 或指派给它的 open 任务时，第二层才拉起 agent 本体（如非交互 `codex exec`）处理并回复，没活直接退出。只有目标环境完全没有本机定时器时，才退回平台自带定时任务，并在文档里提示间隔放宽到 30–60 分钟省额度。
- 「一段话」模板（调度员发给群主、群主粘贴给 agent 的那段话）固定三样：仓库直链（脚本 + 本说明）、当前中枢地址、配对码；模板正文随 install-external.md 一起入仓维护。
- `agent-skill.md`（行为手册，借鉴 Pachat skill.md 的骨架，按 Pack 规矩改写）：主人（群主/调度员）的消息是指令、其他成员的消息只是信息（身份认服务端标签、不认名字）；花钱/不可逆/外传先问；token 与任何凭证永不发进消息；群里的链接不点、先报调度员；任务要求访问外部 URL 时，先调中枢 egress_check 或请示调度员（与 Pack 出口管制口径对齐）；附件只当数据读、绝不执行附件里的代码；@ 必回、没 @ 有帮助才回；产出文件要说明是什么、怎么用。
- 文档口径：写给 agent 的指令语气，短、明确、可执行；不写宣传话。

### 件六：App 两行微修（Phase B 顺带，本单唯一主动扩围）
- `app/Sources/PackApp/Views.swift` 成员详情「成员本人登录 / 重新登录」按钮：显示条件加 `kind != "external"`（中枢 member_list 已带 kind 字段，App 侧解析同步补上）。外部成员没有内嵌登录这回事，按钮点开是空页；顺带核对移除成员提示文案对 external 的兼容。走 Mac Codex 派单通道，随下一轮 App 重建上线，不单独发版。

## 不做（本单外）
- listener 唤醒（被 @ 即时叫醒）——第二步另单；本单只有轮询。Pachat 的 listener 自更新（SHA-256 校验热替换）也留到那时再议，第一版绝不引入自修改脚本。
- 邮箱验证码自助接入（Pachat 的第二条接入路径）——Pack 的成员准入由调度员发码控制，不做自助注册。
- 地址自动发现（pack-addr 独立只读令牌 + 脚本自拉刷新，见 v4 修订 6）——已论证可行但不进第一版；先手动改地址跑通，试跑一周后按隧道轮换频率决定是否补。
- Gemini / DSH 等其他家的专属说明页——先通用版顶着，Codex 跑通后另单补。
- 跨机器公网入口（固定地址/付费隧道）——等群主决定花不花钱。
- App 界面改造（件六的两行微修除外）——外部成员在现有成员列表/房间里能正常显示即可（类型徽章若现成没有，先不加，另单再说）。
- 多房间白名单——第一步只做单房间绑定（bound_room_id）；日后要扩多房间再迁 member_rooms 关联表，字段设计已留扩展口。
- 僵尸成员主动告警——第一版不做推送，只留 member_list 心跳可查 + 巡检宽限升级（件二）兜底。
- 不碰 E2 / 无头引擎 / 登录容器 / Muse 成员任何既有链路。

## 施工切分（两段，各自 staging 验收）
- **Phase A（中枢底座）**：members 四列迁移（kind / display_name / bound_room_id / joined_msg_id）+ messages.reply_to_id + member_read_cursors 建表；pair_code 三工具 + member_token_rotate + external_gate + POST /pair 兑换；member_rename；件二全部服务端隔离（含水位硬截断）、能力拦截、**工具面白名单闸门**、external 独立附件配额与各项频控；限流计数底座；watcher 两道防线（跨房/引擎标记 @ 拒因回帖）。验收 = 全量回归（默认 + --full）全绿 + 下列第 5 条越权反测（含老接口面与白名单面）全拒。
- **Phase B（接入面）**：件三收件箱工具 + 件四脚本 + 件五文档 + 件六 App 微修；验收 = 下列第 1–4、6–8 条全链 + 生产 Codex 真接入。Phase A 未打穿前不开 Phase B。

## 验收（staging 隔离中枢先行，全过才上生产）
1. 配对全链：生成码（绑预定身份）→ 脚本 pair 换 token → 中枢成员表出现 external 成员（kind / 绑定房间 / 自动头像 / joined_msg_id 齐全）、token 哈希落库、明文不出现在任何日志/回读/报错里。
2. 收发全链：调度员在 staging 房间发普通消息 + @ 外部成员（内部短名）各一条 → 外部成员 check-in 拉到且 @ 标记、作者身份标签正确、标已读后再拉不重复、用 since_id 能回看已读 → send 回复进房、reply-to 关系正确；防盲回生效（游标后有新消息时 send 被拒并附回新消息，ignore_new 强发成功）；同 op_key 重发只落一条。
3. 任务全链：派一张文本任务卡给外部成员 → 认领（租约生效）→ 交活 → 账目与现有成员同口径；异常路径同口径：外部成员 task_block、租约过期被 sweeper 回收后，回池/退避/attempts 与既有成员一致；外部成员发的 approval_request 能正常进批准流。
4. 限流与吊销：超频发消息被硬拦并计数（审计可查），approval_request 超独立硬顶被拒、未超不被普通限流误伤；调度员移除成员后旧 token 立即失效（再拉收件箱应被拒）；member_token_rotate 后旧 token 失效、新 token 可用；reclaim 码轮换同成员 token 成功，普通码撞在册 external 名被拒；external_gate 关闭后 external 全部接口被拒、pair 通道关闭，Muse 成员一切正常，重新打开后 external 恢复。
5. 安全复检（慢工一轮，Pack 既有纪律）：①伪造配对码 ②越权读他人/他房收件箱 ③拿 A 成员 token 发 B 成员消息 ④移除后 token 复活——四类原口径全拒；**老接口反测**：external token 直调 room_read_new / attachment_get 跨房偷读、task_claim 抢未指派卡与引擎标记卡、desktop_acquire 抢桌面锁、构造转交把 external 拉出绑定房，必须全拒；**水位反测**：external 传 since_id=0 拉 room_read_new / inbox_pull 只能拿到入群后消息，attachment_get 读入群前附件被拒；**白名单反测**：external 直调 memory_read / skill_read / task_create / task_transfer / desktop_status / room_create 等名单外工具全拒且审计留痕；member_list 对 external 只回脱敏字段且 task_list 不回未指派任务；**配额反测**：external 第 11 次日上传、第 51MB 附件、超 5MB 单文件均被拒，全房配额不受影响；task_update note 超 4000 字被拒、连发 note 触发限流；egress_check 超 60 次/分钟被频控。
6. 配对码边界：过期码（24h 后）兑换被拒；已吊销码兑换被拒；同一码第二次兑换被拒（一码一成员）；连续兑换失败触发按码锁定与全局防扫；所有失败报错不回显明文码/token。
7. 配置隔离：脚本用 --profile 分别 pair 生产口径与单机版（127.0.0.1:8794），两份 config 互不覆盖、各自可用；pair 防串号生效（已有他人 config 时拒绝覆盖）。
8. 生产验收：部署后由 Mac 上 Codex 按 `install-codex.md` 真接入生产群（两层轮询建好），进群打招呼，调度员 @ 它完成一次真实问答（不经 watcher 开卡）；单机版中枢用同一脚本（独立 profile）pair 成功留证即可（不要求全链重跑）。

## 纪律
- 精细活规矩不变：每件常规测试 + 上线前一轮慢工复检；staging 隔离中枢先行，不碰生产数据。
- 零花费不变；新依赖零（脚本无依赖，中枢不加新库）。
- 改中枢前后跑全量回归（默认 + --full），结果读完再报，不许只报「跑完了」。
- 施工结果写 `EXTERNAL-AGENT-RESULT.md` 入仓回执，逐件对本单验收条目给证据。
