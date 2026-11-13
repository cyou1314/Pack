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
