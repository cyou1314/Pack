# Pack Phase 4：模块边界与渐进拆分方案

> 状态：执行中；policy/schema/storage 已提取，MCP 与 SQLite 契约快照已固定；HubContext 和领域迁移待完成。  
> 原则：先冻结协议与数据契约，再按领域提取模块；不做大爆炸式重写。

## 1. 当前代码边界

以下行数来自本次整改分支的仓库快照，仅用于估算拆分成本：

| 区域 | 当前文件 | 规模 | 现状 |
|---|---|---:|---|
| Hub 服务端 | `hub/hub.py` | 约 5,221 行 | 配置、DB、鉴权、审计和 75 个 MCP tool 注册仍集中在服务入口/闭包；纯 policy/schema/storage 已开始外提 |
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

Phase 4 已进入执行阶段。当前完成了纯策略、基础 SQLite 结构/访问层和契约冻结样板；尚未迁移 MCP 领域工具，也未改变 SQLite 的运行语义。

### 已落地的模块边界

- `hub/core/policy.py`：纯策略函数，包括 action tag、access mode、退避、executor pinning、egress host/domain 规则和规则优先级选择。
- `hub/core/schema.py`：集中存放原 `hub.py` 中的 SQLite 基础 `SCHEMA` 常量；`hub.py` 通过兼容导入继续调用。
- `hub/core/storage.py`：集中存放 SQLite 连接配置和 audit insert 原语；`hub.py` 保留 `db()` / `audit()` 兼容入口，事务提交仍由调用方负责。
- `hub/test_policy.py`、`hub/test_storage.py`：覆盖纯策略、egress rule adapter、WAL/busy_timeout 和 audit 事务边界。
- `hub/export_contracts.py`：从实际 FastMCP 注册表和临时 SQLite 初始化数据库生成契约，不从手写清单推测工具接口。
- `hub/contracts/`：已提交 MCP 工具和 SQLite schema/迁移基线；更新规则见同目录 README。

### 契约快照基线

- MCP：当前实际注册 **75 个工具**，快照包括名称、描述、输入 JSON Schema 和输出 JSON Schema。
- SQLite：fresh init 与 legacy fixture upgrade 均得到 **24 张表**；快照覆盖列、索引、外键和触发器。
- Legacy fixture 验证既有 member/token hash、room、message、task 和 task transfer 数据保留；旧 transfer 状态会按当前迁移逻辑回填为 `accepted`。
- `python hub/export_contracts.py --check` 已接入 CI；CI 只检查，不自动改写快照。
- 快照导出使用临时目录复制 `hub.py` 与 `hub/core/`，不会接触开发机或生产 Hub 数据库。

### 兼容与安全修复

- `run-all-suites.sh` 在每个隔离 staging 副本中复制整个 `hub/core/` 包；历史 staging 源目录保持不变。
- 恢复 `hub/publish-url.sh` 的 Git 可执行权限。
- 将 URL 校验内置回单文件 `runtime/pack-agent.mjs`，保持现有外部 Agent 单文件安装方式，并增加独立安装回归测试。
- 两条对应的 P1 代码审查意见已回复并解决。

### 自动化验证

- Run #313：Hub 单测、17 组默认 staging、Runtime、Swift build/test、App Bundle 检查全部通过。
- Run #320：Hub 单元测试、MCP/SQLite 契约快照检查、Runtime 测试和 macOS App 构建/测试均通过；该轮默认 staging 回归当时仍在运行。
- 用户 Mac 的 LaunchAgent、Keychain、DMG、真实账号登录态和数据恢复验收尚未开始，继续遵守“仓库整改完成后再跑本机”的顺序。

### 下一步（未完成）

1. 建立显式 `HubContext`，统一 DB 路径/连接工厂、clock、lock、attachment root 和 audit 依赖；先做可注入测试，不移动领域逻辑。
2. 逐步让认证、actor 身份和审计通过 context 传递，移除隐式全局依赖。
3. 按 members/rooms、tasks/approvals/rules、attachments/memory/skills、desktop/runtime 顺序逐域迁移，每次保持 tool schema 快照无意外差异。
4. 完成 Hub 领域层后，再拆 Swift Core、Transport、Store 与 Feature UI。
