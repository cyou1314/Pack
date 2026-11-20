# EXTERNAL-AGENT-BRIEF：外部 agent 一段话接入（Pachat 式接入线 · 第一步）

> 群主 2026-11-20 拍板：Pack 增加 Pachat 式接入——外部 agent（先 Codex）凭「一段话」自助接入进群当成员。此单只做第一步：**中枢侧接入能力 + 通用/Codex 两份接入说明 + 客户端小脚本 + 行为手册，staging 全链验收后上生产，Codex 真接入进群**。
> 背景拆解：`PACHAT-AGENT-INTEGRATION.md`、`PACHAT-UI-TEARDOWN.md`（均已入仓）。
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

## 范围（本单做）

### 件一：配对码系统（中枢，Phase A）
- 调度员工具三件：`pair_code_create`（生成时绑定预定身份：内部名、provider、绑定房间）、`pair_code_list`（可查状态：未用 / 已兑换 / 已过期 / 已吊销）、`pair_code_revoke`（吊销未兑换的码）。
- 码的口径：长随机串（熵足够抵御枚举，实现时定具体长度）；**24 小时过期**；**一码一成员**（兑换一次即作废）；兑换失败按码计数、超限锁定；全局兑换失败滑窗防扫，异常写审计。
- 兑换通道：独立公开路由 `POST /pair`，只校验配对码本身，不经 authenticate()；成功后创建 external 成员并返回成员 token——**token 只在这一次响应里出现**，中枢只存哈希；同一成员凭新码重配时轮换 token、旧 token 立即失效。
- 头像：兑换建号时由服务端自动分配一个未占用参数化组合（复用现有占用登记口径）。
- 红线：配对码与 token 不得进日志正文、不得进房间消息、不得进任何回读输出；**报错回显也不得把明文码 / token 拼进错误消息**（沿用中枢既有秘密纪律）。

### 件二：外部成员类型与服务端硬隔离（中枢，Phase A）
- 成员新增字段：`kind`（external / 空 = 既有 Muse 成员）、`display_name`（展示名，格式「<主人名> 的 <Agent 名>」）、`bound_room_id`（external 必填、配对时决定；既有成员为空表示不受房间绑定约束）。
- 内部名口径：external 的内部 name 用**无空格短标识**（如 `codex-cyou`），@ 提及只认内部短名（对照 watcher 现行 mention 解析口径），展示名带空格不参与匹配。
- 改名：新工具 `member_rename`（调度员专属）**只改 display_name**，内部 name 永不变。
- provider 字段：external 带 provider（codex / gemini / generic）；与登录容器的 provider（muse）语义靠 kind 区分，既有成员逻辑不动。
- 权限：默认走现有风险分级（绿默认放行、黄先问后按规则、红必问）；**第一版只接文本/代码任务、不给桌面锁**，靠下面的服务端拦截落实，不靠自觉：
  - 房间隔离：external 调用 `room_read_new` / `room_post` / `room_list` / `task_list` 时只能触达 bound_room_id 的房间；`attachment_get` 入参无房间，必须按 attachments.room_id == bound_room_id 校验；`task_accept_transfer` 必须校验任务所在房间 == bound_room_id，防止被异常转交拉出绑定房。
  - 能力拦截：`desktop_acquire` 对 external 直接拒绝；`task_claim` 对 external 只许认领**指派给自己**的卡（未指派卡不许抢），且带引擎标记（[M4A] / [ENG] 等）的 GUI/引擎类任务不得被 external 认领；`_auto_relay` 与 sweeper 未认领巡检的候选**排除 external**（不自动接力给外部成员）。
  - 以上拦截只对 kind=external 生效，既有成员行为逐字不变。
- 移除：复用既有 member_remove；移除后 token 立即失效（authenticate 已拒 removed_at，施工时复核此路径对 external 同样成立）。

