# Pack Phase 4：模块边界与渐进拆分方案

> 状态：执行中；HubContext 与多数领域服务已拆分，Hub 入口保留 MCP 注册/鉴权兼容层；macOS Views 已按功能拆分，AppState 已抽出 Connection/Members/Rooms/Tasks/Notifications/Approvals/Chat/Lifecycle/Catalog 扩展；Settings 状态属性仍由 AppState 托管，跨域状态所有权继续评估。  
> 原则：先冻结协议与数据契约，再按领域提取模块；不做大爆炸式重写。

## 1. 当前代码边界

以下行数来自本次整改分支的仓库快照，仅用于估算拆分成本：

| 区域 | 当前文件 | 规模 | 现状 |
|---|---|---:|---|
| Hub 服务端 | `hub/hub.py` | 约 2,874 行 | 保留 FastMCP 入口、认证/工具注册与兼容 helper；多数成员、房间、任务、规则、附件、记忆、技能、桌面和配对业务已迁入 `hub/services/` |
| App 状态与入口 | `PackApp.swift` / `AppState.swift` | 19 / 200 行 | 启动/刷新流程与 Connection/Members/Rooms/Tasks/Notifications/Approvals/Chat/Catalog 方法已迁入领域扩展；AppState 主要保留状态属性与应用级协调 |
| App 视图 | `Views.swift` + Feature Views | 233 + 430/448/285/64 行 | 主窗口、Chat、Tasks、Settings、TaskDetail 和 Components 已按声明边界拆分；本批仅移动声明，不改布局/行为 |
| 网络客户端 | `HubClient.swift` / `Networking/HubTransport.swift` | 107 / 74 行 | HubClient 保留 MCP session、JSON-RPC、SSE 解析与会话恢复；HTTP 请求、Bearer Header、超时和同源重定向策略独立在 HubTransport |
| 领域模型 | `app/Sources/PackApp/Models.swift` | 276 行 | 仍有 App 侧消息/任务/成员模型；PackCore 保持独立且不依赖 SwiftUI |
| 成员交接 | `app/Sources/PackApp/MemberHandoff.swift` | 约 200 行 | 本机交接/IPC 辅助能力 |
| 其他功能 | `SearchPanel.swift`、`AddressSource.swift` | 约 98 行 | 搜索和地址来源辅助 |
| Core | `app/Sources/PackCore/` | 3 个文件 | 当前包含 PackConfig、HubEndpoint、ApprovalRuleScope；应继续保持无 SwiftUI 依赖 |

Hub 的 MCP tool 注册函数大多位于服务构造流程内，直接移动函数会牵涉闭包捕获的数据库连接、配置、actor 身份、审计函数和服务实例。必须先建立显式上下文，不能仅按行切文件。

## 2. 目标模块边界

### 2.1 Hub

建议保留 `hub/hub.py` 作为兼容入口，逐步形成以下结构：

```text
hub/
  hub.py                 # CLI/服务入口、配置加载、FastMCP 实例
  core/
    context.py           # HubContext：DB、配置、actor、audit、clock
    database.py          # 连接、事务、迁移与查询基础设施
    auth.py              # Token、角色、成员身份与权限检查
    policy.py            # action_tag、rule_eval、approval/access policy
    errors.py            # 对外错误码与安全日志分类
  domains/
    members.py           # 成员、在线状态、成员能力
    rooms.py             # 房间、会话与消息
    tasks.py             # 任务生命周期、claim/finish/transition
    approvals.py         # 审批请求、决策与持有人校验
    rules.py             # allow/deny 规则、范围和优先级
    attachments.py       # 附件元数据、上传/下载、清理
    memory.py            # 记忆读写和来源
    skills.py            # Skill 发现、安装和执行契约
    desktop.py           # 本机控制、handoff、运行状态
  tests/
```

每个领域模块暴露 `register_tools(mcp, context)`，只负责注册自身 MCP tools；所有模块共享同一个 `HubContext`。不要让领域模块自行创建第二个 DB 连接池或重新实现鉴权/审计。

### 2.2 macOS App

建议按“Core / Transport / State / Feature UI”分层：

- **PackCore**：纯模型、配置、凭证策略、URL/重定向策略、审批范围等；不依赖 SwiftUI。
- **PackNetworking**（可先作为 PackApp 内独立目录，稳定后再建 target）：HubClient、JSON-RPC DTO、重试/超时和连接错误分类。
- **PackApp/State**：ConnectionStore、RoomStore、MemberStore、TaskStore、ApprovalStore、SettingsStore；按现有观察机制保持主线程状态更新。
- **PackApp/Features**：Rooms、Agents、Tasks、Approvals、Search、Settings、Computer 等功能视图。
- **PackApp/Components**：按钮、卡片、头像、状态标识、弹窗等可复用组件。
- **PackApp/AppCoordinator**：仅保留启动、依赖装配、导航和跨 Feature 协调，不再承担所有领域状态。

不建议一开始就新增多个 Swift Package target。先按目录和访问级别拆分，等依赖方向稳定后再决定是否拆 target。

## 3. 迁移顺序

1. **冻结契约**：生成 MCP tool 名称、参数 JSON Schema、返回结构快照；记录 SQLite 表/索引/迁移快照；记录现有 URL、权限和审计语义。
2. **抽纯逻辑**：先将 action tag、rule specificity、access mode、approval gate 等无副作用逻辑移入 `hub/core/policy.py`，保留兼容导入并补单元测试。
3. **建立 HubContext**：把 DB 连接工厂、配置、actor、审计和时钟显式注入；此步骤不改变 tool schema。
4. **逐领域迁移 Hub tools**：先 members/rooms，再 tasks/approvals/rules，再 attachments/memory/skills，最后 desktop/runtime；每次只移动一个领域。
5. **拆 Swift 模型与网络层**：将无 UI 依赖的 Codable 模型移入 PackCore；HubClient 先独立成 Transport 目录，再稳定成 target。
6. **拆 AppState**：每次提取一个 Store，旧 AppState 暂时保留 façade/转发接口，避免一次性改动全部 View。
7. **拆 Views.swift**：按 Feature 移动视图；先移动不改布局和行为，再做 UI 优化。
8. **清理兼容层**：只有引用扫描、schema snapshot、单测和 staging 回归都通过后，才删除旧实现/转发层。

## 4. 不可破坏的契约

- MCP tool 名称、输入字段、默认值、返回 JSON 和错误语义保持不变。
- SQLite schema、索引、迁移版本和既有用户数据保持不变；如确需迁移，必须另立变更并提供备份/回滚。
- Token、Keychain service/account、配置路径和本机 IPC 路径保持兼容。
- 权限检查、审批持有人、规则优先级、审计事件不能因移动模块而改变。
- App 的导航、会话恢复、任务更新和审批交互保持现有行为。
- 不在模块拆分提交中顺带改名 MCP tools、换状态管理框架或重写 UI。

## 5. 每个拆分批次的验收

- MCP tool/schema snapshot 无意外差异。
- Hub policy/domain 单元测试通过。
- 当前默认 staging 回归全绿。
- SQLite schema/迁移快照无意外差异。
- Swift build/test 通过；涉及 UI 的批次增加手工 smoke test 清单。
- 变更仅涉及当前领域；无关文件不格式化、不重构。
- 有明确回滚点；回滚不要求用户迁移数据。

## 6. 第一项可执行任务（已完成）

Phase 4 不应直接拆 5,000 行 Hub。第一项采用低风险提取样板：

1. 将 Hub 纯 policy helper 提取到 `hub/core/policy.py`；
2. 保留 `hub.py` 兼容导入，确保现有 tool closure 无需改变；
3. 将 `hub/test_policy_helpers.py` 改为直接测试 policy 模块；
4. 增加 MCP tool/schema 与 SQLite schema/迁移快照，防止后续领域迁移改变协议；
5. CI 全绿后，再抽取 `HubContext`。

以上 1–4 已完成；第 5 项尚未开始。

这一步改动面小，能验证模块边界和测试方法，为后续拆分建立样板。


## 7. 执行记录（2026-11-21）

Phase 4 已进入执行阶段。当前完成了纯策略、基础 SQLite 结构/访问层、初始 HubContext 和契约冻结样板；尚未迁移 MCP 领域工具，也未改变 SQLite 的运行语义。

### 已落地的模块边界

