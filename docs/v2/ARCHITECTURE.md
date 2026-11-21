# Pack v2 技术架构

## 1. 总体原则

Pack v2 使用控制平面与执行平面分离的架构。

- Hub：协作状态、身份、房间、任务、审批与审计的事实来源。
- Mac Local Runtime：本机 Agent 连接、浏览器与桌面执行的统一入口。
- Provider Adapter：屏蔽不同 AI 的协议差异，不替代其原生执行引擎。
- SwiftUI App：交互、状态展示、审批和人工接管。
- Event Gateway：可靠分发消息、运行事件和执行命令。

## 2. 逻辑架构

    SwiftUI Mac App
      ├── Rooms / Agents / Tasks / Workflows
      ├── Approvals / Activity / Computer
      └── Local Runtime Supervisor
            ├── Adapter Registry
            ├── Session Manager
            ├── Codex App Server Adapter
            ├── Gemini CLI Adapter
            ├── OpenAI API Adapter
            ├── Doubao Ark Adapter
            ├── Muse Connector Adapter
            ├── Browser Controller
            └── Desktop Controller

    Pack Hub
      ├── Identity & Membership
      ├── Rooms & Messages
      ├── Agent / Session Metadata
      ├── Task & Workflow Orchestration
      ├── Permission & Approval
      ├── Shared Context & Artifacts
      ├── Event Inbox / Outbox
      └── Audit

## 3. 运行边界

### Hub

继续保留 FastMCP + SQLite WAL 的现有部署方式，首轮不迁移数据库引擎。

Hub 负责持久化 Agent 和 Provider 的非敏感元数据、Connection 凭证引用、Session 外部 ID、Room、Message、Task、Workflow、规范化事件索引、Permission、Approval、Audit、Room Shared Memory 和 Artifact 引用。

Hub 不保存 API 明文密钥、浏览器 Cookie、原生个人记忆或本机私密配置。

### Mac Local Runtime

使用 launchd/supervisor 管理，主动连接 Hub。

负责读取 Keychain 凭证、启动 Provider Adapter、连接本机 Codex App Server、连接 Gemini CLI、发起官方 API 请求、执行浏览器和桌面动作、本地缓存事件游标与未完成 Run、断线重连与进程恢复。

Hub 不直接访问本机回环端口，也不向公网暴露 Mac 入站端口。

### Provider Adapter

每个 Adapter 只处理一个明确的 Provider/连接模式组合。

负责连接和健康检查、创建/恢复 Session（能力支持时）、发送消息、中断运行、将 Provider 原始事件映射为 Pack Event、将权限/审批请求转换为 Provider 支持的交互。

Adapter 不负责自行创建 Pack 房间任务、绕过 Hub 权限、跨房间读取上下文、保存其他 Provider 私人记忆或绕过审批执行高风险操作。

## 4. 事件流

1. 用户在 App 发送消息。
2. Hub 持久化用户消息并创建 Agent Run 请求。
3. Orchestrator 选定 Agent 和目标 Session。
4. Local Runtime 获取请求与授权 Context Package。
5. Adapter 向 Provider 发送请求。
6. Provider 原始事件进入 Adapter。
7. Adapter 转换为统一 Agent Event。
8. Runtime 将事件幂等写回 Hub。
9. App 订阅或增量读取事件并更新 UI。
10. Run 结束后保存最终消息、产物引用和用量。

事件至少包含 event_id、provider_id、agent_id、room_id（适用时）、session_id、run_id、sequence、event_type、occurred_at 和 payload。

## 5. 可靠性

- Outbox/Inbox 采用唯一事件 ID 和幂等键。
- 每个 Session 维护 Provider 游标或本地同步游标。
- 事件顺序按 Session 或 Run 维护，不假设跨 Provider 存在全局顺序。
- 断线重连后先补拉未确认事件，再恢复新事件订阅。
- 重复事件不得重复创建消息或执行工具。
- Provider 不支持恢复时，显式创建新 Session 并保留旧 Session 状态。
- Run 状态必须可中断、可超时、可人工接管。

首轮可以先使用现有 MCP 请求和增量同步作为兼容通道；实时推送应通过独立 Event Gateway 逐步引入，不一次性替换现有 Hub API。

## 6. 数据模型演进

现有 members 保留为兼容层。新增 Agent Registry 后，逐步映射：

- members → agents 的身份/权限兼容视图。
- provider / kind → provider_id / connection_mode。
- 现有 member token → 受限连接凭证。
- 现有 tasks / runs → workflow 执行节点的基础记录。
- rooms / messages → v2 房间与消息主模型。

新增实体建议：providers、agents、agent_connections、agent_sessions、agent_events、room_agents、workflows、workflow_steps、task_dependencies、tool_calls、permission_grants、context_references、artifacts。

具体字段与迁移必须先基于现有 SQLite schema 和历史测试确定；不得臆造已存在字段。

## 7. 安全边界

- API Key 和 OAuth Refresh Token 由 macOS Keychain 保存。
- Hub 只保存凭证引用、权限范围和连接状态。
- 每个 Agent 使用独立身份与 Session。
- 房间消息、外部网页和其他 Agent 输出均是不可信输入。
- Context Package 采用显式白名单。
- 私人记忆不自动进入 Room Context。
- 跨房间引用必须由用户授权。
- Browser/Desktop Controller 只接受通过 Hub 权限检查的任务。
- 高风险操作每次审批。
- 所有操作记录 actor、agent、session、run、task 和目标资源。

## 8. 迁移策略

- 不重写 Hub 任务状态机。
- 不删除现有 external Agent 安全隔离。
- 不替换现有 Muse Runtime，先作为 LegacyMuseAdapter。
- 新 Adapter 先独立测试，再加入 Registry。
- 新数据表先增量创建，双读验证后才切换读路径。
- App 新页面按功能切片增加，旧页面保留回退入口。
- 只有端到端验收通过后才移除旧路径。

## 9. 当前代码落点

- Hub：hub/hub.py
- Mac App：app/Sources/PackApp
- Muse Runtime：runtime/runtime.mjs
- 外部 Agent：runtime/pack-agent.mjs
- 现有共享契约：runtime/pack-contracts.mjs
- 新 Agent Core：runtime/agent-core
- Runtime 测试：runtime/tests
- CI：.github/workflows/ci.yml