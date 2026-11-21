# Pack v2 Provider 能力矩阵

**状态口径：** 已有仓库实现 / 官方候选接口 / 待实测 / 不支持。未完成端到端验证的能力不得标记为已支持。

| Provider | 个人账号原生接入 | 本机 Runtime | 官方 API | 当前仓库状态 | 下一步验证 |
|---|---|---|---|---|---|
| Muse | Custom Connector 候选 | 现有 ego Runtime | 待评估 | 已有 Muse Runtime 与 MCP Connector 相关资产；无完整 v2 Adapter | Connector 能否接收消息、主动回复、恢复会话；Pack 能否反向唤醒 |
| Codex | 复用本机登录的 App Server 路线 | Codex App Server 候选 | 官方 API 路线需另行评估 | 已有 external pack-agent 接入，不等于原生 App Server 集成 | 本机登录、Thread/Turn、事件、审批、重连 |
| Gemini | 个人 App 原生会话接口待验证 | Gemini CLI 候选 | Gemini API | 尚无 v2 Adapter | CLI JSONL、会话恢复、工具调用和取消 |
| ChatGPT | 个人 App 会话接口待验证 | 无默认本机 Runtime | OpenAI Responses API | 尚无 v2 Adapter | 流式、工具调用、MCP、费用、上下文隔离 |
| 豆包 | 个人助手接口待验证 | 可选本机桥接待评估 | 火山方舟 | 尚无 v2 Adapter | 模型 API、Function Calling、流式和计费 |
| Grok | 个人 App 会话接口待验证 | 待评估 | 官方 API 候选 | 尚无 v2 Adapter | 官方 API、工具和会话能力 |
| Hark | 个人助手接口待验证 | 待评估 | 待验证 | 尚无 v2 Adapter | 获取官方接入规范和可用权限 |

## 接入能力分级

- A：原生双向会话，可发送、接收、恢复和主动唤醒。
- B：本机 Runtime，可通过受支持协议运行和恢复。
- C：官方 API，可创建 Pack 独立 Agent。
- D：MCP Connector，仅能调用 Pack 暴露的工具或按协议回传消息。
- E：Browser Bridge，受 UI 变更和平台策略影响的兼容方案。

能力等级必须按具体 Adapter 实测，而不是按 Provider 整体定级。

## 明确限制

1. OpenAI API Agent 不自动继承 ChatGPT App 的私人记忆、历史聊天或自定义 GPT。
2. 豆包 API Agent 不自动继承豆包 App 的个人记忆和助手会话。
3. Muse Connector 可用不代表支持外部主动唤醒或读取完整私人历史。
4. Codex 通过 pack-agent 外部成员接入不等于 Codex App Server 原生接入。
5. 浏览器自动化不等于官方个人 Agent 接入。
6. API 使用费用与个人 App 订阅分开统计。

## Phase 0 必须形成的证据

每个 Provider 至少保存官方文档链接和检查日期、使用的 API/CLI/Connector 版本、认证方式、最小可运行示例、请求/响应脱敏日志、断线恢复结果、工具调用结果、记忆边界验证、失败与限流表现及明确不支持的能力。