# PiBot 开源项目对 Pack v2 的参考价值（2026-11-22）

> 状态：只读技术评估；不等于已在 Pack 中集成、部署或验证。整改继续先完成 Hub/Agent/UI，最后处理真实 Mac 安装。

## 1. 所审查项目

本次以 [w01fb0ss/PiBot](https://github.com/w01fb0ss/PiBot) 为主：本地优先的桌面多 Bot 工作台，具有群聊协调、工具/命令/浏览器/电脑控制、MCP、例行任务和审批。仓库声明 Apache-2.0，Agent 内核基于精确锁定的 pi SDK，Electron 仅作为宿主。已阅读根 README、[AGENTS.md](https://github.com/w01fb0ss/PiBot/blob/main/AGENTS.md)、[policy README](https://github.com/w01fb0ss/PiBot/blob/main/packages/policy/README.md) 和 [agent README](https://github.com/w01fb0ss/PiBot/blob/main/packages/agent/README.md)。

另有同名的 [FireSpoonYZ/PiBot](https://github.com/FireSpoonYZ/PiBot)，强调「中心服务器 + 多端设备本地执行」，许可证 AGPL-3.0。本次**不假定两者相同**；Pack 首版是 Mac 专业工作台，不因同名项目改为跨平台服务器架构。

注意：w01fb0ss/PiBot 在 2026-11-21 左右才公开；README 与源码提供设计证据，但缺少 Pack 环境下的真实安全、性能和用户体验验证。仅凭项目自述不能判定成熟度。

## 2. 值得借鉴的工程原则

| 优先级 | PiBot 已公开的设计 | Pack 对应落点 | 迁入方式与验收 |
| --- | --- | --- | --- |
| P0 | tools 统一先过 `packages/policy`，策略裁决与执行隔离，审批、命令和外发落审计 | Hub `_gated_tool`、Runtime 的 Browser/Desktop/Command 能力、v2 Permission/Approval | 建立「一个执行动作只能经授权入口」的工具矩阵；默认拒绝不认识的操作，验证跨房、提权、符号链接、权限变更与审批竞态 |
| P0 | Agent 核心不依赖 Electron，宿主能力通过接口注入；协议边界有共享 schema | Pack `runtime/agent-core`、`Provider Adapter`、SwiftUI↔Runtime↔Hub 事件契约 | 不照搬 Electron；统一事件类型、错误码、取消与超时；Agent 测试在无 SwiftUI/真账号环境可执行 |
| P1 | Bot 会话与凭据放入自己的数据目录、可注入 CredentialStore，不继承宿主 pi 环境；禁自动扫描其他用户目录 | Pack Provider Registry、Adapter、Keychain、Session Manager | 每个 Agent/Provider 隔离 session 与私密数据；假凭据/隔离 HOME 测试不得访问第三方个人记忆或密钥 |
| P1 | 工作目录边界与路径 canonicalization；高风险命令保守分析、grant 仅允许指定范围 | Pack Computer Controller、文件/命令工具、审计 | 用路径逃逸、symlink、命令包装器、下载即执行、内网请求验证 fail-closed；先定义策略，不直接抄一整套 Bash 分析器 |
| P1 | 操作清单可审查、macOS 可恢复的检查点 | Pack 工程文件、用户数据、桌面/文件操作 | 文件修改先有可追溯 diff/manifest；高风险修改审批、可逆 rollback；Mac 还原能力需在隔离机器实测 |
| P2 | 固定测试矩阵、分层依赖规则、fake model、E2E 验证入口 | `.github/workflows/ci.yml`、Runtime tests、Hub tests、macOS CI | 把权限决策和 Agent event replay 加进自动测试；协议快照与工程边界检查继续保留 |
| P2 | 定时、目录触发、连接触发例行任务 | Pack workflows/scheduler | 权限、幂等、重试/取消与唤醒防重复完成后再设计，暂不增加新调度系统 |

## 3. 不建议直接照搬

1. **不替换 Pack 现有 Muse Runtime 或 Codex App Server Adapter。** Pack 已确定「个人原生 Agent + 官方 API」双模式，采用逐 Provider Adapter 验证，而 PiBot 的 pi kernel 是另一种运行时选择。
2. **不将 PiBot 声称的 subscription sign-in 当成 ChatGPT / 豆包私人记忆继承能力。** 官方 API 与个人应用数据不自动打通，需逐 Provider 实测并遵守授权和服务条款。参见 `docs/v2/PROVIDER-MATRIX.md`。
3. **不把 Electron/Node 应用架构直接替换 Pack SwiftUI。** 可复用的是分层与权限契约，不是框架形态。
4. **不把未经本机验证的沙箱或自动检查点宣传为已安全可用。** PiBot policy 自述执行边界仍需 DNS 重定向复核，Windows 隔离不同于 macOS；Pack 必须独立验证。
5. **不在 Hub Phase 4 引入 PiBot 源码/依赖、数据库迁移或新的电脑执行权限。** 先做自有模块边界、契约测试和风险清单。后续若复用 Apache-2.0 实现，单独进行安全/许可证/NOTICE 审核；AGPL-3.0 的同名项目不能按相同条件复制。

## 4. 建议形成的后续验收任务（按原整改顺序）

1. Hub 任务/Run/转交/消息服务重构完成并通过原有 75 MCP 工具与 24 SQLite 表快照。
2. Agent/Runtime 阶段：审计 `Provider Adapter → permission gate → tool executor → audit` 全调用链，不得存在任一绕过审批的执行路径；mock provider 通过取消、超时、断线和重复事件测试。
3. Computer 阶段：统一 `read/write/execute/browser/desktop/network` 操作分类、独立审批与撤销授权；执行前二次检查权限，敏感路径与网络出口默认拒绝。
4. UI 阶段：明确展示权限范围、工具动作、审计轨迹、变更预览、人工接管和失败恢复状态；按包级合约对齐错误码与事件。
5. 以上仓库整改和 CI 稳定后，才进入 Mac 安装、保留账号/Keychain/登录态的差异升级和回滚验收。

**本轮决策：参考 PiBot 的架构与验证纪律，不引入 PiBot 代码或更换 Pack 产品方向。**