- `hub/core/policy.py`：纯策略函数，包括 action tag、access mode、退避、executor pinning、egress host/domain 规则和规则优先级选择。
- `hub/core/schema.py`：集中存放原 `hub.py` 中的 SQLite 基础 `SCHEMA` 常量；`hub.py` 通过兼容导入继续调用。
- `hub/core/storage.py`：集中存放 SQLite 连接配置和 audit insert 原语；`hub.py` 保留 `db()` / `audit()` 兼容入口，事务提交仍由调用方负责。
- `hub/core/context.py`：`HubContext` 集中保存 base/db/attachment 路径、共享锁、wall clock 和 business clock；默认 `db()` / `audit()` / `_lock` 兼容入口转接到默认 context。
- `hub/services/rooms.py`：首批房间读服务已迁出，包括房间列表、摘要读取和新消息读取；`hub.py` 保留原 MCP tool 名称、参数签名与 docstring，只负责鉴权和注册。
- `init(context=...)`、`authenticate(context)`、external/access-mode gates、`build_server(context)`、`sweep_once(context)`、`sweeper(context)` 和 `serve(context)` 已支持显式 context；MCP 工具闭包绑定同一个 context 的 DB、audit、lock、clock 和附件根目录。
- `_close_run`、`_approval_gate`、memory、quota、attachment、inbox、relay 等共享 helper 已增加 context 参数，旧调用仍回退到默认 context。
- `hub/test_context.py` 使用隔离 DB/token 目录验证 init、认证、拒绝审计、inbox、lease sweeper，并通过 FastMCP in-memory Client 实际调用 `member_list`。
- `hub/test_policy.py`、`hub/test_storage.py`、`hub/test_context.py`：覆盖纯策略、egress rule adapter、WAL/busy_timeout、audit 事务边界和 context 注入。
- `hub/test_rooms_service.py`：覆盖归档筛选、最后消息游标、external 房间绑定、摘要默认值与越权拒绝。
- `hub/test_packaging.py`：复制实际 Hub 运行包并验证可注册 MCP 工具；同时检查 local install、DMG、manager install/uninstall 的 core/services 文件清单。
- `hub/export_contracts.py`：从实际 FastMCP 注册表和临时 SQLite 初始化数据库生成契约，不从手写清单推测工具接口。
- `hub/contracts/`：已提交 MCP 工具和 SQLite schema/迁移基线；更新规则见同目录 README。

### 契约快照基线

- MCP：当前实际注册 **75 个工具**，快照包括名称、描述、输入 JSON Schema 和输出 JSON Schema。
- SQLite：fresh init 与 legacy fixture upgrade 均得到 **24 张表**；快照覆盖列、索引、外键和触发器。
- Legacy fixture 验证既有 member/token hash、room、message、task 和 task transfer 数据保留；旧 transfer 状态会按当前迁移逻辑回填为 `accepted`。
- `python hub/export_contracts.py --check` 已接入 CI；CI 只检查，不自动改写快照。
- 快照导出使用临时目录复制 `hub.py` 与 `hub/core/`，不会接触开发机或生产 Hub 数据库。

### 兼容与安全修复

- 本地安装、DMG 构建、安装器 manager 和卸载脚本现在都会同步复制/清理 `hub/core/` 与 `hub/services/`；安装时执行 FastMCP 工具注册 smoke test，preflight 检查关键模块文件。

- `run-all-suites.sh` 在每个隔离 staging 副本中复制整个 `hub/core/` 包；历史 staging 源目录保持不变。
- 恢复 `hub/publish-url.sh` 的 Git 可执行权限。
- 将 URL 校验内置回单文件 `runtime/pack-agent.mjs`，保持现有外部 Agent 单文件安装方式，并增加独立安装回归测试。
- 两条对应的 P1 代码审查意见已回复并解决。

### 自动化验证

- Run #313：Hub 单测、17 组默认 staging、Runtime、Swift build/test、App Bundle 检查全部通过。
- Run #320：Hub 单元测试、MCP/SQLite 契约快照检查、Runtime 测试、macOS App 构建/测试和默认 staging 回归全部通过。
- Run #330：Hub 单元测试、契约快照、Runtime 和 macOS App 检查通过；默认 staging 回归仍在运行。
- Run #331：新增 FastMCP in-memory Client 实际调用测试后，Hub 单元测试与契约快照检查通过；Runtime 和 macOS App 检查通过，staging 回归随后通过。
- Run #339：包含首批 rooms service 和模块化安装包修复；Hub 单元测试、契约快照、Runtime 和 macOS App 检查通过，完整 staging 回归仍在运行。
- 用户 Mac 的 LaunchAgent、Keychain、DMG、真实账号登录态和数据恢复验收尚未开始，继续遵守“仓库整改完成后再跑本机”的顺序。

### 下一步（未完成）

1. 完成 rooms 写服务迁移，再按 members、tasks/approvals/rules、attachments/memory/skills、desktop/runtime 顺序从 `hub.py` 提取 service modules；继续显式传递 HubContext。
2. 每个领域迁移均保持 MCP tool schema 快照无意外差异，并补充事务、权限和 legacy 数据测试。
3. 迁移完成后删除不再需要的全局兼容 wrapper，保留明确的应用入口。
4. 完成 Hub 领域层后，再拆 Swift Core、Transport、Store 与 Feature UI。


### 追加执行记录：成员生命周期与任务读取服务（2026-11-21）

- 成员服务 hub/services/members.py 现已承接成员列表、登录状态、访问模式、展示名、Token 轮换、调度员额度维护、自报额度防伪造和成员软删除；hub.py 继续持有 MCP 注册与权限检查。
- 对成员软删除补充了事务回滚故障注入测试：在 Token 作废阶段注入失败，核验任务、转交、技能、桌面锁、成员状态与审计全部回滚。
- 对同一成员的并发移除补充了双线程竞争测试，验证仅一次成功、另一请求因成员已移除而拒绝，且仅产生一条成功移除审计。
- 新建 hub/services/tasks.py，初步提取任务转交历史与任务完整审计的只读查询。MCP task_transfers_list 与 task_audit 的工具名、参数、说明和认证保持不变，写入及状态转换暂不迁移。
- hub/test_tasks_service.py 覆盖按 id 排序、任务不存在拒绝、精确任务前缀归属、run/transfer 台账与事件 200 条截断；Hub 安装包 smoke test 同时检查新 tasks service 文件。
- 继续遵守每批次通过 Hub 单测、MCP/SQLite 契约快照、staging 与 macOS CI 后才扩展到任务认领、完成和审批领域的要求。

- 任务列表 MCP task_list 已迁至 hub/services/tasks.py；保持原有排序、状态多选、队列序号、退避展示、外部成员房间/指派约束及分页语义，并通过 try/finally 关闭数据库连接。
- hub/test_tasks_service.py 增补优先级与多状态组合、JSON envelope、退避时间、外部成员越权过滤及跨越 100 条分页的全队列序号测试；安装包 smoke test 检查 task_list 工具仍可注册。


### 任务写入迁移：创建、加急与排班（2026-11-22）

- task_create 与 task_set_urgent 已迁入 hub/services/tasks.py；保持原 MCP 工具声明、dispatcher 鉴权、审计名称和输出结构。task_create 的任务记录、task_card 消息与审计在同一事务提交。
- task_schedule 已迁入任务服务；保持业务时钟下的未来排班约束、next_claim_at 与 sched_at 同步写入、仅允许在册 AI member、external 绑定房间及 executor-pinned 任务禁止指派等规则。
- hub/test_tasks_write.py 覆盖创建/加急/排班成功和拒绝路径、重复加急审计、wall/business clock 差异，以及任务卡插入失败时的事务回滚。
- hub/test_packaging.py 确认隔离运行包仍注册 task_create、task_set_urgent、task_schedule 这三个原 MCP 工具。
- 认领租约、任务完成与审批状态机尚未迁移；完整 macOS 本机安装及登录态验收仍按原计划后置。


### 任务认领原子性迁移（2026-11-22）

- `task_claim` 的任务写入迁入 `hub/services/tasks.py:claim_task`；`hub.py` 仍保留同名 MCP 工具声明、鉴权、文档字符串和返回结构。
- 外部成员绑定房间、本人指派、executor-pinned 检查仍与单条 CAS UPDATE 位于 `HubContext.lock` 同一临界区，避免 TOCTOU；认领成功的审计和遗留桌面锁清理仍处于同一事务。
- 新实现使用注入的 `HubContext.now()` 业务时钟与既有 `LEASE_SECONDS`，并用 `try/finally` 保证拒绝分支及 DB 异常也关闭连接。
- 新增 `hub/test_task_claim.py`，覆盖单人认领、拒绝与退避、external 约束、遗留锁清理、故障回滚、双线程竞争；接入 CI。
- 本批不迁移 `task_update`、`task_complete`、`task_block`、转交和批准状态机；保留后续逐批验收边界。


