# Pack 学习轮归纳合成（SYNTHESIS）

> 产出时间：2026-11-15。材料：第一轮六份拆解（`LETTA / AUTOGEN / CHATDEV / CREWAI / ASTRBOT / LANGGRAPH-TEARDOWN.md`）+ Rakazo 三份（`RAKAZO-TEARDOWN.md`、`RAKAZO-UI-TEARDOWN.md`、`RAKAZO-FULL-TEARDOWN.md`）+ `SPEC-v0.md`（v0.8，附录 G）。
> 附录 G 落地状态（合成基准）：中枢侧四条已上线——批准规则库、独立审查者、执行防重跑、数据出口管制，中枢现 24 工具；未落地 = 记忆硬顶（G.1-5）、呈现纪律（G.1-6）、成员互发回执 chip（G.1-7）、执行体接入 run_start / egress_check。

---

## 1. 跨仓共识

### 1.1 任务状态机：无一家有 Pack 式的，Pack 是独走——且是被反证的差异点

逐家证据：

| 家 | 结论 | 证据 |
|---|---|---|
| AutoGen | 无任务实体 | 全 `agentchat/` grep `lease/claim/assignee` 零命中；`run(task)` 的 task 只是一条初始消息；状态是进程内 dict，进程死群死（AUTOGEN §6） |
| CrewAI | 无认领/租约/状态机 | 全文 grep `claim/lease/heartbeat` 零命中；执行者定义时静态绑定或 manager 进程内直调；状态只活在内存对象，checkpoint 只是事件日志供 replay（CREWAI §6） |
| ChatDev | 无「可认领的工作项」 | 活是写死在 YAML 图里的阶段节点，按边流转，不存在多个执行体竞争同一任务的场景；它的纪律在环计数与阶段合同，不在任务层（CHATDEV §2、§4） |
| LangGraph | 根本没有任务概念 | 它是图执行运行时：状态=通道表、节点=函数；持久化是检查点快照链，服务于「同一条执行线续跑」，不是「一堆活派给多个执行体」（LANGGRAPH §1、§2） |
| AstrBot | 无任务层 | 入站消息过固定管道（唤醒→白名单→限流→插件/LLM），消息即工作，没有任务卡、状态与关单（ASTRBOT §5） |
| Letta | 无任务层 | 单 agent 记忆系统，多 agent 只是各自独立仓库并存，没有协作与派活（LETTA §5） |
| Rakazo | 最接近者也只是直指派单 | Ticket Board 是 Labs 功能：`assigneeBotId` 直指 + 唤醒，无认领、无任务租约、无尝试上限，状态靠 bot 自觉改；它的纪律在 Run 执行层（lease+fence 防同 run 双跑），与任务层防的不是一件事（RAKAZO-TEARDOWN §4） |

**主流的「分活」做法只有四种**：静态绑定（CrewAI sequential、ChatDev 图）、manager 进程内直调（CrewAI hierarchical 委派，无卡无记录）、LLM 主持人选人（AutoGen Selector，只管讨论轮次不管任务）、直指派单（Rakazo Ticket）。没有一家做「服务端原子认领 + 租约心跳 + 尝试上限 + 独立关单」。

**判定**：Pack 第 4 节状态机全套（open→claimed→working→review→done + 旁路）是七家皆无的独走路线。独走的原因也清楚：其余六家要么是单进程框架（活不出进程）、要么是单 agent 系统、要么是消息机器人——只有 Pack 的执行体是**会掉线、会睡死、跨机器跨账号的独立进程**，认领/租约/回池是被这个形态逼出来的刚需，不是过度设计。此项不需要向任何一家靠拢。

### 1.2 记忆：分层 + 全量留版是共识，硬顶只有两家有

