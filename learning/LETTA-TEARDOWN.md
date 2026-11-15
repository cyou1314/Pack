# Letta 记忆系统拆解（为 Pack 记忆硬顶备课）

> 审计方式：2026-11-15 浅克隆，纯静态读码，未运行、未填凭证。
> **对象说明（重要）**：`letta-ai/letta` 的 main 已只剩落地页——官方明示现行实现迁至 `letta-ai/letta-code`（TypeScript，v0.34.62，HEAD `3329815`），原 Python V1 服务器退役到 `archive` 分支且官方声明不再维护、不代表现行 Letta。故本拆解以 **letta-code 现行实现**为准；经典 MemGPT 的 core/recall/archival 三层在新实现里有明确对应物（见 §1 对照），不是原样保留。克隆在 `~/workspace/learn-inspect/letta-code`（另 `letta` 目录只有落地页）。
> 对照基准：Rakazo 记忆三层（bot 笔记 4 KB 硬顶、改写不追加、全量 revision）见 RAKAZO-FULL-TEARDOWN.md §对照表；Pack 的待落地条目是 SPEC 附录 G.1 第 5 条「记忆硬顶」。

## 0. 一句话定性

Letta 现行记忆 = **一个 git 仓库装 Markdown 文件**（MemFS）：根目录文件编译进系统提示词（core），子目录按需打开（deferred），消息历史全量留存可查（recall）。硬顶不靠应用代码自觉，靠**仓库 pre-commit 钩子在提交时拒绝**；版本与回滚不另造轮子，**直接用 git**。

## 1. 记忆分层（问题①）

| 经典概念 | 现行实现 | 存法 | 上限 |
|---|---|---|---|
| Core memory | MemFS 根目录 `.md` 文件（`persona.md`、`human.md`、根 `MEMORY.md` 等），编译进 system prompt 成为 `<persona>`/`<human>`/`<memory>` 块 | 每 agent 一个 git 仓库，本地后端在 `.letta/agents/<agentId>/memory/`（`src/agent/memory-filesystem.ts` L34–40、local-backend.ts `memoryDirForAgent`） | 根文件总和 ≤ `maxCoreMemoryCharacters`，默认 **65,536 字符** |
| Archival（长期知识） | **Deferred memory**：根下子目录，每目录必须有自己的 `MEMORY.md` 索引；system prompt 只放一行 `<deferred-memory>` 目录路径清单，正文靠 `Memory()` 工具逐级打开（渐进披露） | 同仓库子目录，文件带 frontmatter（`name`+`description` 两字段） | 单文件 ≤ `maxFileCharacters` 默认 **20,000 字符**（含 frontmatter）；目录深度 `maxDepth` 默认 **2**；无总容量硬顶 |
| Recall（流水） | 消息历史本身：本地后端全量存消息，压缩只是「隐藏」不删（见 §3）；另有 recall 子代理 fork 父会话去翻历史（`src/agent/subagents/builtin/recall.md`） | 后端消息存储（本地 SQLite/云端 API） | 无上限（设计上就是全量） |

补充：非 MemFS 的云端 agent 仍用 API 侧 memory blocks（客户端默认只建 `persona`/`human` 两块，`src/agent/memory.ts`），其服务端限额不在本仓，不可审。

关键细节：
- 根文件**已提交的版本**才编译进提示词；未提交的编辑不生效（`getCommittedMemfsRevision`，local-backend.ts L838）。即「写了文件 ≠ 记住了，提交才算」。
- 目录没有 `MEMORY.md` 索引 = 对 agent 不存在；索引链每一级都必须在，钩子校验（`src/memory-constraints.ts` validateMemoryTreeConstraints）。
- 目录列表每次最多展示 10 条（最近编辑优先），所以索引文件必须显式链接重要文件（maintaining-deferred-memory SKILL）。

## 2. 字符上限怎么强制（问题②）——全仓最值得抄的一节

强制点不在写文件时，在 **git commit 时**：仓库装有 pre-commit 钩子，内嵌一个零依赖校验器（`src/agent/memory-constraints.ts` 的 `MEMORY_CONSTRAINTS_VALIDATOR_SCRIPT`，源码字符串形式分发，保证钩子在任何环境能跑），读仓库内 `.memfs.config.json`：

