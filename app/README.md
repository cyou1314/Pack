# Pack 群主 App（macOS，SwiftUI）

B2 阶段的原生 App：群聊 / 任务板 / 成员三屏（M0 只读，写通路在 M1）。

## 构建与运行（在 Mac 上）

```bash
cd app
swift build
.build/debug/PackApp
```

需要完整 Xcode 或可用的 Swift 工具链；纯 SwiftPM 工程，无第三方依赖。

## 配置

App 读取 `~/.pack/config.json`：

```json
{ "hubUrl": "https://<当前隧道网址>/mcp", "token": "<dispatcher 令牌>" }
```

令牌只放本机此文件（权限 600），不入仓库。隧道网址会自动发现
（连不上时拉仓库 `runtime/hub-url.txt`），config 里的值只是起点。

## 结构

- `Sources/PackApp/HubClient.swift`：中枢瘦客户端（手写 MCP JSON-RPC，
  串行/会话自愈/网址发现，与 runtime.mjs 同逻辑）
- `Sources/PackApp/Models.swift`：消息/任务/成员模型
- `Sources/PackApp/PackApp.swift`：界面（三屏）与轮询状态

## Agent 工作台（新增）

侧栏新增 Agent 页面，与原有 Muse 成员 / Hub 任务体系并行，不会替换原有登录态成员。

### 当前支持

- 聊天展示：按消息角色渲染独立气泡，支持 SwiftUI `AttributedString` Markdown 基础解析与文本选择；代码高亮和附件消息仍未实现。

- 清空会话：只清空当前会话消息并重置该会话 Response ID，不影响同 Agent 的其他会话。

- 会话迁移：SQLite Schema v3 将旧版每 Agent 单聊天记录迁移到该 Agent 的“默认会话”，迁移在事务中执行；切换模型或 Endpoint 会清除该 Agent 的所有会话续接 ID。

- 多会话：每个 Agent 可创建、切换、重命名和删除多个本地会话；每个会话独立保存消息与 OpenAI Response ID。

- 中断生成：提供本地流式任务取消；当前 OpenAI Adapter 无法确认服务端生成是否同步停止，界面会明确提示服务端状态未确认。

- 聊天管理：可清空当前 Agent 的本地聊天记录；不删除服务商侧会话或 Response ID。

- API Key 更新：可在 Agent 编辑页单独替换 Key；新值先写入 Keychain 并初始化新 Adapter，失败时回滚旧 Key。该步骤只验证本机 Keychain 读取和 Adapter 配置，不代表服务商已接受密钥，也不会发送计费请求。

- Agent 编辑：支持修改显示名称、模型 ID 和自定义 Endpoint；模型或 Endpoint 变化时重建 Adapter 并清除旧 Response ID，需重新测试连接。

- OpenAI 官方 API：Responses API、SSE 流式输出、`previous_response_id` 会话续接。
- 多账号：每个 Agent Profile 使用独立 UUID；同一 Provider 可添加多个账号。
- 本地安全存储：Profile 存于 `~/Library/Application Support/Pack/agents.sqlite`；API Key 存于 macOS Keychain，SQLite 仅保留 Keychain reference。
- 连接状态：创建时显示 `configured`；用户显式执行真实 API 测试成功后显示 `connected`。
- 聊天：每个 Agent 独立维护当前进程内的聊天显示内容和会话续接 ID。

### 添加 OpenAI API Agent

1. 打开侧栏「Agent」并选择「添加」。
2. 填写显示名称、模型 ID 和 OpenAI API Key；自定义 Endpoint 可留空。
3. 保存后，密钥写入 Keychain，Profile 写入 SQLite。
4. 点击「测试连接」会发送一次真实 API 请求，可能产生 API 费用；只有请求成功后才标记为已连接。
5. 后续消息使用该 Agent 最近一次返回的 Response ID 续接会话。

### 当前限制

- 仅 OpenAI 官方 API Adapter 已实现；ChatGPT 个人账号 Native 接入、豆包 Native/API Adapter 尚未实现。
- 当前不支持附件、服务端会话列表、服务端取消和工具调用。
- 会话续接 ID（OpenAI Response ID）随 Agent Profile 持久化；重启后可以继续同一 API 会话。聊天 transcript 按 Agent ID 存入本机 SQLite，启动时恢复最近 500 条消息；删除 Agent 时由外键级联删除其聊天记录。
- API Agent 使用 Pack 管理的上下文边界；不会读取或声称拥有 ChatGPT 个人账号里的私有记忆。
- 自定义 Endpoint 必须使用 HTTPS；请只配置可信服务商地址。

### 构建

```bash
cd app
swift build
```

GitHub Actions 的 macOS App / build 用于验证 Swift 编译；真实 API Key、线上请求和本机 Keychain 行为仍需在用户 Mac 上验收。