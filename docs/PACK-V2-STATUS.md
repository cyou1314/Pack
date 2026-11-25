# Pack v2 状态与实施基线

更新时间：2026-11-25

## 1. 产品目标

Pack v2 的目标是专业级 macOS AI 工作台，而不是单一模型的聊天壳。

Agent 接入必须保留两种明确模式，添加 Agent 时由用户选择：

1. **原生个人 Agent**：使用用户在对应服务商中的个人账号、登录态、服务商侧记忆与原生工具。Pack 不应声称能够把不同服务商的私有记忆自动合并。
2. **官方 API Agent**：使用用户明确提供的官方 API 凭证、模型和权限配置。API 模式的上下文与记忆由 Pack 明确管理，不能冒充服务商个人账号中的原生记忆。

两种模式共用任务、会话编排、权限、审计和本地工作区，但凭证、上下文来源、工具能力与记忆边界必须分离。

## 2. 当前仓库实际状态

| 模块 | 当前实现 | v2 状态 |
|---|---|---|
| macOS App | SwiftUI；Hub 配置、Keychain、成员列表、Muse 登录与会话交接 | 尚未形成多 Provider Agent 管理界面 |
| 原生登录 | `MemberLoginWebView` 目前只注册 Muse；数据存储按成员名隔离 | ChatGPT、豆包等 Provider 尚未接入 |
| 会话交接 | App 将 Muse Cookie 暂存到 device-only Keychain，再经本机 Unix socket 交给 Engine | 协议与外部 Engine 的一致性仍需验收 |
| Hub | 本地 SQLite、MCP 工具、成员、任务、消息、附件、额度与审计 | 已有基础；继续强化状态机和恢复 |
| Engine | DMG 构建从外部 `pack-headless-l2` 目录取 Electron 与 headless 源码 | 源码未纳入 Pack Git 仓库；DMG 已要求经审核的 Engine 产物锁，但实际锁仍待真实产物生成，干净克隆无法独立复现完整 DMG |
| Runtime 目录 | 有独立 Node Runtime、Muse4AI Bridge、L2 handoff 参考实现与测试 | 当前 DMG 构建不打包该目录；不能把它的测试通过等同于已验证外部 Engine |
| API 接入 | 仓库中有 Muse4AI Bridge 相关代码 | 尚无统一的官方 API Provider 注册、凭证和任务适配层 |
| 安装器 | Hub、Engine、App 的 macOS 安装包；支持卸载保留数据与受验证的安全重装 | 完整跨 Mac 凭证迁移和恢复尚未完成 |

## 3. 目标架构

### App 层

- Agent Registry：统一管理 Agent Profile、Provider、模式、模型、账号、能力和状态。
- Provider Catalog：声明登录入口、域名范围、支持的认证方式、能力和限制。
- Credential Vault：所有 API Key、原生会话交接票据和 Hub 凭证进入 macOS Keychain。
- Workspace：统一会话、任务、素材、文件、Prompt 和项目上下文。
- Policy UI：由用户明确授权工具、文件、网络、浏览器和本机操作权限。

当前已在 `PackCore` 中落地第一版 `AgentProfile` 类型和边界校验：Native Account 只能引用原生会话、使用 Provider Account 上下文；Official API 只能引用 API Key、使用 Pack Workspace 上下文；两种模式的记忆所有权不能混用。已新增 AgentRegistry、候选 Provider Catalog 和单进程 AgentRegistryStore（独立私有目录、版本化 JSON、原子写入）。尚未接入 App UI、实际 Keychain Vault、Provider 登录或 API 调用。

### Agent Adapter 层

统一接口至少应表达：

- `provider_id`、`agent_id`、`mode`；
- 原生账号 Profile 或 API Credential Reference；
- 模型、上下文窗口和能力声明；
- 工具清单、权限范围和执行环境；
- 记忆来源、读写权限与保留策略；
- 超时、并发、取消和重试策略。

Native Adapter 与 API Adapter 必须是不同实现，不得用 API Key 冒充原生个人账号，也不得假设 API 可以读取服务商网页账号的私人记忆。

- Native Account：每个 Provider 账号使用独立、持久化的 WebKit 数据目录；登录态、历史、个性化记忆和服务商原生工具留在该账号内，不跨 Agent 共享 Cookie，不把网页端私有记忆抓取后伪装成 Pack 记忆。
- Official API：只使用官方数据面 API 和 API 凭证；凭证仅存 Keychain，Profile 只保存引用。上下文和可持久化记忆由 Pack Workspace 管理，工具调用必须显式声明并经过 Pack 权限策略。
- 两种模式可以并存于同一 Provider，但必须创建不同 Profile；用户添加时明确选择模式，不能自动把 Native Profile 静默降级为 API Profile。

### Hub / Runtime / Engine 层

- Hub 负责身份、任务状态机、协作、审计、配置和持久化。
- Runtime 负责 Agent 生命周期、任务执行、超时、重试和恢复。
- Engine 负责隔离的浏览器/桌面自动化及本机 IPC。
- Provider 业务逻辑不得散落在 SwiftUI View 或通用任务状态机中。

## 4. 当前已完成的安全与可靠性整改

- Installer：LaunchAgent 所有权校验、独占写入、失败回滚、卸载保护、验证数据根目录后的安全重装。
- Runtime：有界子进程、执行超时、输入/输出限制、取消和 Hub 瞬时错误重试分类。
- Hub：任务取消/审核/转交/接力的状态比较更新；自动接力 Savepoint；附件哈希校验、原子写入和权限收紧；可搬迁备份清单与完整备份集轮换。
- App：Hub URL 主机白名单、禁止 Hub 请求重定向、迁移凭证使用 device-only Keychain；原生会话交接增加数据上限和过期票据清理。

这些改动仍需通过完整 CI 和 macOS 真机验收；代码提交或单元测试通过不等于端到端部署已验收。

## 5. 下一阶段优先级

1. **锁定 Engine 源码依赖**：将 `pack-headless-l2` 纳入组织仓库并固定 commit，或以受控 submodule/vendor 方式纳入；验证它实现的 IPC 协议与 App/L2 handoff 契约一致。
2. **完成 App / Hub / Engine 端到端验收**：登录、会话交接、任务执行、取消、超时、崩溃恢复、卸载和安全重装。
3. **Provider Registry**：先抽象 Provider Profile、账号身份和隔离存储，再添加 ChatGPT、豆包等原生登录入口。
4. **官方 API 模式**：建立独立 API Adapter、Keychain 凭证管理、模型选择、速率限制、成本记录和权限策略。
5. **统一任务与记忆边界**：为 Native 与 API 定义明确的上下文来源、记忆读写权限和可审计的上下文传递。
6. **完整迁移与恢复**：设计包含数据库、附件、配置和受保护凭证的安全迁移流程；当前 DB+附件备份不是完整迁移包。

## 6. 发布验收底线

- 从固定 commit 的干净克隆可构建 App 和 DMG。
- Engine 依赖有可追溯版本，不能依赖开发者家目录中的未固定源码。
- 所有外部 Hub 请求都验证 URL，禁止把凭证转发到未授权主机或重定向目标。
- Native 与 API 凭证、账号、记忆和工具权限相互隔离。
- 任务状态转换具备并发保护；进程有超时、输出/输入上限和可恢复策略。
- 安装、升级、卸载、重装和数据迁移均有自动化测试与 macOS 真机验收记录。

## 开源 Engine 候选

详见 [ENGINE-OPTIONS.md](ENGINE-OPTIONS.md)。已完成文档初筛，未引入第三方依赖，也未替换现有 Engine。