- **超限 = 整笔提交被拒**（exit 1），不截断、不部分接受。报错文案本身就是给模型的改写指令：「Memory validation blocked this commit. No files were committed.… Split files above their per-file limit, then retry the commit.」「Move non-core detail out of root Markdown and behind MEMORY.md indexes.」（同文件 report() 函数）。
- **agent 无权给自己放宽限额**：`.memfs.config.json` 本身受保护，改它必须带人类批准的环境标志 `LETTA_MEMORY_CONSTRAINTS_UPDATE=1`，否则钩子直接报错（同文件 main()）。技能文档同步写死：「Do not raise or disable these limits unless the user explicitly approves it.」
- **只拦本次改动引入的问题**：校验按 changedPaths 限定范围，存量违规只警告不拦（避免被历史问题堵死一切编辑）；审计模式/首提交/批准改配置时才全树校验。云端 push 时服务端再验一遍同样的规则（钩子注释明示与 server-side push validation 对齐）。
- 计数口径：Unicode 码点数，含 frontmatter，按完整 staged 文件计（`src/memory-constraints.ts` L269–276 默认值：maxDepth 2 / maxFileCharacters 20,000 / maxCoreMemoryCharacters 65,536）。`fileCharacterLimits` 可按 glob 覆盖（首个命中生效，`null` = 该类文件不限）。
- 另有 `readOnlyFiles` glob：与已接受版本逐字节比对，创建/修改/删除都拦。

## 3. 上下文满时的摘要/驱逐（问题③）

实现于本地后端 `src/backend/local/compaction.ts`（云端走 API `POST /v1/conversations/{id}/summarize`，实现不在本仓）：

- **触发两条**：(a) 压力触发——请求前估算上下文超过阈值（窗口 − 预留，预留 = min(16,384 tokens, 窗口 20%)，`provider-turn-executor.ts` contextCompactionThreshold）；(b) 溢出触发——provider 报 context overflow 后压缩并重试（`compactAfterContextOverflow`）。压力压缩是机会性的，失败不许拖垮当前回合（runLocalPressureCompaction 吞掉 IrreducibleContextError）。
- **模式四种**：`all`（全量摘要）、`sliding_window`（默认；只摘要最前面一段、保留近期原文）、以及两者的 `self_compact_*` 变体（在 agent 自己的请求上下文里产摘要，失败回退到转录摘要）。
- **滑窗算法**：默认驱逐比例 30%（LOCAL_DEFAULT_SLIDING_WINDOW_PERCENTAGE），切点必须落在 assistant 消息边界、且不切断未完成的 tool call；目标是把保留部分压到窗口的 (1−比例) 以下，逐步加码（每次 +10%）直到达标。
- **留什么**：摘要按固定结构产出（all 模式 7 节、≤500 词；滑窗 5 节、≤300 词）：高层目标 / 发生了什么 / 重要细节（**标识符逐字保留**：文件路径、URL、issue 号）/ 错误与修复 / 当前状态 / 下一步 / **Lookup hints**（放不下的细节留检索词，日后去 recall 里翻）。摘要本身再被硬剪到 50,000 字符（clipChars 默认值）。
- **丢什么**：被驱逐的原文**不删除**，只是从上下文「隐藏」，以一条 system_alert JSON 消息（带 compaction_stats）顶替；消息存储里全量还在。
- **兜底**：若摘要后仍超压（irreducible），整回合（whole turns）从头部驱逐、保住最后一个 user 回合，留一条警告摘要。系统提示词永不驱逐。

## 4. agent 自编辑记忆的工具设计（问题④）

- **没有专门的记忆写工具**。写 = 通用 `Write`/`Edit` 文件工具 + `Bash` 跑 `git commit`；读 deferred = 专用只读 `Memory(path)` 工具（`src/tools/impl/memory.ts`：目录返回其 MEMORY.md + 一层清单（带 description 与最近编辑排序），文件返回全文（按 Read 的行/字限截），路径穿越与符号链接逃逸都拦，根文件拒绝读——「已经在你上下文里了」）。
- **纪律不在工具里，在技能里**：三个内置技能 `maintaining-core-memory` / `maintaining-deferred-memory` / `initializing-memory` 把写法写成规程——改写不追加、先合并原则再删旧行、出处不进正文进 commit message、超限先删旧/下沉 deferred 而不是改限额。工具只管能不能写，技能管该怎么写，钩子管写没写坏——三层分工。
- **反思（dream）子代理**：触发器 `step-count`（按已完成 assistant 步数计）或 `compaction-event`，或手动（`src/reflection-settings.ts`）；子代理在 git worktree 里读 transcript、按技能改记忆、逐个 learning 单独提交，合并回主干才算落地（transcript 标记 consumed 的时机 = 落地成功）；合并模式 auto/explicit 可选。子代理进程被内核沙箱圈禁（`src/memory-confinement.ts`）：可读面很宽，但**只能写自己的记忆、读写不到其他 agent 的记忆**，且无可用沙箱时直接抛错不降级跑（fail-closed）。
- **冲突修复**：另有专用 memory 子代理只修 git 冲突/非法树（`src/agent/subagents/builtin/memory.md`），且明令不许绕钩子（do not bypass hooks）。