### 任务进度写入迁移（2026-11-22）

- 将 `task_update` 的持有人认证之后的任务校验、条件更新、续租和进度消息写入迁入 `hub/services/tasks.py:update_task`；`hub.py` 保留原 MCP 名称、文档字符串、鉴权和访问模式闸。
- 保持原逻辑：旧 claim_token 经 `hmac.compare_digest` 检查，再由受持有人 id+token 约束的 SQL UPDATE 决胜；被替换的持有人不能覆盖新任务状态。
- external 成员的绑定房、4000 字 note 限制、三窗口速率计数与写事务继续在统一 `HubContext.lock` 内执行；触限审计单独提交，拒绝续租。
- 写入报告消息失败时由连接关闭回滚任务状态；清理原实现的重复 close 分支。
- 增加 `hub/test_task_update.py` 七个隔离 DB 测试：普通续租/报告、无备注、持有人拒绝、external 边界、频控审计、写入故障回滚和并发限流，并接入 CI。
- 本次不迁移 `task_complete`、`task_block`、审批与转交；无需数据库迁移或客户端接口变更。


### 任务完成服务迁移（2026-11-22）

- `task_complete` 的持有人校验、envelope 校验、任务状态转 review、run 关闭、desktop lock 释放、report 消息、附件绑定与审计迁入 `hub/services/tasks.py:complete_task`。
- 原 MCP 名称、输入/输出、文档说明和成员权限闸不变；继续使用原 constant-time token 比对及带 assignee/claim_token 条件的 UPDATE。
- 交付写入各步骤使用同一 SQLite 事务；附件部分绑定、报告消息写入或审计失败时关闭连接回滚，不留下孤立的 review 状态、已关闭 run 或已释放的桌面锁。
- 新增 `hub/test_task_complete.py` 七项测试：纯文本交活及 run/桌面锁、结构化 envelope、无效 envelope、合法附件、部分附件回滚、report 插入失败回滚、错误 token 与 external 跨房限制。
- CI 增加该测试，安装包 smoke test 覆盖 task_claim / task_update / task_complete；不修改数据库 schema 或真实部署。
- `task_block` 的 quota 耗尽与自动接力保持在 `hub.py`，下一批单独迁移。


### 任务报卡服务迁移（2026-11-22）

- `task_block` 的权限后置业务逻辑迁入 `hub/services/tasks.py:block_task`；`hub.py` 保留原 MCP 工具名、签名、描述、认证和工具访问模式闸。
- 保留 `hmac.compare_digest` 令牌校验与受 assignee/token 约束的 UPDATE、失败分类缺省 unknown、基于业务时钟的指数退避、run 关闭与 desktop lock 释放。
- quota 类报卡仍将 member 额度标记为 100%、source=fact 并写审计；既有候选筛选、transfer cap、pending 转交与执行者绑定规则继续由原 `_auto_relay` 实现。
- 对自动接力增加 SQLite SAVEPOINT 隔离：接力发生写入异常时回滚接力部分变更，不再允许失败的转交/分配写入随 block 事务提交；任务 block 和额度审计仍保留。
- 新增 `hub/test_task_block.py` 七项隔离 DB 测试：正常报卡+run/锁清理、失败类别退避、权限/错令牌、quota 无候选、quota 自动接力、部分接力写入失败回滚、关闭 run 异常回滚。
- CI 增加单测，打包 smoke test 增加 `task_block` 工具注册；未修改 SQLite schema、MCP 契约或真实用户数据。


### 调度员任务验收与取消迁移（2026-11-22）

- 将 `task_review` / `task_cancel` 的权限后置服务迁入 `hub/services/tasks.py`。MCP 原签名、docstring、dispatcher 权限校验保持在 `hub.py`，没有新 MCP 工具，也未变更 SQLite schema。
- `review_task` 保留 approve 时状态 done 并清理失败类别/退避；reject 时状态 open、保留 attempts、按 fail_class 或 unknown 重算 backoff；旧成员被移除时重新释放 assignee。
- `cancel_task` 保留 done/cancelled 的审计型幂等返回；其他状态取消、run 关闭、desktop lock 释放、取消审计仍在单笔 SQLite 事务内提交。
- 收紧任务状态写入：review 的 UPDATE 增加 `status='review'` 条件，cancel 的 UPDATE 排除 `done/cancelled`，核对 rowcount 并拒绝覆盖并发变更；移除原早返与异常路径遗漏的连接关闭。
- 新增 `hub/test_task_review_cancel.py` 九项隔离数据库测试（批准、拒绝及退避、已移除 assignee、非法状态、审计失败回滚、取消 run/桌面锁、重复取消审计、缺失任务、释放异常整体回滚）。
- CI 接入新测试；安装包 FastMCP 注册 smoke test 追加 task_review / task_cancel。


### 任务转交发起与接受服务迁移（2026-11-22）

- 将 `task_transfer` 与 `task_accept_transfer` 从 `hub.py` 的闭包提取到 `hub/services/tasks.py:transfer_task`、`accept_task_transfer`；保留原 FastMCP 注册、文档字符串、输入/输出和成员/访问模式认证。
- 发起转交继续校验旧持有人 claim_token，dispatcher 可强制转交；目标必须是未移除的内部执行成员，禁止 owner、dispatcher、external、自转交和同任务已有 pending；达到 TRANSFER_CAP 时仍向房间发告警并留审计后拒绝转交。
- 转交成功时原任务状态/attempts 不变，原 token 与 lease 清零，关闭 running Run、释放 desktop lock，在同一事务写入 pending 转交、调度员聊天消息及审计；任务条件 UPDATE 新增 status、assignee、claim_token 谓词，防止旧快照覆盖其他持有人。
- 接受转交仅接收方本人可对最新 pending 且未过期的转交领取新随机 token，attempts 不变。先在数据库中带 pending/expiry 条件标记 accepted，再对任务做持有人/status/空 token 条件更新；任何失败都回滚整笔，防止与 sweeper 的 expired 状态竞态。
- 新增 `hub/test_task_transfer.py` 九项隔离 DB 测试，覆盖 run 与 desktop 解锁、token 轮换、强制转交、目标与 pending 拦截、接力上限告警、超时及错误接收方、事务回滚、审计异常回滚和双线程接受竞争；纳入 Hub CI 和安装包工具注册 smoke test。
- 本批暂不迁移 `sweep_once` 的 pending 超时回收，也不改 SQLite schema 或本机数据；后续独立完成 sweeper 模块与并发测试。


### 转交超时回收迁移与 Mac 最小改动约束（2026-11-22）

- 将 `sweep_once` 内的 pending 转交超时回收提取为 `hub/services/tasks.py:expire_pending_transfers`，由原 sweeper 传入同一 HubContext、SQLite 连接及业务时钟，保持原 60 秒轮询、返回计数和整批事务边界不变。
- 过期 ledger UPDATE 复检 `status='pending'` 与 `expires_at<now`；回池 UPDATE 增加接收成员 assignee 条件，同时保持只对无 claim_token 且仍为 claimed/working 的任务回池，不得覆盖已接受或重新指派的任务。
- `hub/test_task_transfer_expiry.py` 七项隔离回归验证：恢复原指派、未过期跳过、原成员移除后解除指派、已接受令牌保护、任务状态/归属/令牌变化保护、审计故障回滚、重复扫与混合过期计数。
- **Mac 部署冻结原则**：本批不修改 `installer/manager.py`、`installer/build-dmg.sh`、`local/install-local.sh`、`local/*.plist`、App 配置键、服务 label、端口和数据路径。不引入 SQL schema 迁移；新增业务代码仍放在原打包脚本已包含的 `hub/services/` 包内。
- 部署前必须单独验收现有安装实例的升级路径：当前 DMG manager 和 local installer 均会拒绝覆盖已存在的本机安装（这是安全约束，不是原位升级能力）。不能以「无需改动即可覆盖升级」宣传；待仓库整体验收完毕，设计一次性备份、差异升级和回滚流程，再在隔离 Mac 验证。


### 任务释放与退避清理迁移（2026-11-22）