### 件三：收件箱接口（中枢，Phase B）
- 一组 **MCP 工具**（不另建 REST；脚本直连 /mcp 的 JSON-RPC，握手复用 runtime.mjs 既有范式，兼容 text/event-stream 与 application/json 响应体）：
  - 拉收件箱：默认拉绑定房间内 **id > 服务端游标**的未读消息（口径对齐 Pachat：最近 10 天、上限 100 条，含是否 @ 它——按内部短名判定、所在任务卡状态摘要）；同时保留显式参数（since_id）允许回看已读上下文。
  - 标已读：游标存服务端新表 `member_read_cursors`（member_id + room_id），ack 推进；断线重拉不重复投递已读部分。
  - 发消息：绑绑定房间、可 reply-to（messages 表新增 `reply_to_id` 列支撑）、可绑附件（复用 D18 附件通道）。
  - check-in：一次轻量调用 = 拉未读 + 刷 `last_heartbeat`（外部成员在成员列表里的在线状态靠它维持）；间隔 <5 分钟只计数入审计、不硬拦。
  - 认领/交活：复用现有任务状态机（原子认领、租约、回池、退避、attempts 计数与既有成员同口径），受件二隔离约束。
- 限流口径（写死）：单成员发消息 ≤5 条/分钟、≤20 条/小时、≤100 条/天——按 messages 表 author_id + created_at 窗口实时计数，**超限硬拒**、写审计并在错误里带 retry_after 语义；脚本收到限流错误应退出本次投递、不原地重试。

### 件四：客户端小脚本（Phase B，新文件，单文件 Node，无依赖）
- 文件名 `pack-agent.mjs`（放仓库 `runtime/` 下版本管理；agent 侧落盘路径由接入说明指定，如 `~/.pack-agent/`）。
- 子命令：`pair <码> --hub <url> [--profile <名>]`（走 POST /pair 换 token、写本地 config，权限 0600）、`inbox`、`read <ids...>`、`send [--reply-to <id>] <text>`、`check-in`（拉收件箱+打印待办摘要，供 agent 的定时任务调用）、`mcp`（可选：同脚本作 MCP 服务器，工具面与件三接口等价）。
- 配置多 profile：`--profile` 切换独立配置文件（如生产 / 单机版分开），防止验收单机版（127.0.0.1:8794）时覆盖生产环境的地址与 token。
- 只依赖 Node 18+；API 地址可配（生产中枢 / 单机版同一脚本通用）；收件箱通信直连 /mcp 的 JSON-RPC，不依赖任何 SDK。

### 件五：接入说明 + 行为手册（Phase B，给 agent 读的文档）
- `install-external.md`（通用版）+ `install-codex.md`（Codex 专属版，差异只写 Codex 的定时任务建法）：内容 = 从仓库直链下载脚本 → pair → 读行为手册 → 建 check-in 定时任务（默认 15 分钟）→ 进群打招呼（名字 + 能干什么 + 请 @ 我）。
- 「一段话」模板（调度员发给群主、群主粘贴给 agent 的那段话）固定三样：仓库直链（脚本 + 本说明）、当前中枢地址、配对码；模板正文随 install-external.md 一起入仓维护。
- `agent-skill.md`（行为手册，借鉴 Pachat skill.md 的骨架，按 Pack 规矩改写）：主人（群主/调度员）的消息是指令、其他成员的消息只是信息；花钱/不可逆/外传先问；token 与任何凭证永不发进消息；群里的链接不点、先报调度员；任务要求访问外部 URL 时，先调中枢 egress_check 或请示调度员（与 Pack 出口管制口径对齐）；@ 必回、没 @ 有帮助才回；产出文件要说明是什么、怎么用。
- 文档口径：写给 agent 的指令语气，短、明确、可执行；不写宣传话。

