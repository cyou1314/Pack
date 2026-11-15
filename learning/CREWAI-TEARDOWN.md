# CrewAI 拆解笔记（crewAIInc/crewAI）

> 审计方式：2026-11-15 浅克隆到 `~/workspace/learn-inspect/crewai`，HEAD `d7dfa0e`，纯静态读码，未运行、未填凭证。路径均相对 `lib/crewai/src/crewai/`。
> 一句话定性：CrewAI 是**单进程内的编排框架**——一份配置在内存里一次跑完（kickoff），不是常驻服务。它的「委派」是进程内函数调用，不是跨进程任务分发；Pack 的中枢＋租约＋认领那套问题，它根本不在同一个问题域里。

## 1. 三件套的定义与配置

- **Agent**（`agent/core.py` 的 `Agent(BaseAgent)`，Pydantic 模型）：身份三件套 `role / goal / backstory`（纯提示词），加 `llm`、`tools`、`max_iter`、`max_rpm`、`max_execution_time`（秒，默认 None）、`max_retry_limit`（默认 2）、`allow_delegation`、`memory`、`guardrail`＋`guardrail_max_retries`（默认 3）、`step_callback`。
- **Task**（`task.py` 的 `Task(BaseModel)`）：`description`＋`expected_output`（都是自然语言）、`agent`（静态绑定执行者）、`context`、`tools`（任务级工具白名单，覆盖 agent 的）、`callback`、`human_input`、`async_execution`、结构化输出三件套（见 §2）、`id` 为 uuid（构造时生成、frozen）。
- **Crew**（`crew.py` 的 `Crew`）：`agents`＋`tasks`＋`process`（`process.py`：只有 `sequential` / `hierarchical` 两值，consensual 还是 TODO 注释）、`manager_llm / manager_agent`、`planning`（先让 planner 给每个任务补计划再执行）、`step_callback / task_callback`、`checkpoint` 配置、`kickoff(inputs)` 入口（inputs 做 `{变量}` 插值填充 description）。
- **两种配置方式**：①纯代码构造三个 Pydantic 对象；②YAML 声明式——`project/crew_base.py` 的 `@CrewBase` 约定 `config/agents.yaml`＋`config/tasks.yaml`，方法上 `@agent`/`@task`/`@crew` 装饰器按名字装配，tasks.yaml 里 `agent: <role>` 字符串在 `crew.py` 的 `_create_task` 里按 role 查表绑成对象引用。

## 2. 任务依赖与输出传递

- **依赖只有一种形态：`Task.context: list[Task]`**（`task.py` L177）。未指定（`NOT_SPECIFIED` 哨兵）时默认吃**此前全部任务**的输出；显式给列表就只吃那些任务的。
- **传递方式是文本拼接**：`crew.py` 的 `_get_context()` 把上游 `TaskOutput.raw` 用 `aggregate_raw_outputs_*` 拼成一段字符串，塞进下游任务的 prompt。没有变量级/字段级引用，下游拿到的是全文。
- **执行顺序即依赖**：sequential 模式严格按 tasks 列表顺序跑（`_execute_tasks`）；`async_execution=True` 的任务先并发起跑，下一个同步任务会先等它们收齐。另有 `tasks/conditional_task.py` 的 ConditionalTask：按上一个输出决定跳不跳过。
- **结构化输出**（`tasks/task_output.py` 的 `TaskOutput`：`raw / pydantic / json_dict / output_format` 三态）：Task 上 `output_pydantic`（要一个 Pydantic 对象）、`output_json`（要 JSON dict）、`response_model`（走提供商原生结构化输出）。下游 context 拼接用的仍是 raw 文本，结构化结果主要给程序消费（`CrewOutput.tasks_output`）。
- **output_file**：Task 可指定落盘路径，执行完 `_save_file` 写 raw（`task.py` L822 起；路径有防穿越/防 shell 展开校验，L516 起）。这是「交活落盘」的现成形态，但只是单机文件、无校验和、无回执。

## 3. 委派工具：实现与护栏