- `task_release` / `task_clear_backoff` 的 dispatcher 鉴权之后的业务搬入 `hub/services/tasks.py:release_task`、`clear_task_backoff`，MCP tool 名称、签名、说明和鉴权原样保留。
- blocked 任务放回池仍严格执行 `MAX_ATTEMPTS`，保留原 assignee（成员移除时清空），根据 fail_class 与 attempts 计算业务时钟下的退避；`clear_backoff=True` 则立即允许认领。open 任务的手动清退避同时清理 sched_at，但保留 fail_class 审计信息。
- 两项操作均采用 SQLite 状态条件 UPDATE，并由 rowcount 检查保护任务已变更时不覆写；任务写入与原审计格式继续在一个事务内提交。所有失败路径关闭连接并回滚。
- 新增 `hub/test_task_backoff.py` 八项隔离 DB 测试，覆盖标准退避、清退避、重试上限、已移除指派、状态限制、缺失任务与审计插入失败回滚；纳入 CI 和复制运行时 FastMCP 工具注册 smoke test。
- 本批不涉及 Mac 部署脚本、Keychain、LaunchAgent、数据库 schema 或 API 契约变更；按照原 Phase 4 计划继续后续模块提取。


### Run 生命周期迁移（2026-11-22）

- `run_start` / `run_finish` 的业务实现迁入 `hub/services/tasks.py:start_run`、`finish_run`；原 FastMCP tool 名称、签名、说明及成员/外部成员访问模式闸保留在 `hub.py`。
- 启动 Run 继续检查当前持有人的 claim_token（hmac.compare_digest）、external 绑定房限制以及 claimed/working 状态，同一任务仅一条 running Run，fence 每次从历史最大值递增。
- 重复启动仍先提交 `run_fence_reject` 审计再抛错；成功启动的 Run 与 `run_start` 审计同事务。运行结束继续只关闭 Run、不改变任务状态；完成/失败/中止 outcome 保持原白名单，`run_close` 审计失败时回滚状态。
- 新增 `hub/test_run_lifecycle.py` 八项隔离 DB 测试：开始/关闭及 fence 增长、重复启动审计、outcome 白名单及无 Run、错误 token/任务状态、external 跨房限制、启动/结束审计失败回滚、双线程竞争；纳入 CI 与运行时复制 smoke test。
- 本批不改 SQLite schema、75 个 MCP 工具契约，不修改 Mac 安装路径、启动参数或客户端设置。


### 租约超时回收服务迁移（2026-11-22）

- `sweep_once` 过期 claim 回收迁入 `hub/services/tasks.py:reap_expired_leases`，继续由原 sweeper 按 60 秒轮询，复用原 SQLite 连接和最终事务提交；不新增 MCP 工具、计时器或数据库表。
- 收紧回收 UPDATE：要求任务仍为 claimed/working、原 assignee/claim_token 未变、lease_expires 仍过期，避免旧扫描结果清理已续租或被转交的任务。达到 MAX_ATTEMPTS 转 blocked；其余保留有效 assignee、标 fail_class=transient、按原 backoff 重算 next_claim_at。Run 关闭、desktop 释放与审计仍在调用方事务。
- 超时后符合原接力条件时继续走 `_auto_relay`，使用既有 context.lock 并复检状态；自动接力失败新增 SAVEPOINT：只撤销失败的转交/指派部分写入，保留本轮合法的超时回收与审计。
- 新增 `hub/test_lease_reaper.py` 八项隔离 DB 测试：正常回池与 Run/桌面锁、尝试上限、移除指派、未到期跳过、成功自动接力、部分接力失败回滚、Run 关闭异常回滚、重复扫描计数；加入 CI。
- Mac 安装器、LaunchAgent、Keychain、权限白名单、MCP 和 SQLite 契约未改动；后续继续拆分未认领任务巡检。


### 未认领任务巡检服务迁移（2026-11-22）

- 将 `sweep_once` 的 open 且已指派任务巡检拆到 `hub/services/tasks.py:inspect_unclaimed_tasks`，保留原 `HubContext`、SQLite 连接、60 秒 sweeper、既有 MCP/表结构、审计类型和返回计数。
- 原宽限期（内部 240 秒、external 2700 秒）、心跳陈旧 300 秒、候选配额新鲜度、额度触顶条件与 executor-pinned 排除继续有效；检查顺序保持「已到期 + 过宽限 + 失联或额度高 + 无 pending」。
- 接力记账段依旧使用 `context.lock`，重新核查任务状态/assignee/claim_token，并复检 pending；best-effort 自动接力使用 SAVEPOINT，避免异常时已经写入的转交、任务状态或消息被最终 sweeper 事务一同提交。
- 接力失败且无其他候选时仍按「task id + assignee id」升级一次；维持原消息和 `task_unclaimed_escalate` 审计格式。新测试 `hub/test_unclaimed_sweeper.py` 覆盖过期/重复告警、有效自动接力、时间/标记/待转交/额度/外部成员约束及失败接力回滚。
- 此批保持 Mac 部署冻结，不修改 Agent、UI、安装器、Keychain 或数据库迁移；后续先继续 Hub 消息模块，再推进 Agent/Computer/UI。


### 房间消息编辑与软删除服务迁移（2026-11-22）

- `message_edit` 与 `message_delete` 从 `hub.py` 入口迁至 `hub/services/rooms.py:edit_message` / `delete_message`；原 MCP 工具名、签名、中文描述、成员认证与 external/readonly 闸门保持不变。
- 仅作者本人可以修改 `chat` 类型且尚未删除的消息；external 成员必须在绑定房间内，结构性消息（task_card/report/approval_request/system）不可编辑或删除。编辑保留 20,000 字上限、空白拒绝；删除以原占位文本覆盖正文，保留原软删除字段和只含 message id 的审计。
- 两个 SQL UPDATE 增加 `author_id` 与 `kind='chat'` 约束，继续以 `deleted_at IS NULL` 和 rowcount 防重复删除；修改与审计同事务，异常自动关闭连接回滚。
- 新增 `hub/test_message_mutations.py` 九项隔离测试：成功编辑/软删与读取遮蔽、重复删及删后编辑、非作者拒绝、external 跨房拒绝、结构性消息、输入限制、审计失败整体回滚、双线程重复删除；纳入 CI 和拷贝运行时的 MCP 注册 smoke test。
- 本批未动 `room_post` 附件绑定和审批门槛逻辑、SQLite 表结构或 Mac 部署环境；该复杂写入路径独立后续批次处理。


### 房间消息发送主路径迁移（2026-11-22）

- `room_post` 核心写入迁移到新增 `hub/services/messages.py:post_message`；`hub.py` 仍保留兼容 `_room_post_impl` 薄入口，供 `room_post` 与 `inbox_send` 共享。原 FastMCP 工具名、参数、文档和身份/访问模式闸不变。
- 原 kind 白名单、成员执行任务证明、external 绑定房/频控/审批额度、question payload 与未答去重、question_answer 跨房/重复答检查、附件 1–4 个同房同上传人且未绑定约束继续有效；原策略回调经注入保持单一实现，不在消息服务复刻权限规则。
- `post_message` 负责新建和关闭 SQLite 连接；异常均由 `finally` 关闭并回滚未提交消息、附件绑定和决策副作用，成功提交仍原子。特别修复旧实现中 `_approval_gate` 抛错可能导致的连接未关闭风险；需持久写入的 external 拒绝审计仍按旧路径 `commit` 后抛错。
- 新增 `hub/test_room_post_service.py` 十项隔离 DB 测试，覆盖正常/非法附件、external 房间与类型、external 频控与拒绝审计、approval_request 执行者权限、审批决议原子提交与失败回滚、question/answer 状态机和 kind/payload 限制；加入 Hub CI 和复制运行时 MCP smoke test。
- 未增加数据表、Mac 目录、LaunchAgent 或部署要求。本阶段继续仓库整改，真实 Mac 集成测试最后执行。


### inbox_send 幂等原子事务迁移（2026-11-23）

- 修复 `inbox_send` 的历史双提交窗口：原先 `_room_post_impl` 先提交消息/附件，再独立 INSERT OR IGNORE `message_op_keys`，若后半段失败，同一 op_key 断线重试可能再次发消息。现在改由 `hub/services/messages.py:send_inbox_message` 编排单 SQLite 连接和 `BEGIN IMMEDIATE` 事务，防同一 Hub 进程及多进程竞争。
- `post_message` 增加仅限内部使用的可选 `connection`、`before_commit`，在同一个事务依次落库消息、附件、op_key 和 `inbox_send` 审计，然后一次 commit。hook 抛错时整笔回滚；external 频控拒绝审计和 inbox 防盲回拦截审计仍按原语义持久化。
- `message_op_keys` 唯一键继续是 (member_id, op_key)。发送前同事务查找已持久化 message_id，存在即原样返回 deduplicated；历史 message_id=NULL 行会在同事务填回，绝不覆盖已有非空回执。其他成员同名 key 不冲突；无 key 时保持普通发消息行为。
- 保留 `inbox_send` 原 FastMCP 接口、身份权限、版本门、跨房回复水位、防盲回默认拦截和 ignore_new；底层仍复用 `room_post` 的附件、kind、频控逻辑，不更改表结构或 Mac 安装路径。
- 新增 `hub/test_inbox_atomic.py` 十项隔离数据库回归：断线重试同 key、空 key 兼容、op_key/审计故障整体回滚、盲回拦截后允许强发、跨房/缺失引用、external 入群水位、跨成员 key 作用域、历史空回执修复、同 key 并发单次写入。加入 Hub CI；整套结果以 Actions 为准。


