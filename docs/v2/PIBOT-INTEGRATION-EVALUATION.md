# PiBot 与 Pack 集成评估

- 评估日期：2026-11-24
- 上游仓库：<https://github.com/w01fb0ss/PiBot>
- 核验基线：`main @ 152425febf89487f6494e002122646fdd3bb041a`
- 上游 package：`0.0.3`，Apache-2.0
- 结论：**值得做 MCP 外部成员 POC；不应整体引入 PiBot，也不能把它当作 ChatGPT / 豆包原生账号上下文的替代品。**

## 1. PiBot 当前定位

PiBot 是本地优先的桌面 Agent 团队应用，使用 TypeScript monorepo、Electron 和 pi agent kernel。当前公开能力包括：

- 多 Bot、独立角色、模型、技能、记忆和权限；
- Bot 群聊、任务分工、交接；
- 本机文件工作区、命令沙箱、浏览器和电脑控制；
- MCP / 应用 / 技能 / 知识库连接；
- 审批、操作审计、定时例行任务；
- ACP editor attach、headless runner、Android app 和可选 relay。

上游运行要求为 Node `^22.12.0 || ^24.0.0 || >=26.0.0`，pnpm `>=12.14.0 <13`。PiBot Desktop 支持 macOS 13+、Windows、Linux；Android 为配套客户端。

主要参考：
- <https://github.com/w01fb0ss/PiBot>
- <https://github.com/w01fb0ss/PiBot/blob/main/docs/guide/zh/08-connections.md>
- <https://github.com/w01fb0ss/PiBot/blob/main/docs/guide/zh/13-privacy-and-security.md>
- <https://github.com/w01fb0ss/PiBot/blob/main/SECURITY.md>

## 2. 与 Pack 的边界

Pack 当前采用 SwiftUI/macOS App + Python/SQLite Hub + Node runtime。PiBot 是一套完整桌面 Agent 平台，不是一个轻量 SDK。

因此：

- 不把 PiBot Electron app vendoring 到 Pack；
- 不替换 Pack Hub、SQLite、权限系统或任务系统；
- 不把 PiBot 的 Agent / memory / tools 数据复制进 Pack 数据库；
- 不为复用 PiBot 而把 Pack 改造成 Node/Electron monorepo。

更合理的方式是让两套系统保持独立，通过 Pack 已有的 MCP external member 边界协作。

## 3. 第一优先级：PiBot 作为 Pack MCP 外部成员

这是当前成本最低、最容易撤销、最能验证价值的接入方式。

### 已具备的两侧能力

**Pack：**

- Hub 提供 MCP HTTP 入口；
- 支持 external member 配对与 Bearer token；
- `provider="generic"` 可用于 PiBot；
- 有成员 / 房间绑定、入群水位、inbox 和任务工具；
- external tool whitelist 限制可调用工具；
- 有任务认领、更新、完成、阻塞、审计、egress policy 和 external gate。

**PiBot：**

- Connections 页面支持添加 MCP URL；
- HTTP MCP transport 支持自定义 headers；
- 访问令牌保存在本机加密存储；
- 可以把连接的 MCP 工具授权给指定 Bot 使用。

参考实现：
- <https://github.com/w01fb0ss/PiBot/blob/main/packages/agent/src/mcp/http-transport.ts>
- <https://github.com/w01fb0ss/PiBot/blob/main/docs/guide/zh/08-connections.md>

### POC 流程

1. Pack 调度员创建 external member，指定 `provider="generic"`、成员名和目标房间。
2. 通过 Pack 的配对流程兑换一次性 Bearer token。
3. 在 PiBot Desktop → Connections → Add MCP 中选择 HTTP，填写 Pack Hub MCP URL。
4. 将 Auth 设为 Bearer，选择或创建一个本机 secret；PiBot 会将它解析为 `Authorization: Bearer <token>` 请求头，不需要把明文 token 写进连接 URL。
5. 只给一个测试 Bot 开启该 MCP 工具权限。
6. 手动触发 Bot，先调用 `inbox_pull` 读取消息；若 `has_more=true`，用上一页最后一条已读取消息的 ID 继续拉取，直到读完本轮要处理的消息。
7. 只对实际读过的消息调用 `inbox_mark_read(up_to_id=<highest-consumed-message-id>)`，不要直接把游标推进到最新消息。随后执行 `task_claim` → `task_update`。
8. 发送回报前再次 `inbox_pull` 检查新消息；读完并标记已读后再调用 `inbox_send`。如果返回 `blocked="new_messages"`，以返回的 `cursor` 作为 `since_id` 再调用 `inbox_pull` 分页读取全部未读消息（`new_messages` 只是预览，不保证覆盖全部），只将实际读过的最高 ID 标记已读后再重试；不要用 `ignore_new` 绕过盲发保护。
9. 在 Pack 侧检查任务归属、审计记录、external whitelist、egress policy 和 token 撤销。

### POC 验收

