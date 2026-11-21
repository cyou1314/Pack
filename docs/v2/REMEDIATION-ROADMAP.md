# Pack 仓库系统性整改路线图

> 状态：执行中  
> 基线：PR #6 `codex/pack-v2-foundation-20261121` @ `23c52203d69be0b47eb5ce58833aa1a55d9e19fe`  
> 整改分支：`codex/pack-repository-remediation-20261121`  
> 原则：先完成仓库级整改与 CI 验收，再进行用户 Mac 本机部署和端到端验收。

## 1. 目标

把 Pack 从已有 v1 协作产品和 v2 Agent Core 基础，逐步整理成可维护、可验证、可安全部署的专业级 macOS AI 工作台。

本轮不是推倒重写。保留已经验证有效的 Hub、任务/审批、Muse Runtime、附件、备份和 macOS App 能力；在兼容既有数据和协议的前提下，逐步拆分模块、加固权限、补齐测试，并建立可回滚的发布流程。

## 2. 基线事实

以下只代表本路线图建立时仓库可见的事实，不等同于生产环境验收：

- `main` 基线：`3c11de6cf8365c4de9098260ebfea519055f9800`。
- PR #6：开放的 Draft PR，基于 `main`，包含 v2 Agent Core/Codex Adapter 基础和历史文件归档。
- PR #6 提交 `23c5220` 的 GitHub Actions：Hub/Regression、Runtime、macOS App Build 均通过。
- `hub/hub.py` 约 5,400 行；SwiftUI 的 `PackApp.swift` 和 `Views.swift` 仍包含大量状态及视图实现。
- 当前 CI 执行 Node `runtime/tests/*.test.mjs`，未覆盖 `runtime/l2-handoff/engine.test.cjs`。
- 系统级 systemd 服务单元未显式设置运行用户、组和 umask；systemd 系统服务默认身份需要在目标 VM 上验证。
- `rule_eval` 明确将空 action / target 作为通配键；App 的“总是允许”路径在任务标题没有动作标记时会生成空 action。
- `HubClient.load` 在部分 Keychain 读取错误或凭证不一致时仍可能使用配置文件中的凭证；应在单独变更中设计 fail-closed 行为和迁移测试。

## 3. 执行原则

1. **分支隔离**：本分支从 PR #6 当前 Head 创建；本整改 PR 以 PR #6 分支为 base，形成可审查的 stacked PR。不得直接改写 `main` 或 PR #6。
2. **小批次提交**：每一批只解决一组相关问题；先写测试或验收，再改实现。
3. **兼容优先**：未经迁移设计，不改 Hub SQLite 既有表结构、既有 API 参数、用户配置格式和登录态数据。
4. **安全默认**：权限、凭证、浏览器出口和 Agent 执行失败时采用 fail-closed；不得为了“兼容”静默降级。
5. **删除需证据**：只有确认无引用、无运行时发现、无打包/安装依赖后，才删除文件或依赖；否则先标记弃用。
6. **验证分层**：静态检查、单元测试、Mock 集成测试、CI 构建和真实 Mac/VM 验收分别记录，不相互替代。
7. **不碰生产**：本轮只改 Git 仓库；不连接用户 Mac、不部署 VM、不操作真实账号/凭证、不迁移生产数据库。

## 4. 分阶段路线

### Phase 0 — 基线与变更控制

- [x] 从 PR #6 精确 Head 创建独立整改分支。
- [x] 创建整改 PR，并以 PR #6 分支为 base。
- [x] 建立本路线图和每批次变更记录。
- [x] 记录基线 CI 和提交 SHA。

验收：PR #6 和 `main` 均未被本分支改写；整改 PR 的变更可独立审查。

### Phase 1 — 安全与部署基础

优先修复能够从仓库代码明确确认、且可以通过静态/单元测试验证的风险：

