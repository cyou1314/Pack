# Pack v2 状态与实施基线

更新时间：2026-11-26

## 1. 产品目标

Pack v2 的目标是专业级 macOS AI 工作台，而不是单一模型的聊天壳。

Agent 接入必须保留两种明确模式，添加 Agent 时由用户选择：

1. **原生个人 Agent**：使用用户在对应服务商中的个人账号、登录态、服务商侧记忆与原生工具。Pack 不应声称能够把不同服务商的私有记忆自动合并。
2. **官方 API Agent**：使用用户明确提供的官方 API 凭证、模型和权限配置。API 模式的上下文与记忆由 Pack 明确管理，不能冒充服务商个人账号中的原生记忆。

两种模式共用任务、会话编排、权限、审计和本地工作区，但凭证、上下文来源、工具能力与记忆边界必须分离。

## 2. 当前交付状态（以 GitHub 实际提交为准）

- 主分支基线：8458a500f458779e93b0f800b6cd97c5e70ea411。
- 当前集成分支：codex/group-collaboration-policy。
- 集成 PR：[ #52 ](https://github.com/cyou1314/Pack/pull/52)，仍为 Draft，未合并。
- 当前分支 HEAD：24a910486ac19da3750561799cde71b70a7169a9（本次仅同步状态文档）；最新代码提交：3fe96df42b808c07d338f3203348d29c7c77a1e5。PR 包含 42 次提交、73 个文件，新增约 9,543 行。
- 代码提交 3fe96df 的完整 CI：[运行 45774467891](https://github.com/cyou1314/Pack/actions/runs/45774467891)，Runtime、Hub、Browser/Chromium、macOS App 四个 Job 均通过。后续文档同步提交会触发新的 CI；当前状态以 [PR #52 Actions](https://github.com/cyou1314/Pack/actions) 为准。

**CI 通过不等于真实产品链路验收。** 当前连接的 Mac 设备离线；尚未在用户本机执行构建、安装、数据库迁移、真实账号登录或真实 Provider 请求。不得把 CI 的 mock、fixture、隔离浏览器测试描述为真实账号验收。

## 3. 模块实现状态

| 模块 | 当前实现 | 尚未完成 / 验收边界 |
|---|---|---|
| Agent Profile / Registry | PackCore 已有 UUID 档案、Provider Catalog、模式与凭证引用校验、单进程本地注册表 | 尚未完成完整账号生命周期、跨设备迁移与恢复 |
| Agent 管理 UI | 集成分支新增本地 Agent 账号管理界面；API Key 使用 Keychain insert-only 配置 | 真实签名环境 Keychain、密钥轮换/删除流程仍需实机验收 |
| OpenAI API | 新增文本 API Adapter，使用官方 Responses API | 未验证真实 Key、模型权限、生产限流和计费 |
| Gemini API | 新增 Gemini Developer API 文本 Adapter，支持明确模型和 token usage 读取 | 未验证真实 Key、模型兼容性和真实请求 |
| 火山引擎 API | 新增 Ark Chat Completions 文本 Adapter | 不等于豆包个人账号；模型参数兼容性仍需真实验证 |
| ChatGPT / Gemini / 豆包原生账号 | Catalog、独立账号档案、手动登录开发入口和 Native Adapter 契约已存在 | 登录完成仍标记为未验证；没有完整的 Gemini/豆包页面 Driver；ChatGPT Driver 仍依赖受信任宿主提供真实页面选择器与身份观察器 |
| 原生记忆与工具 | Native Profile 将记忆边界标记为 Provider Managed，不复制网页端 Cookie 或历史消息 | 尚未验证各 Provider 实际账号身份、记忆设置和原生工具能力；不能承诺平台记忆一定生效 |
| 群聊协作 Hub | 增加 Focus / Compare / Team 计划、依赖 DAG、预算、专用执行身份、上下文授权、审核、取消、成果发布和用量账本 | 协作 Schema 默认不创建；生产数据库尚未迁移；真实 Provider 自动执行未启用 |
| macOS 协作工作台 | 已接入协作列表、创建表单、成果查看、审核、取消和发布确认 | 未完成真实 GUI 操作验收、实时状态推送和完整异常恢复验收 |
| Runtime | 新增 Provider-neutral Runner 契约、账号租约、无自动重试与不确定结果隔离；API 有手动单任务宿主 | Runner 尚未接入生产 Runtime 的自动启动与持续调度；Native 真实执行链路未接通 |
| 浏览器开发载荷 | 固定依赖锁、Chromium 候选载荷、搬迁验证、开发版 App 子进程监管 | 当前是开发候选包；manifest 未批准发布，不能视为正式签名发布依赖 |
| Installer / DB Migration | 协作迁移脚本加入安装程序白名单；迁移具备 dry-run、在线备份、SHA-256 manifest、事务回滚和幂等检查 | 安装不会自动启用协作 Schema；真实数据库迁移、备份恢复和 Mac 安装验收尚未执行 |
| Engine | 现有 App / Hub / Engine 架构保留 | 外部 pack-headless-l2 源码与真实产物仍需固定版本；干净克隆完整构建 DMG 尚未验收 |

## 4. 安全与数据边界

### Native Account

- 每个账号使用独立本地 Profile 和浏览器数据目录。
- 登录由用户手动完成；不自动填写密码，不绕过验证码、挑战或风控。
- 不导出 Cookie，不将服务商私有记忆复制到 Pack。
- 新会话可以使用服务商账号自身提供的记忆能力，但当前尚未验证实际记忆状态。
- 页面状态、账号身份、模型和记忆设置必须由真实 Provider Driver 观察并复核；不能只凭域名或用户填写的名称判定已登录。

### Official API

- API Key 只进入 device-only Keychain；注册表只保存 UUID 引用。
- 当前 Adapter 是文本调用，不开放模型工具、附件或多模态执行。
- 每次请求展示准确目标、模型和不含密钥的请求正文，用户明确批准后才读取密钥并发送。
- 不自动切换模型、不做应用层重试；网络结果或任务提交结果不确定时保留账号租约，禁止盲目重发。
- Token usage 是 Provider 返回的报告值，不等于财务账单；当前不估算费用。

### 群聊协作

- 协作数据库 Schema 由用户显式启用；先 dry-run，再备份，再执行事务迁移。
- 专用 Agent 身份使用独立令牌和工具白名单；普通成员权限不能自动继承。
- 上下文按任务步骤授权，不自动读取全房间历史或其他 Agent 的私有记忆。
- Hub 取消不保证已经发出的 Provider 请求停止，也不保证不会产生费用。
- 尚未完成全局侧信道审计和生产级数据隔离前，不得向不可信 Agent 投递敏感内容。

## 5. 当前主要风险与下一步

1. **先完成 Native 身份验证链路。** 建立真实的账号身份登记、模型/记忆状态观察与发送前复核；先完成 ChatGPT，再按同一接口接入 Gemini、豆包。页面选择器和身份观察必须基于真实页面验收，不猜测 DOM。
2. **完成 API 实机验证。** 先以 Gemini API 为重点，再验证 OpenAI 与 Ark；使用用户自己的 Key，在本机验证成功、拒绝、限流、超时、取消和结果不确定路径。测试日志不得包含密钥。
3. **接通生产 Runtime。** 将可信宿主、账号租约、审批、Hub 任务提交和恢复策略接入实际应用运行循环；自动调度必须在手动闭环可靠后再启用。
4. **完成协作安全审查。** 检查全部旧 Hub 读取/写入路径、消息、附件、审计和错误日志，验证普通 Agent 无法旁路专用身份和上下文授权。
5. **完成 Mac 实机验收。** 先备份现有数据，执行只读检查与迁移 dry-run，再验证安装、升级、卸载、重装、数据库备份恢复和 Keychain。未经明确确认，不迁移真实生产数据库。
6. **固定 Engine 依赖。** 纳入或固定 pack-headless-l2 源码版本，生成可审查的 Engine 产物锁，并从干净克隆验证完整 DMG。

当前 PR #52 保持 Draft，不因 CI 全绿直接合并。Foundation M2 另有 PR #43–#51，基于旧分支且与 main 分叉；双方 17 个文件重叠。具体移植顺序见 PACK-V2-MAINLINE-RECONCILIATION.md。应先完成模块审查和 Mac 实机验收，再决定是否拆分、合并或继续补齐。

## 6. 发布验收底线

- 从固定 commit 的干净克隆可构建 App 和 DMG。
- Engine 依赖有可追溯版本，不能依赖开发者家目录中的未固定源码。
- 所有外部 Hub 请求都验证 URL，禁止把凭证转发到未授权主机或重定向目标。
- Native 与 API 凭证、账号、记忆和工具权限相互隔离。
- 任务状态转换具备并发保护；进程有超时、输出/输入上限和可恢复策略。
- 安装、升级、卸载、重装和数据迁移均有自动化测试与 macOS 真机验收记录。
- Provider 真实账号与 API 请求分别验收；CI fixture 不能替代真实平台验收。
- 协作 Schema 默认关闭，只有用户明确启用并完成备份后才允许迁移。
- 不确定的模型调用或 Hub 提交不得自动重试。

## 相关文档

- Engine 选型：ENGINE-OPTIONS.md
- Provider 设计：AGENT-PROVIDERS.md
- 群聊协作实施记录：GROUP-COLLABORATION.md