- PiBot 只能读取绑定房间和入群水位之后的消息；
- 只能认领指派给该 external member 的任务；
- 不能创建任务、伪造 dispatcher 身份或调用白名单外工具；
- 任务状态和消息回报能在 Pack Hub 正确追踪；
- external gate 关闭或 token 撤销后，调用立即失败；
- 所有写入和对外发送遵守 Pack 现有审批与审计规则。

第一轮只验证手动触发和任务闭环，不默认启用后台定时轮询。必须遵守 Pack 的盲发保护：`inbox_pull` 本身不推进 read cursor，只有明确读过的消息才可通过 `inbox_mark_read` 标记；不能为了让 `inbox_send` 成功而跳过未读消息。轮询频率、重试、重复消息、长任务心跳和掉线恢复需要单独验收。

## 4. 第二优先级：ACP attach

PiBot 当前有 ACP attach 入口，可让外部客户端连接到已经运行的 PiBot Desktop owner，复用该 owner 的 Bot、记忆、技能和权限，而不是另开一个数据目录。

当前 Desktop ACP entry 位于：
<https://github.com/w01fb0ss/PiBot/blob/main/apps/desktop/src/main/acp-entry.ts>

如果未来 Pack 需要从自己的 UI 主动启动 PiBot Bot、提交任务并接收结果，可以在 Pack Node runtime 增加 ACP client adapter。

但这会引入额外工程：

- ACP transport 与会话生命周期；
- Pack task / room 与 PiBot session 的映射；
- 工具调用、审批和取消语义；
- 断线重连、超时、幂等与审计关联；
- PiBot Desktop owner 未运行时的失败处理。

生产模式应 attach 到用户正常运行的 PiBot Desktop，不要把 `pibot acp --data` 这类开发用独立数据目录方案当作正式集成方式。ACP 是后续可选能力，不是当前 Hub 整改的前置条件。

## 5. ChatGPT / 豆包原生账号上下文：PiBot 无法替代

这是最重要的边界。

PiBot 的 ChatGPT 登录实现使用 OpenAI OAuth，申请 `chatgpt.tokens.use.direct` 等 scope，资源目标为 `https://api.openai.com/v1`，用于通过 Responses API 调用模型。这是 ChatGPT 订阅授权的 API 通道，不是控制 ChatGPT Desktop/Web UI。

当前没有证据表明 PiBot 能同步或读取：

- ChatGPT App / Web 的原生个人记忆；
- ChatGPT 应用内历史对话；
- 自定义 GPT、项目上下文或应用内工具配置；
- 豆包 App 的原生登录态、历史会话或个人记忆。

PiBot 自己的 Bot memory、skills、tools 和本地上下文可以通过 ACP attach 复用，但这仍然是 PiBot 自己的上下文，不等于 ChatGPT 或豆包 App 的原生上下文。

参考：
- <https://github.com/w01fb0ss/PiBot/blob/main/packages/agent/src/auth/chatgpt-login.ts>
- <https://github.com/w01fb0ss/PiBot/blob/main/apps/desktop/src/main/acp-entry.ts>

因此 Pack v2 仍应保持两条独立接入通道：

1. **原生个人 Agent / 账号会话**：保留各自应用里的记忆、工具和上下文；只有官方支持的会话连接方式才能声称复用。
2. **官方 API / OAuth**：明确展示模型提供方、账号授权、额度和数据流；不能把 API OAuth 包装成 App 原生上下文。

PiBot 的 ChatGPT OAuth 只能归入第二类。

## 6. 安全与部署要求

- 生产 POC 通过 PiBot Desktop Connections UI 配置 MCP，使用 PiBot 本机加密凭据存储；
- Pack 只签发绑定单一成员和房间的 external token，保留 external whitelist、审批、审计和 egress policy；
- 默认只允许 Pack Hub 监听 `127.0.0.1` 的本机连接；
- 跨设备访问必须另加 TLS、网络访问控制、token 轮换与撤销机制；
- 不把未加密的本地 MCP HTTP 端口直接暴露到公网；
- 不使用 PiBot 开发用途的明文 headless data-dir 凭据模式作为生产集成；
- 不复制 PiBot 的私有数据库、token、memory 或工作区文件到 Pack。

## 7. 决策

| 方向 | 决策 |
|---|---|
| 整体引入 PiBot | 不做：与 Pack 产品边界和技术栈重叠 |
| PiBot 作为 Pack external member（MCP） | **建议做小规模 POC**：复用现有 Hub 配对和权限边界 |
| Pack 增加 ACP client | 后续评估：适合从 Pack UI 主动驱动 PiBot Desktop owner |
| 复制 PiBot Agent / memory / policy 代码 | 暂不做：先通过协议边界验证价值 |
| PiBot ChatGPT OAuth 满足 ChatGPT App 原生记忆 | 不采用：它是 API 调用授权 |
| PiBot 直接接入豆包 App 账号 | 不采用：当前未发现原生会话适配 |

推荐顺序：先完成 Pack Hub 与 Mac 本机验收；之后做一次 PiBot MCP external member POC。只有在任务拉取、执行、回报、审计、权限隔离和撤销全部通过后，再决定是否开发 Pack Hub skill 或 ACP adapter。