- [x] systemd 服务使用专用低权限账户 `hatch`、组 `hatch` 和严格 umask。
- [x] 给服务增加经过验证的基础 systemd hardening；避免限制业务所需的工作目录写入和网络访问。
- [x] 更新服务安装脚本：使用脚本相对路径安装 unit；reload 后确保长驻服务重启，令新权限真正生效。
- [x] 将 Hub “总是允许” UI 限制为明确的动作标记 + 指派成员；无明确范围时只允许单次批准，不自动创建空 action 通配规则。
- [x] 为 systemd unit 策略和安装脚本增加静态回归测试。
- [ ] 复核本地服务 LaunchAgent 的权限与卸载边界。

验收：所有系统服务的运行身份和 umask 有自动化检查；安装脚本语法检查通过；未标记任务不能通过 App 创建通配自动批准规则；原有显式规则库语义保持兼容。

### Phase 2 — 凭证、网络与 IPC 安全

- [x] 对 Keychain 读取错误、凭证不一致和迁移失败实行 fail-closed；明确区分首次迁移、Keychain 拒绝访问和配置文件残留。
- [x] 远程 Hub 连接强制 HTTPS；HTTP 仅允许 loopback（127.0.0.1、localhost、::1）。覆盖 macOS App、Pack Runtime、外部 Agent 和 Bridge。
- [x] 增加可注入凭证存储层和 Swift 单元测试，覆盖迁移成功、重复启动、Keychain 失败、凭证冲突和配置文件权限。
- [x] 将 `~/.pack` 配置目录权限收紧为 0700，配置文件保持 0600；拒绝符号链接配置文件，避免凭证迁移跟随链接写到非预期位置。
- [x] 审查 L2 handoff Unix Socket、临时 Keychain ticket、Cookie 清理和错误日志脱敏；补上 JSON 根类型异常回归并保持票据/ Cookie 清理逻辑。
- [x] 为敏感凭证、事件包和共享上下文建立统一敏感字段识别规则；App Hub 错误日志仅记录安全类别。

验收：Keychain 不可用时不使用明文 Token 继续联网；远程明文 HTTP 被拒绝；本机回环 HTTP 保持可用；迁移过程可重复且不丢数据。


> 自动化边界说明：Phase 1/2 的仓库级实现与模拟测试进入 CI；真实 Mac Keychain ACL、真实登录态迁移、systemd VM 所有权变更、SSH 私钥与服务重启仍未在生产环境执行，必须留到隔离 Mac/VM 验收阶段。

### Phase 3 — 测试与质量门禁

- [x] 将 `runtime/l2-handoff/engine.test.cjs` 纳入 CI。
- [x] Node 语法检查覆盖 `.cjs` 文件。
- [ ] 盘点归档 staging 中仍代表当前安全边界的测试；只迁移有明确现行价值的用例，不直接恢复全部历史套件。
- [ ] 为 Hub 的权限、审批、规则通配、任务状态转换和并发边界建立正式测试。
- [x] 为 PackCore 增加 Swift 测试 Target，覆盖凭证迁移、Keychain 故障、Hub URL 与重定向策略。
- [x] 增加配置目录权限与符号链接拒绝的回归测试。
- [ ] 将审批规则范围逻辑抽为可独立测试的纯函数，并补充对应单元测试。
- [x] 增加安装脚本和 systemd 单元的自动检查；DMG 内容清单与版本元数据检查仍待补齐。

验收：关键安全逻辑进入 CI；每项安全修复都有正向和反向测试；历史快照仍隔离，不污染默认回归链。

### Phase 4 — 代码架构渐进拆分

- [ ] Hub 按领域拆分为认证/成员、房间/消息、任务/运行、审批/规则、附件/备份、外部 Agent 等模块。
- [ ] 保持现有 MCP tool 名称、参数、返回结构和鉴权语义兼容。
- [ ] SwiftUI 按 Rooms、Agents、Tasks、Approvals、Settings、Computer 等 Feature 拆分。
- [ ] 将 AppState 拆成连接、会话、房间、任务、审批等独立状态域。
- [ ] 统一配置、日志、错误模型、网络客户端和设计 Token。
- [ ] 建立依赖方向规则，避免 Feature 之间循环依赖。

验收：每次拆分前后同一组回归通过；没有无关 UI 改版；没有未计划的数据库迁移；模块边界有依赖图和文档。

### Phase 5 — Agent Runtime 运行闭环