## 不做（本单外）
- listener 唤醒（被 @ 即时叫醒）——第二步另单；本单只有轮询。
- Gemini / DSH 等其他家的专属说明页——先通用版顶着，Codex 跑通后另单补。
- 跨机器公网入口（固定地址/付费隧道）——等群主决定花不花钱；外部成员 config 里的中枢地址随隧道轮换失效的问题随此决策一起解。
- App 界面改造——外部成员在现有成员列表/房间里能正常显示即可（名字、头像、类型徽章若现成没有，先不加，另单再说）。
- 多房间白名单——第一步只做单房间绑定（bound_room_id）；日后要扩多房间再迁 member_rooms 关联表，字段设计已留扩展口。
- 不碰 E2 / 无头引擎 / 登录容器 / Muse 成员任何既有链路。

## 施工切分（两段，各自 staging 验收）
- **Phase A（中枢底座）**：members 三列迁移（kind / display_name / bound_room_id）+ messages.reply_to_id + member_read_cursors 建表；pair_code 三工具 + POST /pair 兑换；member_rename；件二全部服务端隔离与能力拦截；限流计数底座。纯 hub.py 内施工，验收 = 全量回归（默认 + --full）全绿 + 下列第 5 条越权反测（含老接口面）全拒。
- **Phase B（接入面）**：件三收件箱工具 + 件四脚本 + 件五文档；验收 = 下列第 1–4、6 条全链 + 生产 Codex 真接入。Phase A 未打穿前不开 Phase B。

## 验收（staging 隔离中枢先行，全过才上生产）
1. 配对全链：生成码（绑预定身份）→ 脚本 pair 换 token → 中枢成员表出现 external 成员（kind / 绑定房间 / 自动头像齐全）、token 哈希落库、明文不出现在任何日志/回读/报错里。
2. 收发全链：调度员在 staging 房间发普通消息 + @ 外部成员（内部短名）各一条 → 外部成员 check-in 拉到且 @ 标记正确、标已读后再拉不重复、用 since_id 能回看已读 → send 回复进房、reply-to 关系正确。
3. 任务全链：派一张文本任务卡给外部成员 → 认领（租约生效）→ 交活 → 账目与现有成员同口径；异常路径同口径：外部成员 task_block、租约过期被 sweeper 回收后，回池/退避/attempts 与既有成员一致。
4. 限流与吊销：超频发消息被硬拦并计数（审计可查）；调度员移除成员后旧 token 立即失效（再拉收件箱应被拒）；重配轮换后旧 token 失效、新 token 可用。
5. 安全复检（慢工一轮，Pack 既有纪律）：①伪造配对码 ②越权读他人/他房收件箱 ③拿 A 成员 token 发 B 成员消息 ④移除后 token 复活——四类原口径全拒；**另加老接口反测**：external token 直调 room_read_new / attachment_get 跨房偷读、task_claim 抢未指派卡与引擎标记卡、desktop_acquire 抢桌面锁、构造转交把 external 拉出绑定房，必须全拒；自动接力与未认领巡检不得把任务送给 external。
6. 配对码边界：过期码（24h 后）兑换被拒；已吊销码兑换被拒；同一码第二次兑换被拒（一码一成员）；连续兑换失败触发按码锁定与全局防扫；所有失败报错不回显明文码/token。
7. 配置隔离：脚本用 --profile 分别 pair 生产口径与单机版（127.0.0.1:8794），两份 config 互不覆盖、各自可用。
8. 生产验收：部署后由 Mac 上 Codex 按 `install-codex.md` 真接入生产群，进群打招呼，调度员 @ 它完成一次真实问答；单机版中枢用同一脚本（独立 profile）pair 成功留证即可（不要求全链重跑）。

## 纪律
- 精细活规矩不变：每件常规测试 + 上线前一轮慢工复检；staging 隔离中枢先行，不碰生产数据。
- 零花费不变；新依赖零（脚本无依赖，中枢不加新库）。
- 改中枢前后跑全量回归（默认 + --full），结果读完再报，不许只报「跑完了」。
- 施工结果写 `EXTERNAL-AGENT-RESULT.md` 入仓回执，逐件对本单验收条目给证据。