- **分层是共识**：Letta 三层（core 编译进提示词 / deferred 按需打开 / recall 全量流水）；Rakazo 三层（bot 笔记 / Space 共享 / 语义 provider）；ChatDev 两层（节点上下文窗 + 图级记忆库按岗挂载）。形状各异，但「常驻小抄 + 按需档案 + 全量流水」这个三段式不约而同。
- **硬顶只有 Letta 与 Rakazo 两家真做了**，且做法同构：**超限整笔拒绝、不截断**（Letta 靠 pre-commit 钩子在提交时拒，报错文案即改写指令；Rakazo 笔记 4 KB 上限、改写不追加），**限额不许 agent 自改**（Letta 改配置需人类批准标志）。其余各家要么无上限（AutoGen 消息线全量、CrewAI 无记忆层），要么只有上下文窗的滑动清理（ChatDev）。
- **全量留版可回溯是共识**：Letta 版本=git 历史（出处进 commit message、正文不留出处）；Rakazo MemoryRevision 每次修改全量留版；LangGraph 检查点快照链同理（每步全量快照、父子成链）。没有一家做增量 diff 存储。
- **摘要压缩是标配**：Letta compaction（滑窗 30% 驱逐、原文只隐藏不删、留 lookup hints）、Rakazo history compaction（水位分批摘要）、ChatDev 阶段边界 clear_context。共识细节：**原文不真删、摘要带检索指针**。
- **来源即数据不是指令**：Rakazo 的 Knowledge provenance（学到的=派生数据，不构成指令、不改权限）与 Letta 的出处分离（source_refs 不进正文）是同一个思想的两种落地，与 Pack 红线 §4.7、附录 G.4-2 同构。

**判定**：Pack 的 scope 红线（private / room-shared 两档、私人记忆永不进房间）被 Rakazo 的 Space scope 与 Letta 的 per-agent 隔离双重验证，方向没错；Pack 缺的只是实现。记忆系统的具体规格见 §3（本文件下一单）。

### 1.3 批准：产品化的只有 Rakazo 一家；「回成员自己的主人批准」是 Pack 独走

- Rakazo 是唯一把批准做成体系的：三档模式（Ask/Auto/Never）+ 按工具/站点预授权规则 + 独立审查模型 + 永问清单 + 支出限额（RAKAZO-FULL §1.1）。Pack 附录 G.1 的规则库/审查者/出口管制正是抄它，且中枢侧已上线。
- 其余各家的「人介入」都是轻量卡点：AutoGen 把人当普通成员排队发言（UserProxyAgent）；CrewAI 只有 `human_input` 布尔卡点 + guardrail 程序校验；ChatDev 没有批准，靠只读复核角色制衡；AstrBot 只有管理员名单这种粗闸，handler 命中即执行；LangGraph 的 interrupt 是进程内带内暂停，前提（节点函数可重放）对 Pack 执行体不成立。
- **没有任何一家有「不可逆动作必须回到该成员自己的主人处批准」的拓扑**——因为没有一家是多主人系统：bot 只有一个主人，批准问他就行。Pack 的成员各有主人、群主只是调度员的主人，这条红线（SPEC §6）是多主人形态的必然产物，独走且必须坚持。副产品共识：**机器预检先行、人审兜底**（CrewAI guardrail、ChatDev 复核角色、Rakazo Auto Review 都是这个分工），Pack 的 review 环可按此加一层预检（见 §2 清单）。

### 1.4 交接：文本拼接是主流，引用传递只有 ChatDev 一家——也是最值得追的一家

- **主流是文本拼接**：CrewAI 把上游输出全文拼进下游 prompt（`_get_context`）；AutoGen 全量广播消息线；Rakazo bot 互发只有消息原语，结果回发起方聊天只读展示。简单，但上下文随链长线性膨胀，且成果物无清单、无校验。
- **ChatDev 独走引用传递**：成果物落共享工作区，交接只传指针，下游自取；平台用前后 sha256 快照自动生成交活清单（attachment 登记），不靠成员自报（CHATDEV §3）。这是七家里唯一把「交活」当一等公民做的。
- **完成信号机器可读是半共识**：ChatDev 用哨兵行 `<INFO> Finished` 做边条件；CrewAI 有结构化输出三态（raw/pydantic/json）供程序消费；Rakazo 的 Run 状态枚举同理。反面是 ChatDev 自己的 final_result 仍是裸字符串——它也没做完，Pack 可以做得比它好。
- Pack 现状（task_complete 一段 result 文本）站在主流那一侧，离 ChatDev 那侧最远。§2 清单第 2 项即为此设。

### 1.5 Pack 路线总判

**被验证的（多家同向，可放心加码）**：
- scope 隔离与私人记忆红线（Rakazo Space、Letta per-agent 仓库、AstrBot UMO 会话隔离——隔离单位不同，思想一致）
- 记忆硬顶 + 拒绝式校验 + 全量留版（Letta + Rakazo 双证）
- 出口管制而非文本识别（Rakazo 成文防线；AstrBot 的管道关卡位置感同向）
- 执行防重跑 fence（Rakazo Run 层；LangGraph「写出级 exactly-once」纪律同向）——已上线
- 失败先分类再重试（LangGraph default_retry_on、ChatDev 三通道可重试判定——两家独立同构）