- [ ] 明确 Provider / Agent / Connection / Session / Run / Event 数据模型和生命周期。
- [ ] 建立本机 Runtime Supervisor：进程启动、健康检查、重连、关闭、崩溃恢复。
- [ ] 把 Codex App Server Adapter 接入实际运行主循环，而不只是独立模块。
- [ ] 将事件规范化、持久化、去重并路由到 Room。
- [ ] 审批事件与 Hub 权限体系闭环；默认拒绝未知/超时审批。
- [ ] 再按同一契约接入 Gemini CLI 和官方 API Provider。
- [ ] Muse 旧桥接保持兼容，官方 Connector 能力单独验证；不复制 Cookie 抓取/私有接口模式。

验收：至少一个真实 Provider 在本机 Runtime 内完成连接、创建/恢复会话、流式回复、中断、审批、断线恢复和审计记录；Mock 测试不能替代真实 Mac 验收。

### Phase 6 — Browser / Computer Use 与工作台产品化

- [ ] 独立 Browser Controller：标签页、导航、下载/上传、会话隔离、域名策略和操作审计。
- [ ] macOS Desktop Controller：Accessibility、屏幕/键鼠操作、权限申请和人工接管。
- [ ] Agent 群聊、@ 路由、任务分工、交接、循环/预算限制和终止条件。
- [ ] Agent 添加向导：原生个人账号、本机 CLI、官方 API 三种模式及能力差异说明。
- [ ] 安装、升级、卸载、数据保留和回滚流程完善。
- [ ] 统一可观测性、诊断包、性能基线和用户可见错误。

验收：在真实 Mac 上按任务级 E2E 验收；明确哪些功能使用原生账号上下文、哪些由 Pack 自己管理；电脑控制有最小权限和可追溯记录。

### Phase 7 — 本机部署前 Gate

只有前述仓库整改完成后才进入：

- [ ] CI 全绿且无未解释的 flaky test。
- [ ] DMG 可重复构建并检查内容清单。
- [ ] 安装、升级、卸载和回滚在干净测试账户/隔离目录验证。
- [ ] 备份与恢复演练通过。
- [ ] 形成部署清单、风险清单和明确的回滚点。
- [ ] 用户确认后才在用户 Mac 上安装和连接真实账号。

## 5. 第一批实际施工范围

首批控制在低耦合、可自动验证的安全改动：

1. systemd 五个服务单元的运行身份、umask 和基础 hardening。
2. `hub/install-services.sh` 的路径解析、unit 安装和重启生效流程。
3. Hub 预检与 systemd 静态测试。
4. App “总是允许”不再为缺少动作标记的任务创建空 action 通配规则。
5. CI 执行 L2 handoff 的现有 Node 测试，并将 `.cjs` 纳入语法检查。

以下暂不在第一批修改：Keychain 迁移逻辑、Hub 数据库 Schema、Hub 大文件拆分、App 大规模拆分、Provider 新接入、Browser/Computer Use。它们需要各自的设计和测试，不与低耦合修复混在一个提交中。

## 6. 第二批追加记录（2026-11-21）

- 变更：PackConfig 在加载凭证前拒绝符号链接配置文件；将配置父目录权限验证并收紧至 0700；配置文件继续强制 0600。
- 测试：新增目录权限收紧测试、符号链接拒绝且不读取/迁移目标凭证测试。
- 兼容性：不改变 JSON 字段、Keychain service/account、Hub URL 规则和 MCP 请求协议；首次加载会收紧 `~/.pack` 目录权限。
- 未验证：真实 Mac Keychain ACL、不同安装用户/自定义 `PACK_CONFIG_PATH` 场景，以及真实用户目录的权限修复。
- 回滚：回退本批 PackConfig 与 PackConfigTests 两个文件的提交；已被收紧为 0700 的目录权限不会自动放宽。

## 7. 每批交付记录格式

每批 PR/提交必须记录：

- 目标和问题证据
- 修改文件及行为变化
- 兼容性与迁移影响
- 自动化测试及结果
- 未验证事项
- 风险和回滚方式

“代码已提交”“CI 通过”“真实 Mac/VM 验收通过”必须分开陈述。
