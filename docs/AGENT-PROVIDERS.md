# Provider 接入与 Credential Vault

## 候选 Provider

- ChatGPT / OpenAI API：独立 nativeAccount 与 officialAPI Profile。
- Gemini / Gemini API：分别使用 gemini 与 gemini-api ID。原生模式由用户在服务商允许的界面自行登录 Google 账号；官方 API 模式使用 Google AI Studio / Gemini Developer API 的 API key，不以个人网页登录态替代 API 凭证。
- 豆包 / 火山引擎 API：独立 Profile。
- Muse：保留现有 Engine。

Google 文档：[Gemini Apps](https://support.google.com/gemini/answer/13275745)、[Gemini 记忆](https://support.google.com/gemini/answer/16598469)、[Gemini API](https://ai.google.dev/gemini-api/docs)。个人历史、Gems、工具及关联应用的能力由服务商及账号资格决定，不承诺第三方自动化或 API 可访问这些能力。不得绕过登录、账号安全检查或访问限制。

## 凭证边界

新增 AgentCredentialVault，API Profile 使用 AgentCredentialReference.forProfile 生成 UUID 引用。Keychain generic-password service 为 com.cyou.pack.agent-api.v1，account 为 providerID:profileUUID。使用 Data Protection Keychain、非同步、WhenUnlockedThisDeviceOnly。不得以明文 JSON、命令行参数、环境变量或日志传输 API key。Native Profile 只使用独立浏览器会话存储，不把 Cookie/Google 密码录入 Vault。

插入不覆盖已有值；轮换和删除为独立显式操作。旧任意字符串引用不自动迁移，Vault 拒绝访问，Registry 兼容读取仍保留。后续 UI 必须使用 UUID 工厂和受信任的内部 Adapter；尚未实现 UI 到 Vault 的完整事务/补偿、实际 API 请求、浏览器 IPC、账号登录。Keychain backend 实际访问受签名及系统权限影响；CI 只验证编译和内存 backend，不声称签名 App 的实机 Keychain 已验证。失败时不降级到文件。
