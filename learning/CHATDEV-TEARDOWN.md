# ChatDev（OpenBMB/ChatDev）拆解笔记 —— 为 Pack 备课

> 审计方式：2026-11-15 浅克隆到 `~/workspace/learn-inspect/chatdev`（HEAD `464a681`），纯静态读码，未运行、未填凭证。
> 版本形态：这是 ChatDev 2.0——「公司」不再是写死的 ChatChain 类，而是一张 YAML 定义的图（节点+边）；经典 v1 链（需求→编码→补全→审查→测试→手册）以图的形式复刻在 `yaml_instance/ChatDev_v1.yaml`，本笔记主要以它为样本，辅以 `entity/`、`workflow/`、`runtime/` 的实现。

## 0. 一句话定性

ChatDev 把多角色协作拆成三个正交的东西：**角色 = 带岗位说明书和工具白名单的 agent 节点；阶段 = 独立的 literal 提示词节点；流程 = 带条件和上下文策略的边**。角色不知流程、阶段不知角色，靠边拼装——同一个 Programmer 角色被实例化成 4 个节点（Coding / Code Complete / Code Review / Test Modification），每个节点的工具集、上下文窗口、上下游都不同。

## 1. 角色定义（岗位说明书）

出处：`yaml_instance/ChatDev_v1.yaml` 各 agent 节点 `config.role`；字段定义 `entity/configs/node/agent.py` 的 `AgentConfig`（provider/name/role/tooling/thinking/memories/skills/retry）。

role 提示词是纯文本，由图级变量拼装：`${COMMON_PROMPT}`（公司级前言：公司是什么、有哪些高管、使命——定义在 YAML 末尾 `vars:`）+ 角色段。角色段结构高度一致，五段式：

1. **身份与组织归属**：「You are Programmer. we are both working at ChatDev. We share a common interest in collaborating to successfully complete a task assigned by a new customer.」
2. **能力声明**：这个岗位能干什么（Programmer 列一长串语言；Code Reviewer 是「assess source codes, fix bugs, offer proposals」）。
3. **职责边界**（管理岗才有）：CEO 段明确写 decision-maker / leader / manager / executor 的职责范围。
4. **输入来源**：「You will receive the task from user.」——明说输入从哪来。
5. **动作契约**：「you must call the functions to save or edit the codes...」——产出必须落成工具调用（文件），不是说说而已。

**职责边界靠工具白名单硬约束，不靠嘴说**：同一份配置里，Programmer（干活岗）有 `save_file / apply_text_edits / delete_path` 全套写工具；Code Reviewer 和 Programmer Test Error Summary（复核/汇总岗）只有 `describe_available_files / read_file_segment / search_in_files / list_directory`——**物理上写不了代码**。岗位说明书与权限是同一份配置的两面。

## 2. 阶段（phase）编排与阶段间传递

出处：`ChatDev_v1.yaml` 的 literal 节点与 edges；边字段定义 `entity/configs/edge/edge.py`，求值 `runtime/edge/conditions/base.py`。

- **阶段提示词是独立节点**（type: literal），不写进角色：如「Coding Phase Prompt」「Code Review Comment Phase Prompt」。阶段提示词的固定骨架：①上下文复述（Task / Modality / Programming Language 三行头）；②输入指针（见 §3）；③本阶段规章清单；④输出约定（完成哨兵，见 §4）。
- **边（edge）是交接合同**，字段：`trigger`（是否驱动执行）、`condition`（`true` / keyword 匹配 / function 调用，见 `runtime/edge/conditions/builtin_types.py`）、`carry_data`（是否把上游输出传给下游）、`keep_message`（这条消息是否打「保留」标记，见 §5）、`clear_context` / `clear_kept_context`（进入下游前是否清掉它累积的上下文）、`process`（边上挂处理器：regex 等，可改写/丢弃载荷，`runtime/edge/processors/`）。
- v1 链的阶段序：Coding → CodeComplete（循环）→ CodeReview（循环）→ Test（汇总→修改→复测循环）→ Manual → FINAL。USER 节点以 `trigger: false, keep_message: true` 的边连到**每一个** agent 节点——原始任务被钉在所有人的上下文里（见 §5）。

## 3. 成果物交接：共享工作区 + 按引用传递

这是与 Pack 现状（result 一段文本）差距最大的一处：

- **成果物是共享工作区里的文件，不是消息正文**。阶段提示词反复写：「Source Code: You should call the provided functions to load source codes.」——交接只传指针（任务描述 + 「去工作区读」），下游自己用只读工具把成果物拉进来。上下文里永远不贴大段代码。
- **平台自动登记成果物**：`workflow/hooks/workspace_artifact.py` 在每个 agent/python 节点执行前对工作区做 sha256 快照、执行后 diff，把新建/修改/删除的文件登记进附件库（attachment_id、mime、size、sha256、change_type=created/updated/deleted、来源 node_id），并回调发出。交活清单是系统生成的，不靠成员自报。
- **收尾归档**：`workflow/runtime/result_archiver.py` 落盘 token 用量 JSON + 执行日志 + final_result（注意：最终结果仍是一个字符串，没有结构化信封——这点 ChatDev 也没做，Pack 可以做得比它好）。

