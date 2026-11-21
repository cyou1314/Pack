# Pack

**Professional Mac AI Workspace**  
专业级 Mac AI 工作台

Pack 将不同厂商的个人 AI 助手、本机 Agent 和官方 API Agent 组织在同一个原生 macOS 工作环境中。用户可以在统一群聊中与多个 Agent 讨论、分工、执行、审核和交接，并在明确授权后让 Agent 操作浏览器和 Mac 应用。

Pack 不是把多个聊天窗口简单放在一起。它提供统一的 Agent 身份、会话事件、任务编排、权限审批、执行审计和本地控制平面，同时保留各 Provider 自己的账号、记忆、工具与运行环境。

## 产品原则

- **原生能力优先**：能连接已有个人助手时，优先保留原账号、原生记忆、工具和会话。
- **API 双模式**：不能原生接入或需要独立模型实例时，允许使用官方 API。
- **记忆隔离**：Provider 私人记忆不自动复制到 Pack；房间共享上下文必须明确授权。
- **统一协作**：不同 Agent 使用统一事件与任务协议，但不强制使用同一执行引擎。
- **本机控制**：浏览器和桌面操作由 Mac 本地执行器完成，不放进云端 Hub。
- **权限可审计**：敏感操作必须审批，所有 Agent 执行都能追踪和中止。
- **渐进迁移**：保留已验证的 Hub、任务状态机、审批、审计和 Muse Runtime，按兼容层逐步升级。

## 目标 Agent

| Provider | 个人账号 / 原生接入 | 本机运行时 | 官方 API |
|---|---|---|---|
| Muse | Custom Connector 可行性验证 | 现有 Muse Runtime | 待评估 |
| Codex | 复用本机 Codex 登录与 App Server 路线 | Codex App Server 候选 | 按官方能力评估 |
| Gemini | 个人 App 原生会话接口待验证 | Gemini CLI 候选 | Gemini API |
| ChatGPT | 个人 App 会话接入能力待验证 | — | OpenAI Responses API |
| 豆包 | 个人助手接入能力待验证 | 可选本机桥接待评估 | 火山方舟 |
| Grok | 个人 App 会话接口待验证 | 待评估 | 官方 API 能力待验证 |
| Hark | 个人助手接口待验证 | 待评估 | 待验证 |

表格描述目标接入路线，不代表这些适配器已经实现。当前真实能力以 docs/v2/PROVIDER-MATRIX.md 为准。

## 当前仓库基线

本仓库包含以下已存在的 v1 能力：

- FastMCP + SQLite WAL 的 Hub。
- 原生 SwiftUI macOS App。
- 房间、消息、任务状态机、认领、租约、转交和验收。
- 成员权限、审批规则、数据出口检查和审计。
- 分层成员记忆、房间摘要、附件和技能管理。
- Muse ego 浏览器运行时与 Muse4AI 过渡桥。
- 外部 Agent 配对、房间绑定和 pack-agent.mjs 接入脚本。
- 桌面锁等执行互斥基础设施。

2026-11-20 的 main 提交 3c11de6 将 Hub 工具数量推进到 75。工具数量不是产品成熟度指标；真实能力必须以代码、测试和端到端验收为准。

## Pack v2 改造状态

### 本轮建立的基础

- v2 产品与架构决策文档。
- Provider 能力矩阵和迁移路线。
- 统一 Agent Adapter 契约。
- Agent Adapter 注册表。
- 房间共享上下文包构建器。
- Agent 事件信封与契约测试。
- CI 中的 Runtime 单元测试入口。

### 尚未实现、不得误报为已完成

- Codex App Server Adapter 已有首版实现与 Mock 测试；真实 Mac 登录、审批和长任务验收仍待完成。
- Gemini CLI 正式适配器。
- OpenAI Responses API 适配器。
- 豆包火山方舟适配器。
- Muse 双向主动唤醒与完整会话桥接。
- 统一实时事件网关。
- 多 Agent 自动编排。
- Browser Controller 和完整 Computer Use。
- v2 Mac App 页面与首次添加 Agent 向导。

## 架构方向

    Mac App
      ├── Rooms / Agents / Tasks / Workflows
      ├── Approvals / Activity / Computer
      └── Local Runtime
            ├── Agent Adapter Registry
            ├── Provider Sessions
            ├── Browser Controller
            └── Desktop Controller

    Pack Hub
      ├── Identity / Rooms / Messages
      ├── Agent & Session Metadata
      ├── Orchestration / Tasks
      ├── Permissions / Approvals / Audit
      └── Shared Context / Artifacts

Hub 是协作状态的事实来源；Mac Local Runtime 是本地执行入口；各 Provider Adapter 负责与具体 AI 服务或本机 Agent 通信。云端 Hub 不保存 API 明文密钥，也不代替 Provider 管理私人记忆。

## 文档入口

- [Pack v2 文档索引](docs/v2/README.md)
- [产品需求规格](docs/v2/PRD.md)
- [总体技术架构](docs/v2/ARCHITECTURE.md)
- [Provider 能力矩阵](docs/v2/PROVIDER-MATRIX.md)
- [迁移与实施计划](docs/v2/IMPLEMENTATION-PLAN.md)
- [Agent Adapter 契约](runtime/agent-core)

## 开发与验证

Hub 回归：

    PY=python ./run-all-suites.sh

Runtime 语法检查：

    find runtime -type f \( -name "*.mjs" -o -name "*.js" \) -print0 | while IFS= read -r -d '' f; do node --check "$f"; done

Runtime 单元测试：

    node --test runtime/tests/*.test.mjs

macOS App 构建：

    swift build --package-path app

修改 Hub、Runtime 或 App 时，先在独立分支完成最小改动和针对性测试，再运行相关回归。不得直接修改生产数据库或凭据文件。