**独走的（无对照，靠 Pack 自己的验收守）**：
- 任务状态机全套（§1.1）
- 调度员唯一仲裁 + 唯一关单（AutoGen 的主持人只选发言人、不关单；CrewAI manager 既分活又验收、无制衡——Pack 的制衡是独走）
- 带外批准门 + 回成员自己主人批准（§1.3）
- 成员=真登录账号、零花费烧免费额度（七家全无此路径：Rakazo/CrewAI/AutoGen 都是 API key 或订阅 OAuth 借额度，无「成员在软件内登录自己账号」的执行体形态）
- 活群 watcher 的 @ 转卡（AstrBot 的唤醒只决定「理不理」，AutoGen 的点名只决定「谁说话」，把群聊消息转成带状态机任务卡的只有 Pack）

---

## 2. 落地清单（汇总去重后排序）

批次口径：批次 1 = 纯 VM 立即可做；批次 2 = VM 可做、价值次一档；批次 Mac = 需 Mac 在线（执行体接入/App 编译验收）；批次 3/4 = 依赖前项或需群主拍板。

| # | 事项 | 来源 | 出处笔记 | 落点 | 改动量 | 依赖 | 批次 |
|---|---|---|---|---|---|---|---|
| 1 | **记忆系统**：分层（L0 核心 4 KB / L1 主题 20,000 字符 / room-shared）+ 拒绝式校验 + 版本表全量留版 + 来源标记防毒化 | Letta + Rakazo（合并：Letta 分层与拒绝纪律 + Rakazo 硬顶口径与 scope + G.4-2 来源标记） | LETTA §7 草案、RAKAZO-TEARDOWN §2、SPEC G.1-5/G.4-2 | 中枢（新表 + 4 工具） | 中 | 纯 VM 可做 | **1（下一单，规格见 §3）** |
| 2 | **结构化交活信封**：task_complete 扩 verdict 哨兵 + summary + artifacts 按引用 + self_check + known_issues + followups；中枢按 schema 校验不过打回 | ChatDev + CrewAI（合并：ChatDev 引用交活/哨兵/自检 + CrewAI schema 校验） | CHATDEV §7(a)、CREWAI §2/可借 1 | 中枢（task_complete 扩展）+ 执行体契约 | 中 | 中枢侧纯 VM；执行体填充随 Mac 批 | 1（中枢）/ Mac（填充） |
| 3 | **失败分类 + 退避回池**：tasks 加 failure_kind / next_visible_at；网络/5xx 类指数退避后才重新可见，逻辑错误类立即回池或转 blocked | LangGraph + ChatDev（合并：两家同构的「先分类再决定重试」） | LANGGRAPH §4/清单 5、CHATDEV §6 | 中枢（回池逻辑） | 小–中 | 纯 VM 可做 | 1 |
| 4 | **成员岗位说明书模板定稿**：Pack 级共同前言 + 岗位六段（身份/能力/边界/输入来源/产出契约/权限面），工具白名单即边界；任务级禁令写进任务卡不写说明书 | ChatDev | CHATDEV §1、§7(b) | 规格 + 成员规程卡 | 小 | 纯 VM 可做（文档） | 1 |
| 5 | **执行体接入 run_start / egress_check**：认领后先 run_start（被拒跳过）、抓取前 egress_check（ask 走 approval_request）；批准恢复后第一动作先查 run/task 现状（幂等纪律） | Rakazo（G.1-3/4 既定）+ LangGraph 幂等纪律佐证 | SPEC G.1-3/4、LANGGRAPH 清单 4 | 执行体（ego/桥/引擎三处） | 小/处 | 需 Mac 在线 | Mac |
| 6 | **guardrail 机器预检**：review 前加可判定项预检（格式/字数/产物文件存在性/路径合法），不过自动打回附原因，占用既有 3 次尝试计数；复杂任务 review 前先蒸馏摘要 | CrewAI + ChatDev | CREWAI 可借 3、CHATDEV §4-4/§7-2 | 中枢（task_review 前置） | 中 | 纯 VM 可做 | 2 |
| 7 | **任务依赖字段**：task_create 加 depends_on，中枢在上游关单时把其结构化交活（信封版）拼进下游卡正文，替代调度员手动粘贴 | CrewAI | CREWAI §2/可借 2 | 中枢 | 小 | 纯 VM；吃 #2 的信封才完整 | 2 |
| 8 | **讨论调度**：活群讨论模式——三层选人（硬规则 @/相关方 → 候选裁剪 → 调度员判断）、选人留痕、终止条件组合（轮次上限｜关键词｜群主喊停外部信号）、防独白（同一成员不连两轮）、全量可见但点名才回 | AutoGen | AUTOGEN §1、§3、§7 | watcher + 中枢 | 中 | 纯 VM 可做；讨论模式开关需群主知情 | 2 |
| 9 | **上下文钉子 + 阶段清上下文**：任务卡原文对执行体全程钉住；每轮开工只带「卡原文 + 本轮意见 + 成果物引用」，不带整段历史 | ChatDev | CHATDEV §5、§7-4 | 执行体契约 + 任务卡格式 | 小 | 卡格式 VM 可定；执行体遵守随 Mac 批 | 2 / Mac |
| 10 | **runs 中间产物登记**：执行体干活中先把产物/进度登记到 run 名下再报 complete，崩溃不丢已产出部分 | LangGraph | LANGGRAPH §2/清单 2 | 中枢（runs 扩展） | 小–中 | 中枢纯 VM；执行体填充随 Mac 批 | 2 |
| 11 | **呈现纪律**：steps 折叠 + 直播只露最近 6 步 + 步数/耗时摘要；状态色收口专用 token（工作中/未读/在线）三处同色同义 | Rakazo（G.1-6 既定） | RAKAZO-UI §4.3、SPEC G.1-6 | App | 中 | 需 Mac 编译验收 | Mac |
| 12 | **成员互发回执 chip**：主线只留居中 chip（头像+名字+已发给/来自），点开浮层看对端全文 | Rakazo（G.1-7 既定） | RAKAZO-UI §4.2、SPEC G.1-7 | App + 中枢（消息呈现） | 中 | 需 Mac 编译验收 | Mac |
| 13 | **任务快照链**：每次状态迁移存完整任务快照并链 parent，任意一步可查可 fork；fork 算一次尝试 | LangGraph | LANGGRAPH §2/清单 1、8 | 中枢（新表 task_snapshots） | 中 | 纯 VM 可做 | 3 |
| 14 | **steps 进度位续跑**：任务加步骤勾选，超时回池被重领时从未完成步继续而非整单重来 | LangGraph | LANGGRAPH 清单 3 | 中枢 + 执行体 | 中 | 需 Mac 配合执行体 | 3 |
| 15 | **事件命名规范化**：中枢 audit 事件名与载荷对齐 task_started/completed/failed 式标准形状，为 App 任务板实时推送预备 | CrewAI | CREWAI 可借 5 | 中枢（audit） | 小 | 纯 VM；与 App 推送一起验 | 3 |
| 16 | **外部入口适配层**：适配器极小面（run/meta）+ UMO 式三元组会话键（入口:房间:会话）+ 通配路由分流 + 能力旗标元数据 + send_by_session 主动推送出口；信任层（指令权分级/批准/按人隔离）由 Pack 自建，不借 AstrBot 的平信任 | AstrBot | ASTRBOT §1–§3、§7、§8 | 中枢入口层（新） | 大 | **需群主拍板**接哪个平台；微信走现成 iLink 桥做协议消费端 | 4 |
| 17 | **SSRF 防线清单**（触发式）：日后任何「可填自定义端点」处（Relay 接入/成员自备端点）照单执行——保存/发现/运行时三道检查 + DNS 复检 + 元数据地址永拒 + 带 key 必须 HTTPS | Rakazo | RAKAZO-TEARDOWN §5 | 中枢/执行体（触发处） | 小 | 触发时做 | 4 |
| 18 | **执行体驱动抽象收拢**：三执行体按 SandboxProvider 式统一契约收拢，workspace 检查点为真数据、执行环境可替换；范围只限 Mac 侧三执行体，不含云沙箱 | Rakazo（G.2-1 既定） | SPEC G.2-1 | 执行体 | 大 | E2 之后 | 4 |
| 19 | **Space 信封加密**（目标态）：中枢数据要出 VM（备份外发/多机部署）时按每房间密钥 + AES-256-GCM 落地 | Rakazo（G.2-2 既定） | SPEC G.2-2 | 中枢 | 大 | 触发时做 | 4 |