### Inbox 拉取与已读游标服务迁移（2026-11-23）

- `inbox_pull`、`inbox_mark_read` 的业务逻辑提取至 `hub/services/messages.py`，原 FastMCP 工具签名、身份认证、版本门、房间绑定与审计格式保持不变。
- 拉取仍使用近 10 天时间窗口、100 条分页与 external 入群水位；`inbox_pull` 心跳与审计保持同一 SQLite 事务，读取不推进已读游标，显式 since_id 可回看历史。
- 标已读以 SQLite `BEGIN IMMEDIATE` 串行化「读取现有游标→房间最大 ID →写入」，UPSERT 再以 `MAX` 保证同一成员/房间游标只进不退，避免多个 Hub 进程的竞态覆盖；只允许标到本房已存在的最大消息 ID。
- 新增 `hub/test_inbox_read.py` 八项隔离回归，涵盖心跳/内容/审计、回看与频繁拉取、external 水位、时间窗口分页、单调标已读、审计失败回滚和双线程竞争。纳入 CI 和复制运行时 FastMCP 工具注册断言。
- 本批未改 SQLite schema、75 工具契约、Mac 安装/服务/数据路径；真实 Mac 验收继续后置。


### 附件读取与列表服务迁移（2026-11-23）

- `attachment_get`、`attachment_list` 的逻辑迁入 `hub/services/attachments.py`；原 FastMCP 名称、参数、docstring、authentication/访问模式入口及成功响应结构不变。附件上传链路暂不迁移。
- external 的绑定房、入群时间水位继续由数据层实施。附件本体读取在文件打开前核验成员权限、文件路径边界和 symlink；读取后核对 SQLite 记录的 size 与 SHA-256，拒绝内容损坏或被替换文件；失败路径统一关闭连接、不提交成功审计。
- 新增 `hub/test_attachment_read.py` 八项隔离测试，覆盖成功读与审计、external 越权/水位、缺失或损坏文件、软链接路径逃逸、审计异常、列表房间边界和消息过滤；CI 与复制安装运行包 smoke test 同步接入。
- 不更改 SQLite schema、75 MCP 工具契约或 Mac 部署路径/服务标签；本批未修改已有附件数据或安装器，完整 staging/macOS 验收以 GitHub Actions 为准。


### 附件上传事务/跨进程配额与文件防覆盖（2026-11-23）

- `attachment_put` 的身份鉴权后逻辑移至 `hub/services/attachments.py:put_attachment`；原 MCP 名称、参数、返回值、base64/MIME/魔数/10MB/外部成员 5MB 和 24h/50MB/房间 500MB 等限制保持不变。
- 写入前在原 `HubContext.lock` 内申请 SQLite `BEGIN IMMEDIATE`，把房间/成员配额读取、文件发布、附件元数据和成功审计放在串行写入区；多 Hub 进程使用不同锁时仍受 SQLite writer reservation 保护。
- 文件落盘使用 `mkstemp` 唯一 0600 临时文件、`fsync` 和不可覆盖的 `os.link`，禁止重写已有 sha256 目标。已有目标文件会按完整内容重新校验；损坏、非普通文件和符号链接拒绝复用，确保新上传不破坏既有文件。
- 普通异常下，DB 插入或审计失败会回滚并清理由本次操作新建且未被旧元数据引用的目标文件；引用既有内容时不删除共享文件，临时文件在成功及失败路径均清理。由于文件系统和 SQLite 没有分布式事务，**进程被强制终止/断电发生在文件发布和 DB commit 之间仍可能留下孤立文件**，后续应单独加入安全巡检和回收；不宣称跨文件系统完全原子性。
- 新增 `hub/test_attachment_upload.py` 13 项隔离测试（成功读写、相同哈希复用、输入/权限/配额、元数据故障回滚、共享文件保护、篡改与软链接、独立 HubContext 并发抢房间/外部成员每日配额，以及 macOS 系统父路径别名兼容），加入 CI。
- 不变更 75 个 MCP 工具契约/24 张 SQLite 表、Mac 安装器或真实用户数据；仍不合并、不部署。

### 附件只读一致性巡检与备份恢复演练（2026-11-23）

- 新增 `hub/services/attachment_audit.py`，可在打包后的 Hub 环境使用 `python -m services.attachment_audit --db PATH --attachments PATH --grace-hours 24`；不增加 MCP 工具、数据库表或后台定时任务。
- SQLite 以 `mode=ro` 读取附件元数据；巡检对比 content-addressed 文件的存在性、长度、SHA-256、符号链接与路径安全，标识异常元数据、未引用的旧文件及 `.pack-upload-*` 临时文件。
- 默认只读输出 JSON，`--strict` 仅影响退出码；**不会删除、移动或修改任何附件**。未引用文件按默认 24 小时宽限区分近期与陈旧候选，但陈旧不等于可删除：备份/回滚、并发上传、停机时序仍需人工复核。
- 新增 `hub/test_attachment_audit.py` 10 项隔离测试：健康引用、重复引用、孤儿新旧分类、旧临时文件、缺失/同长篡改、符号链接、异常元数据、CLI 只读，以及执行 `backup-db.sh`/`verify-backup.sh` 后恢复数据库和附件到隔离目录并验证。测试不访问真实用户数据。
- 原备份脚本未改；在线 SQLite 快照和附件 tar 并非严格同一时刻的跨资源原子快照，后续仍需设计停机窗口/双阶段一致性验证与 Mac 恢复演练。

### 批准与出口规则服务化（2026-11-23）

- 将 `rule_list/rule_upsert/rule_delete` 及 `egress_rule_list/egress_rule_upsert/egress_rule_delete` 六个 dispatcher-only 工具的数据库主体迁出 `hub.py` 至 `hub/services/rules.py`，保持原 MCP 名称、参数、docstring、鉴权与结果格式，规则求值纯策略仍在 `core/policy.py`。
- 写入和审计使用共享 `HubContext.lock` 与 SQLite `BEGIN IMMEDIATE`，保证同一规则的跨进程增删/更新互斥；事务失败显式 rollback 且统一关闭连接。域名归一使用已有 `egress_pattern` 策略，未复制实现。
- 新增 `hub/test_rules_service.py` 13 项隔离测试，覆盖列表、key 标准化、重复 upsert、停用规则重启用、参数拒绝、删除/不存在、审计失败导致的增删回滚，以及两个独立 HubContext 同键并发更新。接入 CI 和复制运行时 smoke。
- 这批未修改 `egress_check` 的调用频率和判定逻辑，后续应单独加强外部 Agent 高并发出口频控；不改变 75 MCP tools / 24 SQLite tables、Mac 安装/登录/真实用户数据。

### 出口决策工具跨进程限流（2026-11-23）

- `egress_check` 鉴权后的查询、external 最近 60 秒审计次数检查、站点决策、允许/拒绝审计整体迁至 `hub/services/rules.py:check_egress`；原 MCP 签名/说明/返回格式不变。`hub._egress_decide` 仍承接现有规则优先级、任务正文点名与畸形 URL 判定，避免修改判定语义。
- `BEGIN IMMEDIATE` + 进程内 lock 确保多个 Hub 进程在同一 DB 上的频控判断与审计插入原子串行；不会让并发请求同时通过 `EXT_EGRESS_PER_MIN=60` 审计计数。被限流的错误 JSON、60 秒 retry_after 和审计动作保持不变。
- `hub/test_egress_check_service.py` 12 项隔离测试验证默认 ask、规则 allow/ask 优先于任务点名、成员自写任务不能自授权、畸形 URL 审计不泄露查询 token、缺失任务、external 限流、内部成员豁免、审计失败、两个独立 HubContext 竞争一个许可名额。已接入 CI。
- 不新增 MCP 工具或 SQLite 表、不改变出口 allow/ask 决策策略；CLI/Hub 现有安装接口保持不变，真实 Mac 不部署。

