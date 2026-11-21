# Pack v2 产品需求规格（PRD）

- 版本：2.0
- 状态：实施基线草案
- 日期：2026-11-21
- 仓库基线：main / 3c11de6
- 平台：macOS 优先

## 1. 产品定义

Pack 是专业级 Mac AI 工作台。它将用户已有的个人 AI 助手、本机 Agent 和官方 API Agent 统一组织为可管理、可交流、可协作的成员。

Pack 提供统一入口和协作治理，但不替代各 Provider 的原生产品、记忆系统或执行引擎。

## 2. 核心用户任务

1. 添加、管理和移除不同 Provider 的 Agent。
2. 与单个 Agent 私聊，或在同一房间中与多个 Agent 群聊。
3. @ 指定 Agent 进行讨论或执行。
4. 将讨论显式转为任务，或由 Orchestrator 生成可审核计划。
5. 让 Agent 分工、并行执行、相互审核和交接。
6. 查看执行状态、工具调用、产物、费用和错误。
7. 在授权范围内控制浏览器和 Mac 原生应用。
8. 随时暂停、取消、接管和回滚可回滚的操作。

## 3. 产品边界

### Pack 负责

- Provider 与 Agent 注册。
- 连接配置和凭证引用。
- Agent Session 元数据。
- 房间、消息和共享上下文。
- 事件规范化与实时分发。
- 任务、工作流和依赖关系。
- 工具权限、审批和审计。
- Mac 本地执行协调。
- 统一 UI、通知和用量展示。

### Provider 负责

- 原生账号身份和登录状态。
- 原生私人记忆与历史会话。
- Provider 自己的模型推理和工具。
- Provider 账号额度与策略。
- 原生 Agent 的执行环境。

### 明确不做

- 不通过抓取 Cookie、绕过登录或逆向私有接口伪装原生集成。
- 不将 API Agent 宣称为个人 App Agent。
- 不把不同账号的私人记忆自动汇总进房间。
- 不让云端 Hub 直接控制 Mac 鼠标键盘。
- 不默认允许 Agent 无限循环或无限预算执行。
- 不在首轮改造中删除现有 v1 数据与任务协议。

## 4. Agent 接入模式

### Native Account

连接用户已有个人助手。只有 Provider 提供受支持的连接方式时才能启用。

能力必须逐项验证：发送消息、接收消息、主动唤醒、恢复会话、附件、工具、原生记忆可用性。

### Local Runtime

连接本机运行的 Agent，例如 Codex App Server 或 Gemini CLI。凭证和会话留在本机，Hub 只存必要元数据与事件。

### Official API

通过官方 API 创建独立 Agent 实例。凭证保存在 macOS Keychain，API 请求从本机运行时发出。API Agent 的记忆和会话策略由 Pack/API 明确管理，不继承个人 App 记忆。

### MCP Connector / External Agent

允许外部 Agent 通过受限 MCP 工具加入房间。保留现有配对、房间绑定、工具白名单和入群历史截断。连接器不自动等于完整双向实时 Agent。

### Browser Bridge

只作为经过用户明确授权的兼容方案。必须标记为受限桥接，不得伪装成官方原生接入。

## 5. 群聊

- 一个房间可以包含多个 Provider 和多个 Agent 实例。
- 消息作者身份由服务端绑定，不接受客户端任意伪造。
- 支持普通消息、@ 指定 Agent、任务、进度、交接、工具事件、审批和报告。
- @ Agent 默认表示请求回应，不自动等同于创建正式任务。
- 用户可以将消息转为任务。
- Agent 回复支持流式增量和最终消息。
- 多 Agent 并行回应时，UI 必须区分各自 Session/Run。
- Agent 只能读取被授权的房间上下文和附件。
- 房间内容是不可信数据，不自动成为系统指令。

## 6. Orchestration

执行模式：
- Manual：用户指定 Agent。
- Assisted：系统建议分工，用户确认。
- Automatic：在用户授权预算和策略内自动执行。
- Team Review：指定多个 Agent 独立审查。

必须支持任务拆解、依赖关系、并行执行、交接、独立审核、失败分类、重试预算、超时、中止和最终汇总。

默认模式为 Assisted。自动模式必须有最大深度、最大任务数、最大调用预算和超时限制。

## 7. 记忆与上下文

- Native Memory：由 Provider 自己管理，不复制到 Hub。
- Pack Agent Memory：Pack 显式保存的 Agent 偏好与工作状态。
- Room Shared Memory：房间内明确共享的摘要、决策和任务上下文。

默认只向 Agent 发送用户本次指令和必要的房间共享上下文。私人记忆不因加入房间而共享。

## 8. Mac 控制

浏览器与桌面控制由独立 Mac Local Runtime 执行。

第一批目标：浏览器连接、标签页、导航、页面读取、点击、输入、上传/下载和截图。

第二批目标：屏幕截图、窗口识别、应用切换、键盘、鼠标和拖拽。

桌面控制必须使用互斥锁。付款、删除、发布、对外发送等高风险动作必须逐次审批。

## 9. 核心 UI

- Rooms：群聊与项目讨论。
- Agents：Provider、Agent、连接和能力。
- Tasks：任务状态、认领、依赖和交付物。
- Workflows：多 Agent 执行计划。
- Computer：浏览器与桌面执行状态。
- Approvals：审批与权限。
- Activity：运行事件和审计。
- Settings：Provider、凭证引用、网络和偏好。

保持原生 macOS 设计，主界面简洁；高级配置放在详情页。

## 10. 非功能要求

- 数据库迁移向前兼容、可回滚。
- 事件具备唯一 ID、顺序号和幂等处理。
- 网络断开后可以恢复同步。
- Agent 断开不丢失任务状态。
- API Key 不进入 Hub、仓库、日志和事件 payload。
- 所有工具调用有 Agent、Session、Run、Task 归属。
- 多 Agent 并行不争抢同一桌面。
- 测试覆盖 Adapter 合约、Hub 集成、权限和端到端场景。

## 11. 首个可用版本验收场景

1. 用户在 Mac App 添加 Codex 本机 Agent 和 Gemini 本机 Agent。
2. 两个 Agent 出现在同一房间。
3. 用户 @ Codex 让其检查仓库，Codex 流式回复。
4. 用户 @ Gemini 审核 Codex 的结论，Gemini 获得被授权的上下文。
5. 用户将建议转为任务，Codex 执行，Gemini 审核。
6. 断网后恢复，消息和任务不重复、不丢失。
7. 私人记忆与 API 凭证没有进入共享房间。
8. 高风险工具调用被审批拦截。

Muse、ChatGPT 个人账号和豆包个人助手的原生接入必须通过 Provider 能力矩阵中的实测后，才能列入已支持范围。