说明：原 G.1 待办中「执行体接入」即 #5、「呈现纪律」即 #11、「回执 chip」即 #12、「记忆硬顶」即 #1，本表已全部收编，不另设项。

---

## 3. 记忆系统增量规格（下一单 · 可直接派活）

以 LETTA-TEARDOWN §7 草案为底，合并 Rakazo 硬顶口径（4 KB、改写不追加、全量留版）与 Pack 红线（SPEC §6 私人记忆不进房间、G.4-2 来源标记防毒化）。以下均为**定死口径**，开发照此执行，不再另行讨论；与既有中枢冲突时以本节为准并回报考点。

### 3.1 分层与上限（定死）

| 层 | scope | key | 内容 | 硬顶 | 计量口径 | 注入方式 |
|---|---|---|---|---|---|---|
| L0 核心笔记 | `private-core` | 固定 `""`（单行全文） | 一事一行的人设/规矩/关键事实 | **4,096 字节** | UTF-8 字节数 | 每次任务下发随任务卡注入 |
| L1 主题档案 | `private` | 主题 slug（见 3.2） | 按主题的沉淀分析 | 单主题 **20,000 字符**；主题数 **≤ 32**；成员总量软顶 200,000 字符（超了只审计警告、不拒绝） | Unicode 码点数（含空白） | 成员按 key 主动 memory_read，不自动注入 |
| 房间共享层 | `room-shared` | 固定 `""`（单行全文） | 房间级公共事实（对应 SPEC §7 房间记忆的成员可读面） | **4,096 字节** | UTF-8 字节数 | 随任务卡注入；**仅调度员可写，成员只读** |
| L2 流水 | —（不建层） | — | 任务与房间消息本身即 recall 层 | — | — | 已有 task_list / room_read_new，不新增 |