### 独立审查决议跨进程互斥（2026-11-23）

- 将 `review_file` 的待审批检查、证据留档、房间 decision 消息与 reviewer 审计迁至 `hub/services/reviews.py:record_review`；原 MCP 工具名称、参数、描述、dispatcher 鉴权和返回结构保持不变。
- 原实现的 `HubContext.lock` 只覆盖单进程，两个独立 Hub 进程可能在同一待审批任务上同时读取到 pending 并分别提交 allow/deny。现于读取任务/审批状态之前执行 SQLite `BEGIN IMMEDIATE`，使检查与写入在跨进程写事务中串行化；第二个提交方读取到首个决议后按原错误语义拒绝。
- 证据、decision 消息与审计仍在一个事务；任一写入失败显式 rollback。URL 审计仍去除 query/fragment，原始目标 URL 仍只存 review_log。
- 新增 `hub/test_reviews_service.py` 7 项隔离测试，覆盖 allow/deny 结果、证据留存与 URL 脱敏、无任务/无审批/已决议拒绝、输入校验、审计失败回滚，以及两个独立 HubContext 并发 allow/deny 仅一个成功。加入 CI 与安装包 smoke。
- 不新增 MCP 工具、SQLite 表或索引；不改变 75-tool / 24-table 契约，不部署真实 Mac。

### Memory 只读服务迁移（2026-11-23）

- 将 `memory_read` 与 `memory_versions` 的查询主体迁入 `hub/services/memory.py`；Hub 入口仍负责 authenticate/require，原 scope/key 校验、目标成员解析、跨成员拒绝审计和 include_text 调度员门禁继续复用既有 helper。
- 保持返回字段、空值形态、版本倒序、before_id 游标、limit 1–100 限制、source_refs JSON 解析和仅调度员可读历史全文等语义不变。读路径不引入新锁或写事务；拒绝审计仍由原 helper 提交。
- 新增 `hub/test_memory_read_service.py` 9 项隔离测试，覆盖当前值/origin、空值、room-shared 目标、调度员代查、跨成员拒绝审计、版本排序/游标/limit、include_text 权限和 scope/key 校验。接入 CI 与安装包 smoke。
- 本次只迁移只读接口；memory_write 与 memory_rollback 的限额、并发、版本写入事务保持原实现，留待单独批次迁移。无 DB schema/MCP contract 变化。

### Memory 写入服务与跨进程主题限额（2026-11-23）

- 将 `memory_write` 的 scope/target/origin/source_refs 校验、分层大小限制、版本追加、当前值更新、软上限告警与审计迁入 `hub/services/memory.py:write_memory`；MCP 层保留身份认证，既有 helper 作为显式依赖注入，避免复制策略。
- 在主题数量 COUNT 前使用 `BEGIN IMMEDIATE`，把「主题数检查 → 版本插入 → 当前值 upsert → 审计」放入一个 SQLite 写事务，补足原 `HubContext.lock` 只能保护单进程的问题；多个 Hub 进程共享同一 DB 时，private 主题硬上限 32 不会被并发穿透。
- 保持 private-core/room-shared UTF-8 字节限制、private 单篇字符限制、主题墓碑、external/self/dispatcher origin 规则、source_refs 结构、soft cap 只警告、错误 JSON 与返回值不变。
- 新增 `hub/test_memory_write_service.py` 11 项隔离测试，覆盖版本与审计元数据、origin 防伪、source_refs/类型校验、字节/字符上限、room-shared 权限、删除墓碑、软上限告警、审计失败回滚及独立 HubContext 并发争用最后一个主题名额。接入 CI。
- 不新增 SQLite 表或 MCP 工具；`memory_rollback` 暂留 Hub 入口，后续单独迁移。

### Memory 回滚服务迁移（2026-11-23）

- 将 `memory_rollback` 的权限判断、目标成员解析、历史版本归属校验、回滚新版本写入与审计迁入 `hub/services/memory.py:rollback_memory`；原 MCP 参数/返回形态不变，调度员专属门禁与 `_mem_denied` 审计口径复用。
- 回滚开始时先取得 `BEGIN IMMEDIATE`，再读取目标版本并追加新版本，避免跨 Hub 进程读到旧状态后与并发写入交错；历史版本不修改、不删除，source_refs 继续记录 `rollback_of`。
- 新增 `hub/test_memory_rollback_service.py` 6 项隔离测试，覆盖 dispatcher 回滚来源、成员拒绝审计、版本不归属、room-shared 目标规则、审计失败回滚和 scope/key 校验。接入 CI 与安装包 smoke。
- Memory 的 read / versions / write / rollback 四个 MCP 接口现已全部迁入服务模块；不新增工具或表，不改变现有记忆格式与限额。

### Skill 注册、指派与版本服务化（2026-11-23）

- 六个 skill MCP 工具（publish/list/read/assign/unassign/rollback）的数据库主体迁入 `hub/services/skills.py`；Hub 层保留 authenticate/require 和原工具签名、说明。
- `skill_publish` 与 `skill_rollback` 在读取 current_version 前执行 `BEGIN IMMEDIATE`，避免多个 Hub 进程共享 DB 时版本号争用 UNIQUE(skill_id, version)；assign/unassign 同样在一个写事务内完成成员状态检查、指派更新与审计。
- 保持正文/简介长度限制、空简介保留旧描述、全量版本留存、成员仅能读取 enabled 指派、成员侧不存在/未指派统一 forbidden、dispatcher 目录 assignees 字段、未指派即 unassign 不造墓碑不审计、移除成员拒绝指派等语义。
- 新增 `hub/test_skills_service.py` 12 项隔离测试，覆盖发布版本与描述、输入限额、dispatcher/member 目录差异、读取授权、assign/unassign 幂等与审计、回滚、审计失败回滚和独立 HubContext 并发版本分配。接入 CI 与安装包 smoke。
- 无新增 MCP 工具或 SQLite 表；现有 75-tool / 24-table 契约不变。

### Desktop 单实例锁服务化（2026-11-23）

- 将 `desktop_acquire/desktop_release/desktop_status` 三个工具迁入 `hub/services/desktop.py`；Hub 层继续认证，原任务持有人/claim_token 检查通过 `_holder` 注入，锁快照复用 `_desktop_snapshot`。
- acquire/release 在读取全局桌面锁状态前使用 `BEGIN IMMEDIATE`，使不同 Hub 进程共享 SQLite 时也只能有一个 GUI 任务拿到锁；保留同任务幂等重入、busy JSON、成员按 claim_token 放锁、dispatcher 强制释放与 idle no-op 行为。
- acquire/release 的状态变更与审计同事务，审计失败回滚锁状态。新增 `hub/test_desktop_service.py` 10 项隔离测试，覆盖角色、claim_token、busy/idempotent、成员释放、dispatcher force release、status、审计回滚和独立 HubContext 并发竞争。接入 CI 与安装包 smoke。
- 任务交活/阻塞/取消/转交/租约回收时使用的 `_desktop_release_for_task` 仍由调用方事务复用，本批不改变这些任务生命周期逻辑。

### 任务模板服务化（2026-11-23）

- 将 `template_upsert/list/delete/fire` 四个工具的数据库主体迁入 `hub/services/templates.py`；Hub 层保留 dispatcher 鉴权与 MCP 工具签名。
- upsert/delete 使用 SQLite 写事务，成员默认指派校验、模板写入/删除与审计原子提交；模板开卡继续调用原 `task_create` 工具路径，不复制任务状态机/审批/出口管制逻辑。`template_fire` 的开卡事务与模板审计仍保持原有分离语义。
- 保持模板字段限制、占位符规则、arg 1000 字限制、override 优先级、移除成员二次校验、缺失成员由 task_create 报错，以及开卡审计失败不回滚已创建任务的既有行为。
- 新增 `hub/test_templates_service.py` 11 项隔离测试，覆盖 CRUD、参数限制、默认成员校验、占位符展开、override、移除成员、task_create 委托和审计失败边界。接入 CI 与安装包 smoke。
- 不新增 MCP 工具或 SQLite 表，保持现有 75-tool / 24-table 契约。

### 成员注册与心跳服务化（2026-11-23）

