# Pack Phase 4：模块边界与渐进拆分方案

> 状态：执行中；policy/schema/storage/context 已提取，HubContext 已贯通服务入口、工具闭包和后台 sweeper；rooms 读服务已迁出首批函数；其他领域模块迁移待完成。  
> 原则：先冻结协议与数据契约，再按领域提取模块；不做大爆炸式重写。

## 1. 当前代码边界

以下行数来自本次整改分支的仓库快照，仅用于估算拆分成本：

| 区域 | 当前文件 | 规模 | 现状 |
|---|---|---:|---|
| Hub 服务端 | `hub/hub.py` | 约 5,200 行 | 配置、DB、鉴权、审计和 75 个 MCP tool 注册仍集中在服务入口/闭包；policy/schema/storage/context 已外提，领域逻辑仍待迁移 |
| App 状态与入口 | `app/Sources/PackApp/PackApp.swift` | 约 1,366 行 | App 启动、AppState 和多个跨域状态操作集中 |
| App 视图 | `app/Sources/PackApp/Views.swift` | 约 1,618 行 | ContentView 与大量功能视图/组件集中 |
| 网络客户端 | `app/Sources/PackApp/HubClient.swift` | 约 362 行 | JSON-RPC 请求、连接、重试/超时等传输职责 |
| 领域模型 | `app/Sources/PackApp/Models.swift` | 约 357 行 | 任务、成员、房间等模型与 App 目标耦合 |
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