红线落法：private 两档与 room-shared 在**表结构与工具鉴权**上分离（见 3.3/3.4），不是靠约定——成员身份永远写不到 room-shared，也读不到他人 private。

### 3.2 标识与身份（定死）

- **调用者身份由中枢从令牌解析**（沿用现有鉴权：令牌只存 sha256、身份查 members 表），工具入参**不接受**调用者自报的 member 身份。
- `memory_write / memory_read / memory_versions` 的目标成员默认 = 调用者本人；**仅调度员**可在入参带 `member` 指定他人（代写整理、代查、回滚前核对）。
- L1 的 key（主题 slug）格式定死：正则 `^[a-z0-9][a-z0-9-]{0,63}$`，不合规返回 `invalid_key`。
- L0 与 room-shared 的 key 恒为 `""`，传非空 key 返回 `invalid_key`。

### 3.3 中枢工具签名（定死；新增 4 个，中枢 24 → 28 工具）

```
memory_read(scope, key="", member=None)
  → {member, scope, key, text, version_id, origin, updated_at}
  权限：成员只能读自己的 private-core / private，以及 room-shared；
        读他人 private* → forbidden（并写审计，见 3.6）；
        调度员任意读（member 指定目标）。
  无内容时 text="" 、version_id=None，不报错。

memory_write(scope, key, text, source_refs=[], origin="self", member=None)
  → 成功 {version_id, size, limit}（size/limit 按该层计量口径回数）
  权限：成员只能写自己的 private-core / private；写 room-shared → forbidden；
        写他人 → forbidden；调度员可代写任意成员（夜间整理）与 room-shared。
  origin 定死规则：枚举仅 {self, external, dispatcher}；
        成员调用只能传 self / external（从外部内容沉淀的必须传 external，契约义务，见 3.7）；
        调度员代写时服务端强制 origin=dispatcher，忽略入参；
        origin=external 的版本在 memory_read 返回与任务卡注入时必须原样透传该标记。
  source_refs：数组，元素 {type: "task"|"message"|"room_msg"|"rollback_of", id: <int>}，只存引用不存正文。

memory_versions(scope, key="", limit=20, before_id=None, include_text=false, member=None)
  → [{version_id, author, origin, source_refs, size, created_at}]（新→旧）
  include_text=true 时每项加 text 全文；该参数仅调度员可用，成员传 true → forbidden。
  权限同 memory_read（成员限自己 + room-shared）。

memory_rollback(scope, key, version_id, member=None)
  → {new_version_id, restored_from: version_id}
  权限：调度员专属，成员调用 → forbidden。
  语义：把目标版本全文作为**新版本**写回（author=dispatcher、origin=dispatcher、
        source_refs=[{type:"rollback_of", id:version_id}]），历史一行不删。
  version_id 不属于该 (member, scope, key) → not_found。
```

