# Engine 开源替代方案初步评估

日期：2026-11-25。以下为公开文档评估，不代表已经集成或运行验收。

| 项目 | 许可证 | 建议定位 |
| --- | --- | --- |
| [Playwright](https://github.com/microsoft/playwright) | Apache-2.0 | 浏览器底层优先：持久化独立 BrowserContext、确定性操作和测试 |
| [agent-browser](https://github.com/vercel-labs/agent-browser) | Apache-2.0 | 可替换 CLI 驱动候选，评估 CDP 和进程隔离 |
| [browser-use](https://github.com/browser-use/browser-use) | MIT | 可选高层自主浏览器代理，不作为原生账号凭证管理器 |
| [Stagehand](https://github.com/browserbase/stagehand) | MIT | 可选语义操作层，需区分本地与云端执行 |
| [Peekaboo](https://github.com/openclaw/Peekaboo) | MIT | macOS 桌面控制候选，需验证 TCC 与系统版本 |

决策：先评估 Playwright 作为 Pack Browser Driver 基础，agent-browser 作为 CLI 候选，Peekaboo 作为独立桌面工具。不要引入完整的第三方 Agent 编排栈替换 Pack Hub。现有 Muse Engine 保留，不删除，不以开源组件声称兼容其 IPC。

## 接入边界

- Pack Registry 用 UUID 分配每个 Native Profile 的独立浏览器目录；用户自行登录，不导入默认个人浏览器 Cookie，不绕过服务商登录或访问限制。
- 原生网页端的历史、记忆和工具仍归 Provider 管理，网页自动化不保证这些能力可用；官方 API 由独立 Adapter 和 Keychain 凭证引用管理。
- 新驱动必须实现受限的启动、停止、健康检查、取消与超时；默认仅本地 IPC，不开放调试端口给网络，不默认授予桌面控制。
- 验收先使用本地测试网页，验证两账号隔离、重启持久化、退出清理、取消、输出上限与权限拒绝，再按 Provider 官方支持方式试验。
- 锁定上游版本、依赖及许可证 NOTICE；检查下载与发布产物哈希。不得自动下载未固定版本的代码。
- 原有 Engine 源码及真实产物锁仍缺失，以上候选不消除现有 DMG 发布阻塞；新 Engine 必须通过独立验收才可替换。