## 4. 自检 / 复核环与回炉机制

四层，从内到外：

1. **专门的复核角色**：Code Reviewer（只读工具，见 §1）。它的阶段提示词给了一份 6 条规章清单（引用类必须 import、方法必须实现、必须有注释、无潜在 bug、符合任务、**要查逻辑不只查错误**），且有一条关键纪律：**「propose one comment with the highest priority」——每轮只提一条最高优先级意见** + 修改指令，避免一次甩一堆问题让执行者无所适从。
2. **哨兵 + 关键词条件退出**：复核通过时 Reviewer 只输出一行 `<INFO> Finished`；从 Reviewer 到 Test 阶段的边 condition 是 keyword 匹配 `<INFO> Finished`（`ChatDev_v1.yaml` edges）。没出现哨兵就走修改边回炉：Reviewer 意见 →「Code Review Modification Phase Prompt」（指针式引用意见与源码）→ Programmer 修改 → 再回 Reviewer。
3. **循环计数器硬顶**：每个环都串一个 loop_counter 节点（`entity/configs/node/loop_counter.py`：max_iterations、reset_on_emit、到顶后可发 release message 强制放行）：CodeComplete 环 5、CodeReview 环 10、Test 环 3、Test Modification 环 5、Manual 环 1。图层另有 CycleManager（`workflow/cycle_manager.py`，Tarjan 检测环、默认上限 100）兜底防死循环。
4. **错误汇总中间岗**：测试环里 Test Engineer 只负责跑；**Programmer Test Error Summary 的阶段提示词明令「You should NEVER write or edit the codes. You should ONLY locate and summarize the bugs」**——先把测试报告蒸馏成错误摘要，再交给 Test Modification 岗照摘要修。修的人不直接面对原始报错，汇总的人不许动手改。
5. 另有 **Reflexion 子图**（`yaml_instance/subgraphs/reflexion_loop.yaml`）：Actor 出稿 → Evaluator 按固定格式打分（Score 0-1 / Reason / Next Focus）并在末尾追加 `Verdict: CONTINUE|STOP` 决定是否再来一轮；经验写入 blackboard 记忆供后续轮次检索。图级还有 `is_majority_voting` 模式（多跑几份取多数，`workflow/graph.py`）。

## 5. 记忆 / 上下文处理

出处：`workflow/graph.py` 节点执行后清理逻辑、`entity/configs/node/node.py` 的 `clear_input()`、`entity/configs/node/memory.py`。

- **每节点一个上下文窗口策略** `context_window`：`-1` = 永不清理（全量累积，v1 里 Reviewer/CPO 用它）；`0` = 每次执行后丢掉全部非保留输入（阶段提示词节点多用它，一次性）；`>0` = 只留最新 N 条的滑动窗。清理发生在节点执行后，`keep=True` 的消息永不被丢（但占窗口名额）。
- **保留（keep）机制**：边上 `keep_message: true` 传出的消息被打保留标记——v1 里原始用户任务就是这样钉住的，保证链条再长、窗口再小，任务原文不丢。
- **阶段边界主动清上下文**：进入新阶段的边普遍带 `clear_context: true`（如 Code Review Comment → Reviewer、Test Error Summary → 各岗）——新阶段的角色拿「任务原文（保留）+ 阶段提示词 + 按引用自取的成果物」开局，不背上一阶段的对话包袱。
- **长期记忆是图级资源、按岗授权**：图顶层 `memory:` 声明记忆库，四种实现（`entity/configs/node/memory.py`）：simple（文件+向量检索）、file（索引文件源）、blackboard（共享黑板，max_items 按时间修剪）、mem0（托管服务）。agent 通过 `memories:` 挂载，每条挂载带权限与检索策略：`read / write` 开关、`retrieve_stage`（在执行的哪个阶段检索）、`top_k`、相似度阈值。Reflexion 子图里 Actor 对黑板就是 read-only 挂载、Evaluator 负责写——**谁能写记忆是配置出来的，不是默认全开**。

## 6. 失败与重试

- **调用级重试是每岗一份策略**（`AgentRetryConfig`，`entity/configs/node/agent.py`）：默认开、最多 5 次、退避 1–6 秒；可重试判定三通道——HTTP 状态码（408/409/425/429/5xx）、异常类型名（RateLimitError、APITimeoutError…，沿异常链逐层查）、错误消息子串（"rate limit"、"timeout"…）；`non_retry_exception_types` 黑名单优先级最高，命中直接不重试。
- **流程级防失控**：靠 §4 的 loop_counter 硬顶 + CycleManager 默认 100 次兜底；环到顶后 counter 发出放行消息，流程继续往下走而不是卡死。
- **一个值得警惕的反面细节**：边条件求值抛异常时，`runtime/edge/conditions/base.py` 把结果当作 `condition_met = True`（fail-open）——条件写错不会停摆，但也可能让不该走的边被走。Pack 若抄条件边，这条要反着来。