### 3.4 超限与错误格式（定死）

拒绝不截断（抄 Letta/Rakazo 共同口径）：超限整笔拒绝、不落版、不部分接受。错误统一 JSON：

```json
{"error": "memory_limit_exceeded", "scope": "private-core",
 "limit_bytes": 4096, "current_bytes": 5231,
 "hint": "合并或删除旧条目后重试；细节下沉到 private 主题档案（memory_write scope=private）"}
```

- L1 单主题超限：同 code，字段换 `limit_chars: 20000, current_chars: N`，hint 指向「拆分主题或删旧段落后重试」。
- 主题数超 32：`{"error": "memory_topic_limit", "limit_topics": 32, "hint": "合并或删除一个旧主题（写入空文本不算删除；删除走 memory_write 同 key 传 text=null 的约定见下）"}`——定死：**删除主题 = memory_write(scope=private, key, text=null)**，落一版空版本（text=""、tombstone 标记在 source_refs 加 `{type:"delete"}`），当前值视为空、主题计数不含空版本主题；L0/room-shared 传 text=null → `invalid_argument`。
- 其余错误 code 定死四种：`forbidden` / `invalid_key` / `invalid_argument` / `not_found`，均带一句 hint。
- **只拦本次写入**：存量若因日后下调限额超标，只在该条下次被写入时要求一并收敛，不追溯拒绝读、不自动删。
- 软顶（L1 总量 200,000 字符）超限：**照常写入**，同时写一条审计 `memory_soft_cap`（见 3.6），不向调用者报错。

### 3.5 表结构（定死）

```sql
CREATE TABLE member_memory (           -- 当前值（一行 = 一个 (成员, scope, key)）
  member     TEXT NOT NULL,            -- members.name
  scope      TEXT NOT NULL,            -- private-core | private | room-shared
  key        TEXT NOT NULL DEFAULT '', -- L1 为主题 slug，其余恒 ''
  text       TEXT NOT NULL,
  version_id INTEGER NOT NULL,         -- → member_memory_versions.id
  updated_at TEXT NOT NULL,
  PRIMARY KEY (member, scope, key)
);
-- room-shared 行的 member 恒为保留名 '__room__'。

CREATE TABLE member_memory_versions (  -- 全量快照，一次成功写入 = 一行，只追加
  id          INTEGER PRIMARY KEY,
  member      TEXT NOT NULL,
  scope       TEXT NOT NULL,
  key         TEXT NOT NULL DEFAULT '',
  text        TEXT NOT NULL,           -- 该版全文（删除主题为空串）
  author      TEXT NOT NULL,           -- 成员名 | 'dispatcher'
  origin      TEXT NOT NULL,           -- self | external | dispatcher
  source_refs TEXT NOT NULL DEFAULT '[]',  -- JSON 数组，见 3.3
  size        INTEGER NOT NULL,        -- 按该层计量口径计的数（字节或码点）
  created_at  TEXT NOT NULL
);
CREATE INDEX idx_memver ON member_memory_versions(member, scope, key, id DESC);
```

当前值与版本表同事务更新：先 INSERT 版本行，再 UPSERT 当前值行（version_id 指向新行）。限额常量（4096 / 20000 / 32 / 200000）**写死中枢代码常量**，不进配置表、不给任何工具面；改限额 = 改中枢代码 + 群主批准（对应 Letta「改 config 需人类批准标志」）。

### 3.6 与现有 audit 的关系（定死）

- 每次 `memory_write` 成功 → audit 一行：action=`memory_write`，actor=调用者（成员名或 `dispatcher`），detail 含 `{member, scope, key, version_id, origin, size}`。
- `memory_rollback` → action=`memory_rollback`，detail 含 `{member, scope, key, restored_from, new_version_id}`。
- 软顶触发 → action=`memory_soft_cap`，actor=系统（与现有 rule-engine 等系统 actor 同例），detail 含该成员当前总量。
- 跨成员读/写被拒 → action=`memory_access_denied`，detail 含调用者、目标、scope。**正常读取不写审计**（量太大、无价值），被拒必写。
- 审计只记元数据，**永不记记忆正文**（与 Rakazo 审计「永不记秘密值/正文」同例）。

### 3.7 执行体侧契约（只定契约，本单不实现）