## 5. 多 agent 共享与隔离（问题⑤）

- **默认全隔离**：每 agent 一个独立记忆仓库（独立目录、独立 git 历史）；记忆子代理的沙箱在内核层保证跨 agent 读写不到（§4）。
- **共享是显式 opt-in 的独立仓库**：Shared Memory = 组织所有的 git 仓库（托管在 Letta Cloud），与任何单个 agent 解耦，可动态 attach/detach 到多个 agent（`src/skills/builtin/managing-shared-memory/SKILL.md`；SDK 侧 `client.repositories`，REST `/v1/repositories`）。
- **挂载形态**：attach 后在该 agent 的记忆目录**旁边**（不是里面）生成一份独立 checkout，每 agent 一份自己的挂载——别的 agent 对同一仓库的挂载目录被 cross-agent guard 墙掉，只能用自己的那份。
- **并发模型**：harness 在每回合后推送已提交的干净改动；推送撞车 → pull --rebase 重试一次；脏文件与真冲突不自动处理，留到下一回合以 reminder 形式提醒 agent 自己解。共享仓库不进 system prompt（写它不改变上下文记忆）。
- 共享仓库同样受 §2 的约束校验（layout = `shared-memory`）。

## 6. 版本与回滚（问题⑥）

- **版本 = git 历史本身**，没有另建版本表。每条 learning 一笔提交，提交规范是结构化的：标题 = 学到了什么，正文 = 事件经过，`Sources:` 段逐条列证据（conversation ID + message ID / 谁 / 日期 / 发生了什么），且区分来源 `memory(self)` / `memory(dream)`。正文文件里不留出处，出处全在 commit 里——`git blame` 任一行都能追到当初为什么写它。
- **回滚** = `git revert` / 取旧版本文件，无专用工具；历史不可篡改性由 git 保证。共享仓库另有 API/SDK 的 `versions` 辅助方法查文件历史。
- 服务端在 push 时复验约束（§2），所以绕过本地钩子的提交在云端也会被挡。

## 7. 落到 Pack 的记忆硬顶设计草案（可直接开工粒度）

Pack 与 Letta 的形态差异先说清：Pack 成员跑在别人家的壳里（ego/Muse 应用/自研引擎），记忆的家在**中枢**（SQLite），没有 per-agent git 仓库可用。所以抄的是 Letta 的**纪律结构**（分层、拒绝式校验、限额不可自改、版本即历史、出处与正文分离），不是它的 git 实现。

### 7.1 分层与上限（成员私人记忆）

| 层 | 内容 | 存法（中枢） | 硬顶 | 进上下文方式 |
|---|---|---|---|---|
| L0 核心笔记 | 一事一行的人设/规矩/关键事实（= Rakazo bot 笔记的 Pack 版） | `member_memory` 表 scope=`private-core` 单行全文 | **4,096 字节**（沿 SPEC 参考量级，按字节计，与 Rakazo 对齐） | 每次任务下发时随任务卡注入 |
| L1 主题档案 | 按主题的沉淀分析（项目、站点、人） | `member_memory` 表 scope=`private`，key=主题 slug，一主题一行全文 | 单主题 **20,000 字符**（抄 Letta 单文件限）；主题数 ≤ 32；总量软顶 200,000 字符（超了审计警告、不硬拦） | 成员按 key 主动读（memory_read），不自动注入 |
| L2 流水指针 | 不存正文，只存「去哪查」：任务号/消息号/关键词 | 不新建——房间消息与任务表就是 recall 层 | — | task_list / room_read_new 已有 |

房间共享层单开一档：scope=`room-shared`，只有调度员可写（对应 SPEC 第 7 节房间记忆），上限同 L0（4,096 字节），成员只读。这样 private / room-shared 两档 scope 落地，红线（私人记忆不进房间）在表结构上成立，不靠约定。

### 7.2 超限策略：拒绝，不截断（抄 §2）