- 将 `member_register` 与 `heartbeat` 迁入既有 `hub/services/members.py`；Hub 层保留 dispatcher/member 身份门禁，注册所需 token factory、hash、头像与组合分配策略作为显式依赖注入。
- 注册在 `BEGIN IMMEDIATE` 下执行成员名占用检查、全局头像组合检查、成员插入与审计，修复原进程内锁无法保护多 Hub 进程同时分配相同自定义头像组合的问题。token 仍只存 SHA-256，明文只在本次响应返回。
- 保持 removed name 永久保留、avatar_combo 局部覆盖/自动补全、provider 初始 pending、profile/provider 长度限制与原 IntegrityError 映射。heartbeat 仍只更新时间戳、不额外写审计。
- 新增 `hub/test_member_registration_service.py` 9 项隔离测试，覆盖 token hash、provider 状态、自定义头像组合、JSON/字段拒绝、重复组合、名字保留、字段限额、审计失败回滚、跨 HubContext 头像组合竞争和心跳更新时间。接入 CI。
- 不新增 MCP 工具或 SQLite 表；pair redemption HTTP 路由保持原实现，后续单独审计。

### 成员头像更换服务化（2026-11-23）

- 将 `member_avatar_set` 迁入 `hub/services/members.py`；Hub 层保留 dispatcher 鉴权，头像枚举与当前在册组合通过显式参数注入。
- 在检查组合占用之前执行 `BEGIN IMMEDIATE`，跨 Hub 进程保证全员 avatar combo 唯一；允许成员保持自己的当前组合，保留 glyph/渐变色，只更新 species/palette/accessory/bg/combo。
- 新增 `hub/test_member_avatar_service.py` 7 项隔离测试，覆盖换头像、本人组合幂等、JSON/字段校验、重复组合、成员状态、审计失败回滚与独立 HubContext 并发抢占。接入 CI。
- 不新增表或工具，member_register 与 member_avatar_set 共用同一成员服务模块。

### Pair redemption 服务化与跨进程一次性兑换（2026-11-23）

- 将公开 `POST /pair` 路由的兑换业务迁入 `hub/services/pairing.py:redeem_pair`；HTTP 层仍负责解析 JSON 并返回原 `JSONResponse`，不改变 endpoint、状态码或响应字段。
- 在失败滑窗统计之前执行 `BEGIN IMMEDIATE`，把全局失败计数、external 总开关、配对码状态、成员创建/reclaim、已读水位、redeemed 标记、attempt 与审计串成一个写事务。两个 Hub 进程并发兑换同一码只允许一个创建/轮换 token，另一个读取到已兑换状态并走模糊失败。
- 保持无效码统一错误、全局 429、external gate 403、按码锁定、token 只存哈希、审计不记录 code/token、reclaim 只轮换 token/provider 且不改 room/joined_msg/read cursor。
- 新增 `hub/test_pairing_service.py` 8 项隔离测试，覆盖成功建号与游标、缺失/未知/重复码、external gate/全局限流、reclaim、内部名字冲突、过期/吊销/锁定、审计失败回滚及独立 HubContext 并发兑换。接入 CI 与安装包 smoke。
- 配对码的创建/查询/吊销 MCP 工具仍留在 Hub 入口，下一步继续迁入同一 pairing 服务并补充与兑换的并发测试。

### Pairing 管理工具服务化（2026-11-23）

- `pair_code_create/list/revoke` 与 `external_gate` 现迁入 `hub/services/pairing.py`，与公开 `POST /pair` 兑换共用服务模块；MCP 层仍负责 dispatcher 鉴权，工具名称/参数/返回结构不变。
- pair_code_create/revoke 与 external_gate 写操作均使用 `BEGIN IMMEDIATE`，使发码时的名字/房间/reclaim 检查、吊销与兑换之间跨进程串行，减少「已吊销码仍被并发兑换」或「为已占用名字发出多个码」窗口。
- 扩展 `hub/test_pairing_service.py`，覆盖一次性明文/哈希、身份/provider/room/reclaim 校验、gate、状态台账、吊销、审计失败回滚；与 redemption 的跨进程并发测试共同构成 pairing 领域回归。
- pairing 服务覆盖公开兑换和三个管理工具；无新增工具/表，保持原 75-tool / 24-table 契约。

### Run 台账读取服务化（2026-11-23）

- 将 dispatcher-only `run_list` 的只读查询迁入 `hub/services/tasks.py:list_runs`；Hub 入口继续认证和角色校验，原筛选/倒序/limit 参数及返回字段保持不变。
- 新增 `hub/test_runs_service.py` 4 项隔离测试，覆盖全量倒序、成员名 join、task_id 过滤、limit 和空结果；接入 CI/安装包 smoke。
- Hub MCP 工具入口目前不再直接执行数据库查询；`_holder` 是 Desktop 服务注入的共享校验 helper，不是 MCP 工具主体。

### macOS Views.swift 按功能拆分（2026-11-23）

- 将原 `Views.swift`（约 1,618 行）按声明边界拆为 `Components.swift`、`Views.swift`（主窗口/侧栏）、`ChatFeatureViews.swift`、`TaskFeatureViews.swift`、`SettingsFeatureViews.swift`、`TaskDetailViews.swift`。
- 仅移动现有 SwiftUI 声明与 imports；为跨文件复用移除顶层 view/component 的 `private`（仍为 PackApp module 内部可见），不改属性、body、状态流、导航、布局或事件逻辑。
- SwiftPM target 递归包含 `Sources/PackApp` 下的 Swift 文件，不新增 target/dependency；本批以 Swift build/test 与 app bundle 检查作为验收。

### AppState 与 App 入口分离（2026-11-23）

- 将 `@MainActor AppState`（约 1,340 行）从 `PackApp.swift` 移入独立 `AppState.swift`；`PackApp.swift` 仅保留 `RoomItem` 与 `@main PackApp` 启动入口。
- 不改变 `AppState` 属性、方法、actor 隔离、依赖初始化、Keychain、HubClient 调用或状态发布；两个文件沿用原 imports，保持同一 PackApp target。
- 本批仅做声明边界拆分，为后续按 Connection/Rooms/Tasks/Members/Approvals/Settings 提取 Store 或 extension 建立入口；不提前引入新的状态管理框架。

### AppState 成员域扩展拆分（2026-11-23）

- 将成员选择、成员任务/技能加载、头像编辑、登录状态、引擎状态检查/交接、成员注册/移除等约 220 行方法迁入 `AppState+Members.swift` 的 `@MainActor extension AppState`。
- 仅将被扩展访问的 `client` 从 private 调整为 PackApp module 内部可见；不改成员状态属性、异步任务、Keychain、Hub tool 调用和错误处理。
- 不新增 Store、actor 或状态框架；先按领域逐块迁移，待依赖稳定后再评估独立 Store。


### macOS App 网络层与状态域拆分（2026-11-23）