- 实现在 `tools/agent_tools/`：`AgentTools(agents).tools()` 生成两个工具——`Delegate work to coworker` 与 `Ask question to coworker`（`agent_tools.py`），入参 schema 只有 `task / context / coworker`（coworker 是 **role 字符串**）。
- 执行体 `base_agent_tools.py` 的 `_execute()`：把 coworker 名做大小写/空白归一后按 role 匹配，**当场 new 一个临时 Task**（description=委派内容，expected_output 固定一句「manager 请求」模板），同步调 `selected_agent.execute_task(...)`，结果字符串直接当工具返回值。**委派不进 Crew 的任务列表**——没有任务卡、没有状态、没有记录（只有一个 `Task.delegations` 计数器，`task.py` L1229）。
- 护栏盘点（薄）：
  - 唯一的门是 `allow_delegation`：`crew.py` 的 `_prepare_tools()` 只在该标志为真时才把委派工具加进工具集；manager 强制为真（`_create_manager_agent`）。
  - role 匹配不到 → 返回一段错误文本给 LLM 自行纠正，不抛异常。
  - **没有**委派深度上限、**没有**环检测（A 委派 B、B 再委派 A 在代码层无人拦，只受各自 max_iter 自然耗尽）、**没有**批准环节、**没有**目标校验（coworker 只要 role 对得上就行）。
  - Ask question 与 Delegate work 共用同一 `_execute`，区别只在提示词措辞。

## 4. Hierarchical 模式：manager 怎么分活与验收

- 入口 `crew.py._run_hierarchical_process()` → `_create_manager_agent()`：没给 `manager_agent` 就用 `manager_llm` 现造一个（role/goal/backstory 取 i18n 的「hierarchical_manager_agent」模板，工具=全员的委派工具组，`allow_delegation=True`）；给了自定义 manager 则**强制清空它的业务工具**（有非委派工具直接抛错「Manager agent should not have tools」）。
- **分活方式出人意料**：`_get_agent_to_use()`（`crew.py` L1742）在 hierarchical 下对**每个任务都返回 manager 本人**——任务列表照旧顺序执行，但每个任务名义上由 manager 执行，manager 在自己的 ReAct 循环里决定「亲手做」还是调 Delegate 工具把活（或拆出的子活）派给 role 合适的成员。Task 上静态绑的 agent 在此模式下被架空。
- **验收方式：没有独立验收环节**。manager 拿到委派返回的字符串后，自行判断够不够、要不要再派一轮——验收就是 manager 的 LLM 判断，活在同一段对话里。唯一的程序化关口是 Task 的 `guardrail`（函数或 LLM 描述式校验，失败按 `guardrail_max_retries` 让执行者重做）和 `human_input`（弹人工确认）。没有 review 状态、没有第三方关单者。

## 5. 重试 / 超时 / 回调

- **重试（全是执行层，没有任务级尝试台账）**：
  - Agent 执行出错 → `agent/core.py._handle_execution_error` 按 `max_retry_limit`（默认 2）**整段重跑 execute_task**，`_times_executed` 计数，超限抛错终止整个 kickoff。
  - Guardrail 不过 → 按 `guardrail_max_retries`（默认 3，Task 与 Agent 各一份）把校验反馈塞回去重做。
  - LLM 工具循环有 `max_iter` 上限防打转。
- **超时**：只有 Agent 级 `max_execution_time`（秒）——`agent/core.py` L1066 起，超时抛 TimeoutError 并终止。**Task 本身没有超时字段**，更没有「超时回池给别人」。
- **回调（齐全）**：Task.callback（任务完成）、Crew.task_callback / step_callback（每任务/每步）、Agent.step_callback；外加一整套事件总线（`events/`，task_started/completed/failed、agent_execution_* 等）可订阅。`human_input=True` 是内建的人工卡点。

## 6. 它缺什么（逐项确认）

对 `crew.py / task.py / agent/core.py / crews/utils.py / tools/agent_tools/` 全文 grep `claim|lease|heartbeat`：**零命中**（唯一命中是 guardrail 的 deprecated 文案）。