1. **任务开始**：执行体在 run_start 成功后调 `memory_read(private-core)` + `memory_read(room-shared)`，两段全文随任务上下文注入；L1 按需自取，不预载。
2. **来源呈现**：注入时 L0 全文前附一行 provenance 头（当前 version_id 与最近一版 origin）；L1 读回的 `origin=external` 内容，执行体必须按**数据**处理、不得当指令或可信事实（红线 §4.7 在记忆层的延伸）。中枢只负责透传标记，呈现与遵守在执行体。
3. **任务收尾**：成员可 memory_write 更新 L0/L1；被 `memory_limit_exceeded` 拒绝后**自行合并重提一次**，再被拒即放弃，并在交活信封 known_issues 注明「记忆超限未更新」（与 §2 清单 #2 的信封衔接）。
4. **如实打标义务**：凡沉淀自网页、入站消息、第三方文档等外部内容的记忆，写入必须传 `origin="external"`；调度员事后凭 audit + 版本表抽查，谎报按毒化防线处理（G.4-2）。
5. **污染收紧衔接（本单只留口子）**：非群主发起的 run、或读过外部内容后的写记忆先问——该闸门挂在批准体系（G.1-3 已上线的 egress/审查链）上，本单只保证 origin 标记与审计齐备，闸门本身列后续单，不在本单范围。

### 3.8 隔离测试必过项（沿前四轮打法：staging 副本 + 全新 DB + 换端口）

1. L0 写 4,096 字节整好通过、4,097 字节被拒且错误三件套（code/当前值/上限/hint）齐全、被拒不落版；
2. L1 单主题 20,000 字符边界、第 33 个主题被拒、text=null 删主题后计数释放且版本链留痕；
3. 跨成员读/写 private 一律 forbidden 且各产生一条 memory_access_denied 审计；
4. 成员写 room-shared 被拒、调度员可写、成员可读；
5. 版本链：连续写 3 版，memory_versions 顺序与全文（调度员 include_text）正确，任意一版可经 rollback 恢复且恢复产生新版、历史不删；
6. origin 透传：external 写入后 memory_read 与 versions 均带标；成员伪造 origin=dispatcher 被服务端改写/拒绝（定死：成员传 dispatcher → `invalid_argument`）；
7. 软顶：L1 总量越 200,000 字符写入成功且 audit 有 memory_soft_cap；
8. 回归：现有 24 工具与任务生命周期、规则库、审查、防重跑、出口判定全不破。

---

## 4. 不学清单（六家各一条）

| 家 | 最不值得 Pack 碰的 | 理由 |
|---|---|---|
| Letta | **MemFS 全套形式仪式**（git-per-agent 仓库、frontmatter 两字段、索引链强制、worktree 反思子代理） | 是为任意规模文件树设计的重机械；Pack 记忆就两层一张表，抄纪律（拒绝式校验/限额不可自改）即可，抄形式纯属负担 |
| AutoGen | **Swarm 成员自选下家（handoff 派活）** | 成员互相派活正是 SPEC 明令禁止的，调度员唯一仲裁是 Pack 的地基，这个实现再优雅也是反面教材 |
| ChatDev | **边条件 fail-open**（条件求值抛异常当作条件成立） | 交接合同宁可停摆也不能错放行；Pack 的 watcher/状态机若学这条，一次异常就可能把活派错人 |
| CrewAI | **manager 进程内直调委派**（临时 Task、无卡、无状态、无记录、role 名对上就派） | Pack 的任务卡 + 原子认领 + 全量审计正是为解决它这种「派了什么没人知道」而建，学它等于拆自己的地基 |
| AstrBot | **插件同进程全权限、无沙箱无权限声明**（第三方插件=在主机上跑任意代码，信任全押使用者自审） | 与 Pack 的最小权限、白名单、全量审计纪律系统性冲突；它的适配层可抄，插件生态连同平信任模型必须整体留在门外 |
| LangGraph | **Pregel 超步运行时底盘本身**（通道/reducer 声明式合并、BLOB 快照、带内 interrupt） | 为进程内毫秒级函数节点设计，与 Pack 跨机器分钟级租约的执行体尺度、层级双错位；引进来等于换底盘，只借它重试分类与快照链的思想 |

（Rakazo 的不学项已在 SPEC 附录 G.3 定案：API 接入、深色 UI、直指派、共享云电脑默认形态，不在此重复。）