- 新增 `app/Sources/PackApp/Networking/HubTransport.swift`，集中处理 Hub URL 校验、Bearer 请求头、20 秒请求超时、ephemeral URLSession、禁止 Cookie、同源重定向和 HTTP 状态检查。
- `HubClient.swift` 只保留 MCP session 初始化/恢复、JSON-RPC id、SSE data 行解析、tools/call 结果解析和 Session not found 重试；不改变现有协议版本、请求字段或错误分类。
- 新增 `AppState+Rooms.swift`，迁出房间切换、创建/重命名和归档；新增 `AppState+Tasks.swift`，迁出任务创建、批准/拒绝、验收、详情、加急和取消；此前已迁出 `AppState+Members.swift`。
- `AppState.swift` 从约 1,129 行缩至约 897 行。仅将跨文件扩展需要访问的 `roomGeneration`、`decisionRooms`、`act` 和 `createTaskFromDraft` 调整为 PackApp module 内部可见；没有新增 Swift Package target/dependency。
- 不修改 Keychain service/account、`~/.pack` 配置、Hub URL 发现、LaunchAgent、MCP schema、SQLite schema 或真实用户数据。
- CI 证据：Run [#45099614364](https://github.com/cyou1314/Pack/actions/runs/45099614364)（Transport + Rooms）三个 Job 全部通过；Run [#45099953048](https://github.com/cyou1314/Pack/actions/runs/45099953048) 的 Runtime、macOS Build/Unit Tests/App Bundle、Hub 单测及契约快照通过，Default staging regression 截至记录时仍在运行。未将 staging 标记为通过。


### AppState 连接、凭证与 Hub 地址发现拆分（2026-11-23）

- 新增 `AppState+Connection.swift`，迁出地址源 Keychain 状态/保存、手动 Hub URL 保存、原子配置更新、HubClient 重建、私有地址源与公开 fallback URL 发现。
- 保持原有 `HubEndpoint.validatedURL` 校验、HTTPS/loopback 规则、配置临时文件 0600 与原子 replace、Keychain service/account、client generation 防竞态、发现超时和手动 URL 优先级。
- `AppState.swift` 从上一批约 897 行缩至约 786 行。跨文件访问所需的 `ownerClient`、Hub URL 状态、generation 和 discovery interval/timeout 调整为 PackApp module 内部可见；`persistHubURL`、`installHubClients`、`rebuildHubClients` 仍限制在 Connection extension 内部。
- 不新增 Swift Package target/dependency，不改变配置格式、Keychain、LaunchAgent、Hub API、SQLite schema 或用户数据。
- CI 证据：Run [#45100628949](https://github.com/cyou1314/Pack/actions/runs/45100628949) 的 Runtime、Swift Build、Swift Unit Tests 和 App Bundle 已通过；Hub 单测及契约快照通过，Default staging regression 截至记录时仍在运行。未将完整 Run 标记为全绿。


### AppState 通知与未读状态拆分（2026-11-23）

- 新增 `AppState+Notifications.swift`，迁出通知快照处理、通知点击深链、Dock badge、审批/验收/成员离线提醒、未读数查询与延迟标记已读。
- 保留原通知 kind/room/id/member 字段、`notificationDiff` 变化判定、`pack.lastSeen` 持久化键、未读延迟和当前房间 generation 防串房逻辑。
- 因 `refresh()` 与 Room extension 需要调用，`processNotificationSnapshot`、`navigateNotification`、`refreshUnreadCounts`、`scheduleMarkRoomRead` 保持 module-internal；`notificationDiff` 与 `pendingNotification` 也仅在 PackApp module 内可见。
- `AppState.swift` 从约 786 行缩至约 696 行。未改变通知权限、推送时机、Dock badge 计算或用户配置。
- CI 证据：Run [#45101017194](https://github.com/cyou1314/Pack/actions/runs/45101017194) 的 Runtime、Swift Build、Swift Unit Tests、App Bundle、Hub 单测与契约快照已通过；Default staging regression 截至记录时仍在运行。此 run 的前一版曾因跨文件 extension 中 `navigateNotification` 仍为 private 而失败，已改为 module-internal 并触发新 run。


### AppState 审批与 Chat/Composer 拆分（2026-11-23）

- 新增 `AppState+Approvals.swift`，迁出待审批请求识别、批准/拒绝、带明确 action/target 的“总是允许”规则、任务验收/打回；`AppState+Tasks.swift` 继续保留任务创建、详情、加急和取消。
- 新增 `AppState+Chat.swift`，迁出 @成员提示、问答回复、附件上传/读取、消息发送/失败重试/恢复、引用、编辑和删除。
- `AppState.swift` 从 696 行缩至 385 行；新 Chat/Approvals 扩展分别为 297/125 行，任务扩展缩至 104 行。
- 本批只移动既有声明和 imports；所有 `@Published` 状态仍由同一个 `AppState` 托管，不新增 Store、Swift Package target 或依赖。仅将附件加载去重状态 `attachmentLoading` 从 private 调整为 PackApp module 内部可见，以供 Chat extension 使用。
- 保持 Hub MCP tool 名称/参数、审批范围校验、决议消息文本、任务验收语义、ReplyCodec、附件数量/大小限制、UserDefaults 键、导航和 UI 行为不变。
- 本批验收以 macOS Swift Build/Unit Tests/App Bundle CI 为准；真实 Mac 交互与 Keychain/LaunchAgent 验收仍未开始。


### AppState 启动与刷新生命周期拆分（2026-11-23）

- 新增 `AppState+Lifecycle.swift`，迁出 `start()`、`refresh()` 与任务分页读取 helper；AppState 主文件不再直接承载轮询和整轮 Hub 快照刷新流程。
- 保持启动时配置加载、owner client 初始化、地址发现、刷新间隔下限、room/client generation 防串数据、任务分页上限、失败重试、通知快照、未读统计和延迟已读行为不变。
- `pollTask`、`unreadTask`、`consecutiveRefreshFailures`、`taskListPageSize`、`taskListMaxPages` 仅从 private 调整为 PackApp module 内部可见，供同一 AppState 的 Lifecycle extension 访问；未暴露 public API。
- 不新增 Store、actor、Swift Package target 或依赖；不修改 Hub API、MCP schema、SQLite schema、Keychain、配置路径和用户数据。
- 本批以 Swift Build、Swift Unit Tests 和 App Bundle CI 验收；真实 Mac 启动/登录态与 LaunchAgent 验收仍后置。


### Skill / task-template Catalog 动作拆分（2026-11-23）

- 新增 `AppState+Catalog.swift`，迁出技能/模板目录加载与模板创建任务动作；`AppState.swift` 继续持有 `@Published` catalog UI 状态。
- 保持原 HubClient tool 名称、参数、结果字段、错误提示、模板创建后关闭目录和清空参数的行为不变。
- 不新增 Store、依赖、Swift Package target 或数据库/MCP 变更；Catalog extension 仍运行在同一个 `@MainActor AppState` 上。
- Settings 相关状态属性与其他跨域状态暂留 AppState；只有明确的所有权和测试收益出现后再拆独立 Store。


### Codex App Server 审批响应可靠性（2026-11-23）

- `respondToApproval` 先向 App Server 写入明确的 accept/decline/cancel 决议，只有写入成功后才清除本地 pending 状态和超时器。
- 若进程 stdin 已关闭或响应写入失败，审批仍保留为 pending，超时器继续生效，避免 UI 显示审批已结束但 App Server 实际未收到决议。
- 新增回归测试：模拟审批请求到达后 stdin 关闭，验证响应失败不会提前删除 pending approval；随后断开连接可正常清理状态。
- 不改变 Codex 协议方法、决议枚举、Pack Agent Event schema 或 MCP/SQLite 契约。


### Agent Core 生产集成审计（2026-11-23）

**当前状态：M0 合约骨架已存在；生产 Runtime 集成尚未完成。**

- `runtime/agent-core` 已有 Descriptor、Registry、Event Envelope、敏感字段检查、Context Package 和 Codex App Server Adapter；当前覆盖以 Node mock/process 测试为主。
- 当前 `runtime/runtime.mjs` 仍是 Muse ego 的 v1 轮询/任务执行入口，未装配 `AgentAdapterRegistry` 或 Codex App Server Adapter。`registerCodexAppServerAdapter` 的现有调用只在 Runtime 测试中出现；不能将“Adapter 单测通过”描述为 Pack 已能在产品里添加/运行 Codex Agent。
- `subscribe()` 事件目前只在 Adapter 进程内回调，没有持久化 Inbox/Outbox、重放游标、Hub Event Gateway 或 App Activity/Approval 闭环。
- Context Package 当前有白名单与隔离单测，但还没有由 Hub 授权服务生成并交给生产 Runtime 的端到端路径。
- Codex 审批目前可转换为 `approval.requested` 并由 Adapter 写回决定；尚未连接 Hub 持久化审批、SwiftUI 人工确认与审计记录。不得在未接入统一 Permission/Approval Service 前启用真实高风险工具。
- 当前无真实 Mac Codex App Server 登录/恢复验收；mock process 仅证明协议适配器局部行为。

**下一步严格按 M1 → M2 执行：**

1. 先完成 Agent Registry 的 additive schema/migration、legacy member mapping 与 shadow read（见 `AGENT-REGISTRY-MIGRATION-DESIGN.md`）；不切换旧 members 读写。
2. 再实现 Runtime Supervisor、Session Manager、可靠 Event Inbox/Outbox 和 Event Gateway；先接 mock adapter 做幂等、顺序、取消、超时、断线重放测试。
3. 将 Codex Adapter 注册到 Supervisor，而不是让 Hub MCP handler 直接启动 Provider 进程；连接/断开、Session 生命周期和事件持久化必须由 Supervisor 管理。
4. 在统一 Permission/Approval Service 与 SwiftUI 审批闭环验收前，Codex command/file approval 默认不自动接受，真实工具执行保持关闭或显式人工 gate。
5. 完成以上后才做 Codex 本机登录、真实 Thread/Turn、审批和恢复验收；不触碰真实 Mac 用户数据或部署。


### Agent Registry M1a 最新状态（2026-11-23）

- M1a 已在独立候选分支实现：版本化迁移 ledger/checksum、事务化增量 DDL、members → agents 稳定映射、逐连接 FK enforcement、迁移回滚测试与 schema snapshots。
- 当前尚待该分支 CI 与 Codex 审查；未合入 Foundation，未在用户 Mac/生产数据库运行。
- M1b shadow read 与并发初始化专项验证尚未开始；旧 members 仍是唯一读写事实来源。