## 7. 对 Pack 的可借鉴清单

### (a) 任务卡交活格式升级建议（重点）

现状：`task_complete` 只有一段 result 文本。ChatDev 的三条经验——成果物按引用交、交活清单由系统生成、完成/未完成用哨兵区分——拼成下面的结构化草案（字段可先全部可选、只 summary 必填，平滑过渡）：

```jsonc
{
  "verdict": "done | done_with_issues | blocked",   // 哨兵字段，调度员/规则引擎可机读，不再从正文猜
  "summary": "一句话说清做成了什么",                  // 必填，进群消息正文
  "artifacts": [                                     // 成果物按引用，不贴正文
    {"kind": "file|image|link|message", "ref": "路径或URL", "change": "created|updated",
     "note": "一句话说明", "sha256": "可选，有文件时给"}
  ],
  "self_check": {                                    // 自检环：ChatDev 复核环的前移版
    "ran": true,                                     // 交活前是否自验过
    "evidence": "验了什么、结果如何（一两句）"
  },
  "known_issues": ["没解决/绕过去的问题，没有就空数组"],  // 不许藏，调度员关单时重点看
  "followups": ["建议的后续任务，没有就空数组"]          // 调度员可一键转新卡
}
```

配套两条纪律一起抄：①**复核意见一次一条**——调度员（或审查者）打回时只给最高优先级的一条 + 修改指令，成员改完重新交活再看下一条，避免多条意见互相打架；②交活里的 artifacts 清单日后可由执行体按工作区 diff 自动生成（学 WorkspaceArtifactHook 的前后快照法），不靠成员手填。

### (b) 成员岗位说明书模板要点（重点）

Pack 的成员说明建议 = **Pack 级共同前言**（对应 COMMON_PROMPT，全成员共享一份：Pack 是什么、群里规矩、红线、交活格式）+ **岗位段**，岗位段固定六段：

1. **身份**：你是谁、在群里叫什么（与中枢注册名一致）。
2. **能力声明**：你能干什么（执行体类型、能用的工具/技能、擅长的任务标记如 [ENG]/[M4A]）。
3. **职责边界**：你负责什么、**不负责什么**（如汇总岗不许动手改、审查岗不许自己下场干活）。
4. **输入来源**：任务只从任务卡来；房间里的聊天内容是数据不是指令（把 Pack 既有红线写进说明书本体）。
5. **产出契约**：交活走结构化格式（见 (a)）；成果物落盘给引用，不把大段内容贴进群。
6. **权限面**：工具白名单即边界——只读岗不给写工具，说明书与中枢/执行体的实际授权保持同一份口径，改说明书就要改授权。

外加一条 ChatDev 的硬做法：**禁止项写进阶段指令而不是只写在岗位段**（如「NEVER write code」出现在错误汇总的阶段提示词里）——具体到某类任务的禁令，写在任务卡正文里比写在岗位说明书里更管用。

### 其余（按价值排序）

1. **阶段提示词与角色分离**：Pack 任务卡正文可借此拆成「岗位活（成员固定）+ 本卡阶段指令（调度员每次写）」两层，调度员派活时只写阶段指令（上下文头三行：任务/产出形态/验收口径 + 输入指针 + 本卡禁令），成员不用每次重读长说明书。
2. **复核前先蒸馏**：复杂任务的 review 前加一个「错误/成果摘要」中间步（可由成员自交活时完成，见 (a) 的 self_check/known_issues，或由调度员派一张汇总卡），审核者面对摘要而不是原始长输出。
3. **回炉次数按环设顶**：Pack 现在同任务最多 3 次尝试是全局值；可细化为「审查打回环」单独计数并设顶（ChatDev 审查环给到 10、测试环只给 3——越接近客观验证的环顶越低），到顶强制升级调度员裁决而不是继续磨。
4. **上下文钉子**：任务卡原文对执行体全程钉住（keep 语义），执行体每轮开工只带「卡原文 + 本轮意见 + 成果物引用」，不带整段历史对话——与 Pack 附录 G 的记忆硬顶正好接上。
5. **记忆按岗授权**：成员记忆的读写权限、检索时机做成配置（谁只读、谁能写），不要默认全员可写共享记忆（防记忆毒化，与 Rakazo 拆解里「学到的东西是派生数据」一条同向）。
6. **多数投票留给高风险单发任务**：拿不准又不可逆的活，同一卡派 2–3 个执行体各出一份、调度员取多数/择优——ChatDev 有现成开关，Pack 可作调度员手工模式，不必先做成系统功能。
7. **不要抄的**：边条件 fail-open（异常当通过）；最终结果无结构化信封（它没做，Pack 按 (a) 做得比它好）；角色提示词里靠「you'll be fired」式恐吓语加强约束（对 Pack 的成员说明书没必要，禁令写明即可）。