| 纪律 | 结论 | 依据 |
|---|---|---|
| 原子认领 | **没有** | 执行者在定义时静态绑定（sequential）或由 manager 进程内直调（hierarchical/委派）；不存在「多个执行体竞争同一任务」的场景与代码 |
| 租约/心跳 | **没有** | 单进程同步/异步调用栈，无租约、无心跳、无超时回收 |
| 任务级尝试上限 | **没有**（近似物在别的层） | 只有 Agent 级错误重试 2 次与 guardrail 重试 3 次，绑在执行者身上、不按任务记账、超限即整场失败，不回池、不换人 |
| 持久化状态机 | **没有** | 任务状态只活在内存对象上（`output / start_time / end_time / processed_by_agents`）；`state/` 的 checkpoint 是**事件日志落盘**（JSON/SQLite provider，task_started/completed/failed 等事件），供 `replay` 从断点重放续跑——是事后重放，不是可查询、可被他人接管的状态机；进程一死，进行中任务的执行权无从转移 |
| 审核关单 | **没有** | 见 §4：验收=执行链内部的 LLM 自判断＋guardrail，无独立关单者、无 review 态 |
| 成员隔离/身份 | **没有** | agent 只是同进程对象，role 是字符串；无凭证、无身份、无权限边界（委派靠 role 名匹配） |

补一句公道话：这些「缺」是定位使然——它是给开发者在自己进程里编排一次性流水线的框架，不是给多个独立账号常驻协作的产品。它不需要认领，正如 Pack 不需要它的 YAML 装配。

## Pack 可借的（按价值排序）

1. **结构化交活 schema**（`output_pydantic / output_json / response_model`＋`TaskOutput` 三态）：Pack 的 task_complete 现在交自由文本；给任务卡加可选的输出 schema，交活时中枢按 schema 校验、不过打回——比调度员肉眼验更硬，且天然接得住现有 review 环节。
2. **context 显式依赖声明**（`Task.context` 列表＋默认全量两种语义）：Pack 派卡时可加「依赖任务 #N」字段，中枢在关单时把上游 result 自动拼进下游任务卡正文——现在这步靠调度员手动粘贴。它的「默认吃全部上游」语义可直接抄成默认值。
3. **guardrail 式自动验收**（函数/描述式校验＋定向重试，反馈塞回重做）：Pack 的 review 前可加一层机器预检（格式、字数、文件存在性这类可判定项），不过的自动打回并附原因，省调度员的审核轮次；重试上限正好套 Pack 既有的 3 次尝试纪律。
4. **output_file 交活落盘约定**（含路径防穿越校验）：[ENG-image] 那类出图任务可约定「产物落指定路径＋回执带路径」，中枢只验文件存在与路径合法，比把产物塞消息里干净。
5. **事件总线形态**（task_started/completed/failed 标准化事件）：Pack 中枢已有审计表，把事件命名与载荷规范成它这套形状，将来 App 任务板做实时推送时直接受益。
6. **YAML 声明式成员/任务装配**（@CrewBase＋agents.yaml/tasks.yaml 按 role 装配）：成员注册信息（role/goal/工具白名单）将来可做成声明文件入仓，而不是散在代码里——量级小，顺手级。

## CrewAI 缺而 Pack 已有的（确认 Pack 路线没走偏）

- **原子认领＋claim_token**：中枢服务端原子认领，防两个执行体抢同一张卡——CrewAI 委派是进程内直调，天然无此问题，也天然给不了多机多账号场景。
- **租约＋心跳＋超时回池＋同任务 3 次尝试上限**：Pack 的执行体是会掉线、会睡死（Mac 离线）的独立进程，这套纪律是刚需；CrewAI 进程死了整场重来（或靠事件 replay），没有「换个执行体接着干」的概念。
- **持久化状态机**（open→claimed→working→review→done＋旁路态，SQLite 落盘）：Pack 的任务跨天跨重启都在；CrewAI 的任务只活一次 kickoff。
- **独立审核关单**：Pack 只有调度员能关单、成员交活先进 review；CrewAI 的验收是执行链内部自判断，manager 既分活又验收，无制衡。
- **身份与凭证边界**：Pack 成员是独立登录的账号、token 只存哈希、房间不放凭证；CrewAI 的 agent 是同进程对象，role 字符串即「身份」。
- **批准门与规则库**（不可逆动作回主人批准、附录 G 四条已上线）：CrewAI 只有 human_input 一个人工卡点和 guardrail，没有批准体系。
