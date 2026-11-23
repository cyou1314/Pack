# PiBot → Pack 技术评估

评估日期：2026-11-23
评估基线：w01fb0ss/PiBot main @ 008198f0b650b89e0bca7f4df8d4f640bd6c4e9b
结论：值得做小规模接入验证；不建议整体引入 PiBot。

## 1. 项目判断

PiBot 是本地优先的桌面 Agent 平台，包含多 Bot、独立模型/角色/记忆/技能、MCP、权限审批、命令隔离、群组协作和 ACP 外部 Agent 接入。当前 package version 为 0.0.3，Apache-2.0，技术栈为 TypeScript monorepo、Electron、Node 22+、pnpm 12。

Pack 当前是 SwiftUI/macOS App + Python/SQLite Hub + Node runtime。PiBot 不是轻量 SDK；整体引入会带来 Electron、Node monorepo、依赖树和生命周期管理冲突。因此不 vendoring、不替换 Pack 的 App/Hub。

## 2. 最有价值的接入路径：PiBot 作为 Pack external member

第一轮优先复用 Pack 已有 MCP 外部成员通道，不需要新增 ACP 协议，也不需要修改 Hub 业务代码。

依据：
- PiBot MCP 客户端支持 Streamable HTTP，并支持自定义 HTTP headers： https://github.com/w01fb0ss/PiBot/blob/main/packages/agent/src/mcp/http-transport.ts
- PiBot Connections 页面支持添加 MCP URL、配置密钥，并将访问令牌加密保存在本机： https://github.com/w01fb0ss/PiBot/blob/main/docs/guide/zh/08-connections.md
- Pack Hub 已有 MCP HTTP 入口、external member 配对、Bearer token、inbox 和任务工具。
- Pack 当前 PAIR_PROVIDERS 包含 generic；external tool whitelist 已包含 inbox_pull、inbox_mark_read、inbox_send、task_list、task_claim、task_update、task_complete、task_block、run_start、run_finish、egress_check 等工具。

### POC 步骤

1. Pack 调度员调用 pair_code_create(member_name="pibot-main", provider="generic", room="pack-main", display_name="PiBot")。
2. 通过 Pack 的 /pair 兑换配对码，取得一次性显示的 external Bearer token。
3. 在 PiBot Desktop 的 Connections → Add → MCP 中添加 Pack Hub URL。本机默认示例为 http://127.0.0.1:8794/mcp；HTTP header 设置为 Authorization: Bearer <token>。
4. 先手动触发 PiBot Bot：调用 inbox_pull，处理分配任务，再通过 task_claim、task_update、inbox_send、task_complete 回报。
5. 在 Pack Hub 检查任务归属、审计记录、external 工具白名单和 egress_check；再测试 external gate 关闭与 token 撤销。

首轮限制：这是 PiBot 主动拉取 Pack inbox，不是 Pack 主动推送。先验证手动触发；定时轮询/后台例行任务必须另行验收。

## 3. ACP：后续可选方向

PiBot Desktop 提供 ACP attach 模式，可让外部客户端连接到已经运行的 PiBot Desktop owner，复用其 Bot、记忆、工具和权限，不另开数据库或读取密钥库。参考：
- https://github.com/w01fb0ss/PiBot/blob/main/apps/desktop/src/main/acp-entry.ts
- https://github.com/w01fb0ss/PiBot/blob/main/apps/cli/README.md
- https://github.com/w01fb0ss/PiBot/blob/main/docs/guide/en/16-editor-acp.md

如果未来 Pack 要从自己的 UI 主动启动 PiBot session、发送任务并接收结果，可以在 Node runtime 增加 ACP client adapter。此项需要新增协议适配与会话/审批映射，不是当前 Hub 整改的前置条件。

## 4. 它不能解决 ChatGPT / 豆包原生账号上下文

PiBot 的 Sign in with ChatGPT / ChatGPT plan 实现把登录令牌用于 OpenAI API 的 /v1/responses 请求，资源为 https://api.openai.com/v1。这是订阅额度/API 调用模式，不是驱动 ChatGPT App/Web UI。当前代码没有发现同步 ChatGPT 产品侧个人记忆、自定义 GPT、应用内历史对话或工具配置的实现。

参考： https://github.com/w01fb0ss/PiBot/blob/main/packages/agent/src/auth/chatgpt-login.ts

当前 PiBot 仓库也没有发现原生 Doubao 账号登录或豆包 App 会话适配。兼容 API 配置不等于豆包账号登录，也不包含豆包 App 内的个人记忆和工具上下文。

因此 Pack 必须继续把“原生 App/账号会话接入”和“官方 API/OAuth 接入”分成两条独立通道。PiBot 的 ChatGPT plan 登录只能归入 API 通道。

## 5. 安全要求

- 生产接入使用 PiBot Desktop 的正常 Connections UI，让 PiBot 的加密凭据存储保存 external token。
- 不使用 PiBot CLI 的 mcp --data 或 acp --data 作为生产方案；其文档将 headless data-dir 模式标为开发用途，并说明凭据未加密。
- Pack 只签发 provider="generic" 的 external member token，绑定单一成员和房间；绝不使用 dispatcher token。
- 保留 Pack 的 external tool whitelist、任务审批、审计、external gate 和 egress policy。
- Hub 默认只监听 127.0.0.1。跨设备连接时必须另配 TLS、网络访问控制和 token 轮换，不要把未加密本地 MCP 端口直接暴露到公网。

## 6. 最终建议

| 方向 | 决策 |
|---|---|
| 整体引入 PiBot | 不做：技术栈和产品边界冲突 |
| PiBot 作为 Pack external member（MCP） | 建议做低风险 POC：复用现有 Hub 配对与白名单 |
| Pack 增加 ACP client | 后续评估：适合从 Pack UI 主动驱动 PiBot |
| 复制 PiBot policy/execution 代码 | 暂不做：先借鉴边界设计 |
| 用 PiBot ChatGPT plan 登录满足原生记忆需求 | 不采用：它是 API 调用，不是 ChatGPT App 上下文同步 |
| 用 PiBot 直接接入豆包账号 | 不采用：当前没有原生豆包会话实现 |

推荐顺序：先完成 Pack Hub 与 Mac 本机验收；之后用 generic external member + PiBot MCP 做一次低风险端到端 POC。确认任务拉取、执行、回报和审计完整后，再决定是否开发专门的 Pack Hub skill 或 ACP adapter。
