# Provider 接入与 Credential Vault

## 候选 Provider

- ChatGPT / OpenAI API：独立 nativeAccount 与 officialAPI Profile。
- Gemini / Gemini API：分别使用 gemini 与 gemini-api ID。原生模式由用户在服务商允许的界面自行登录 Google 账号；官方 API 模式使用 Google AI Studio / Gemini Developer API 的 API key，不以个人网页登录态替代 API 凭证。
- 豆包 / 火山引擎 API：独立 Profile。
- Muse：保留现有 Engine。

Google 文档：[Gemini Apps](https://support.google.com/gemini/answer/13275745)、[Gemini 记忆](https://support.google.com/gemini/answer/16598469)、[Gemini API](https://ai.google.dev/gemini-api/docs)。个人历史、Gems、工具及关联应用的能力由服务商及账号资格决定，不承诺第三方自动化或 API 可访问这些能力。不得绕过登录、账号安全检查或访问限制。

## 凭证边界

新增 AgentCredentialVault，API Profile 使用 AgentCredentialReference.forProfile 生成 UUID 引用。Keychain generic-password service 为 com.cyou.pack.agent-api.v1，account 为 providerID:profileUUID。使用 Data Protection Keychain、非同步、WhenUnlockedThisDeviceOnly。不得以明文 JSON、命令行参数、环境变量或日志传输 API key。Native Profile 只使用独立浏览器会话存储，不把 Cookie/Google 密码录入 Vault。

插入不覆盖已有值；轮换和删除为独立显式操作。旧任意字符串引用不自动迁移，Vault 拒绝访问，Registry 兼容读取仍保留。后续 UI 必须使用 UUID 工厂和受信任的内部 Adapter；尚未实现 UI 接入与完整生命周期事务、实际 API 请求、浏览器 IPC、账号登录。Keychain backend 实际访问受签名及系统权限影响；CI 只验证编译和内存 backend，不声称签名 App 的实机 Keychain 已验证。失败时不降级到文件。

## API Profile 创建流程

新 API Profile 使用单一 AgentRegistryStore 实例的 provisionAPIProfile：先验证 Registry，再插入 Keychain，最后原子持久化 Registry。写入失败或提交前检测到取消时，只回滚本次成功插入的凭证；重复凭证不会被删除。跨 await 期间阻止同一 Store 的其他元数据写操作。回滚失败返回 credentialCleanupRequired(profileID:)；调用方应保留该身份并显式处理，不得盲目重试或覆盖已有凭证。

该流程不是跨 Keychain 与文件系统的原子事务，进程被终止、多个 Store 实例或外部凭证写入不在保证范围内。后续 UI 必须统一写入入口；完整崩溃恢复、凭证孤儿排查、凭证删除及恢复策略以及签名 App 实机验收仍待完成。现有 metadata-only insert 保留用于兼容导入，不用于新的 API Profile 创建。

## 已注册 API Profile 的凭证轮换

通过同一 Store 的 rotateAPICredential(id:apiKey:vault:) 操作：仅允许已登记的 officialAPI Profile，使用登记时的不可变身份及凭证引用；不覆盖其他 Profile，不创建缺失凭证，不改变 Registry 内容。与创建及元数据写操作共用互斥门。轮换前检查取消；Keychain 成功替换后不以取消为由回滚旧密钥。此操作仅替换本地保存的凭证，不代表服务商已撤销旧 Key，也不验证新 Key 在线有效性。

所有生命周期写操作应由同一 Store 所有者统一调用；直接调用 Vault 写接口或不同 Store 实例不受该门保护。删除、缺失凭证修复、崩溃后的孤立凭证恢复仍未接入。测试使用内存 backend 注入重入及清理失败，不接触真实 Keychain。