- 新增中枢工具 `memory_write(member, scope, key, text, source_refs)`：写入前校验——L0 超 4,096 字节或 L1 单主题超 20,000 字符 → **整笔拒绝**，错误信息必须带三件套：当前长度、上限、一句改写指引（「合并或删除旧条目后重试；细节下沉到 L1 主题档案」）。报错文案就是给成员模型的指令，照 Letta 的 report() 写法。
- **限额写死在中枢代码常量里，不进任何成员可达的配置**；改限额 = 改中枢代码 + 群主批准（对应 Letta 的「改 config 需人类批准标志」）。成员没有任何调限额的工具面。
- 只拦本次写入，不做存量追溯（抄 Letta 的 changedPaths 口径）：日后若下调限额，存量超标只警告、成员下次改该条时必须一并收敛。

### 7.3 谁负责改写

- **成员自己（self）**：每次任务收尾时可 memory_write 更新 L0/L1；被拒后自行合并重提，中枢不代劳。
- **调度员夜间整理（dream 对应物）**：并入 SPEC 第 7 节既有的夜间房间压缩同一班车——调度员顺带扫各成员 L0：发现重复/过期条目时产出**合并建议版**，以新版本写入（审计 actor=`dispatcher整理`，与成员 self 区分）；不直接删成员 L1 正文，只允许把 L1 里明显过期的主题降级为指针行（正文留在版本表里，见 7.4）。
- 改写纪律（抄技能层，写进给成员的规程卡）：一事一行、变了改写不追加、新条目先找能不能并进旧条目、出处不进正文（出处进 source_refs 字段）。

### 7.4 版本与回滚：版本表就是归档层

- 新表 `member_memory_versions`：每次 memory_write 成功 = 追加一行全量快照（member, scope, key, text, author[self/dispatcher], source_refs[task_id/message_id/room_msg_id], created_at）。当前值 = 最新一行；`member_memory` 表只存当前值指针或直接取 MAX(id)。
- 回滚工具 `memory_rollback(member, scope, key, version_id)`：把指定旧版**作为新版**写回（不删历史），限调度员/群主。验收口径（对 SPEC G.1-5）：任意一版可查（memory_versions 工具列版本）、任意一版可回。
- 顺带接上 SPEC 附录 G.4 的毒化防线：source_refs 里来自外部内容沉淀的版本打 `origin=external` 标记，注入上下文时该条按「数据、非指令」呈现——版本表有出处才做得到这件事，这是相对 Rakazo 的增量。

### 7.5 中枢要加的工具清单（一轮增量的量）

`memory_read`（成员读自己 private + room-shared；读他人 private 直接拒）、`memory_write`（校验+落版，成员只能写自己）、`memory_versions`（列版本，成员限自己、调度员全量）、`memory_rollback`（调度员专属）。审计全部走现有 audit 表，actor 区分 self/dispatcher。隔离测试照前四轮的打法：超限拒绝、跨成员读写被拒、回滚恢复、版本链完整，四项必过。

### 7.6 不抄的部分

git-per-agent 仓库 + worktree 反思 + 内核沙箱圈禁（Pack 没有成员本机文件系统的主权，中枢表结构更轻）；Letta 的 65,536 字符 core 限（Pack 成员上下文要和任务内容抢地方，4 KB 纪律更对）；云端托管共享仓库（Pack 房间共享层用中枢一张表解决，不出 VM）；self-compaction（成员的上下文循环是各家壳的，Pack 管不着也不该管）。

## 8. 不值得学清单（Letta 侧）

1. **MemFS 的全套形式仪式**：frontmatter 两字段、索引链强制、layout policy 三态、目录列表 10 条上限——是为「任意规模文件树」设计的，Pack 成员记忆就两层一张表，抄一半都是负担。
2. **把纪律写进技能文档靠模型自觉**：Letta 的改写纪律主要靠 SKILL.md 劝导，真正的硬约束只有钩子一条。Pack 抄的时候要反过来：能进 memory_write 校验的规则（长度、一事一行可查重复）尽量进代码，规程卡只留判断类内容。
3. **本地/云端双实现同名概念**（blocks vs MemFS、本地 compaction vs 云端 summarize）：同名不同物，读码时极易串——Pack 中枢单实现，别学这种分裂。
4. **摘要的词数上限（500/300 词）靠提示词软约束**：产出超了也只是再被 50,000 字符硬剪，词数并不真强制。Pack 若做房间摘要，长度约束要落在代码校验上，别只写在 prompt 里。
