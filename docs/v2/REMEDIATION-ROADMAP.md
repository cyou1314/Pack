# Pack 仓库系统性整改路线图

> 状态：仓库级安全与打包质量门禁已通过 CI；真实 Mac/VM 与完整 DMG 验收仍待执行  
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
- [x] 复核本地服务 LaunchAgent 的权限与卸载边界：安装/卸载路径拒绝符号链接，卸载前验证 plist 身份和启动命令。

验收：所有系统服务的运行身份和 umask 有自动化检查；安装脚本语法检查通过；未标记任务不能通过 App 创建通配自动批准规则；原有显式规则库语义保持兼容。

### Phase 2 — 凭证、网络与 IPC 安全

- [x] 对 Keychain 读取错误、凭证不一致和迁移失败实行 fail-closed；明确区分首次迁移、Keychain 拒绝访问和配置文件残留。
- [x] 远程 Hub 连接强制 HTTPS；HTTP 仅允许 loopback（127.0.0.1、localhost、::1）。覆盖 macOS App、Pack Runtime、外部 Agent 和 Bridge。
- [x] 增加可注入凭证存储层和 Swift 单元测试，覆盖迁移成功、重复启动、Keychain 失败、凭证冲突和配置文件权限。
- [x] 将 `~/.pack` 配置目录权限收紧为 0700，配置文件保持 0600；拒绝符号链接配置文件及其父目录，避免凭证迁移跟随链接写到非预期位置。
- [x] 审查 L2 handoff Unix Socket、临时 Keychain ticket、Cookie 清理和错误日志脱敏；补上 JSON 根类型异常回归并保持票据/ Cookie 清理逻辑。
- [x] 为敏感凭证、事件包和共享上下文建立统一敏感字段识别规则；App Hub 错误日志仅记录安全类别。

验收：Keychain 不可用时不使用明文 Token 继续联网；远程明文 HTTP 被拒绝；本机回环 HTTP 保持可用；迁移过程可重复且不丢数据。


> 自动化边界说明：Phase 1/2 的仓库级实现与模拟测试进入 CI；真实 Mac Keychain ACL、真实登录态迁移、systemd VM 所有权变更、SSH 私钥与服务重启仍未在生产环境执行，必须留到隔离 Mac/VM 验收阶段。

### Phase 3 — 测试与质量门禁

- [x] 将 `runtime/l2-handoff/engine.test.cjs` 纳入 CI。
- [x] Node 语法检查覆盖 `.cjs` 文件。
- [x] 盘点并选择当前有效 staging 回归：默认 CI 执行 staging 15–19、23–24、38–46；其余历史快照继续隔离。
- [ ] 为 Hub 的权限、审批、规则通配、任务状态转换和并发边界建立完整正式测试。（已覆盖访问模式、审批持有人/dispatcher、action_tag/rule_eval；任务状态与并发仍主要由 staging 集成回归覆盖，待进一步独立化。）
- [x] 为 PackCore 增加 Swift 测试 Target，覆盖凭证迁移、Keychain 故障、Hub URL 与重定向策略。
- [x] 增加配置目录权限与符号链接拒绝的回归测试。
- [x] 将审批规则范围逻辑抽为 PackCore 纯函数，并补充单元测试。
- [x] 增加安装脚本、systemd/LaunchAgent 和 DMG 内容/版本元数据的自动检查。

验收：关键安全逻辑进入 CI；每项安全修复都有正向和反向测试；历史快照仍隔离，不污染默认回归链。

### Phase 4 — 代码架构渐进拆分

- [ ] Hub 按领域拆分为认证/成员、房间/消息、任务/运行、审批/规则、附件/备份、外部 Agent 等模块。
- [ ] 保持现有 MCP tool 名称、参数、返回结构和鉴权语义兼容。
- [ ] SwiftUI 按 Rooms、Agents、Tasks、Approvals、Settings、Computer 等 Feature 拆分。
- [ ] 将 AppState 拆成连接、会话、房间、任务、审批等独立状态域。（Connection/Members/Rooms/Tasks/Notifications/Approvals/Chat/Lifecycle/Catalog 行为已分批迁入 extension；`@Published` 状态仍由 AppState 托管，独立 Store 暂未引入。）
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

## 7. 第三批：审批范围与 LaunchAgent 安全边界（2026-11-21）

- PackCore 新增 `ApprovalRuleScope`：从任务标题提取与 Hub 相同格式的首个动作标记，并要求明确指派成员；空 action/target 不会生成通配规则。
- AppState 在单次批准前构造并验证规则范围，后续 `rule_upsert` 直接使用同一份已验证范围，避免 UI 校验与实际写入二次解析不一致。
- 新增 Swift 单元测试，覆盖首个动作标记、允许字符、非法/空标记、空白成员和目标名称清理。
- `local/install-local.sh` 安装前拒绝符号链接根目录、已有 Hub 目录、已存在 plist 和已加载的同名服务；创建私有数据目录。
- `local/uninstall-local.sh` 在任何 bootout 前验证所有目录和 plist 的 Label、launcher、ProgramArguments；遇到无法确认归属的已加载服务时拒绝操作。只删除明确的程序文件和 venv，保留数据库、凭证、附件、备份。
- `installer/manager.py` 对 `~/.pack`、Library、LaunchAgents、Hub、Engine、配置和 socket 路径增加安全预检；卸载前精确校验每个 LaunchAgent 的入口参数；Pack 数据根目录固定为 0700。
- Runtime LaunchAgent 显式设置 umask 077；本地 LaunchAgent 模板继续保持私有 umask。
- 自动化测试：`installer/test_launch_agent_safety.py`；CI 增加本地 shell 语法、Python 编译和安装/卸载边界测试。

验收：GitHub Actions [Run #139](https://github.com/cyou1314/Pack/actions/runs/44873563806) 全部通过，包含 Hub 默认 staging 回归、Runtime 和 macOS App 构建。

## 8. 第四批：DMG 构建可复现与版本清单（2026-11-21）

- `app/make-bundle.sh` 支持显式输出路径；目标已存在或为符号链接时拒绝覆盖，不再删除工作区内已有的 `Pack.app`。
- `installer/build-dmg.sh` 直接在临时 staging 目录调用 bundle builder，不再依赖仓库里预先存在的 `app/Pack.app`。
- `Info.plist` 增加 `CFBundleVersion`；DMG 根目录生成 `BUILD-MANIFEST.txt`，记录 App 版本/构建号、最低 macOS 版本、Python/FastMCP/Engine 版本和源码提交。
- 打包前检查 Hub/Engine payload 中已知数据库、Cookie、Token、Vault、Session 等用户数据文件；打包后执行 `hdiutil verify` 并输出 SHA-256。
- 新增 `installer/test_dmg_manifest.py`；macOS CI 实际构建 Pack.app，并执行 plist 与代码签名验证。

验收：GitHub Actions [Run #141](https://github.com/cyou1314/Pack/actions/runs/44874307272) 全部通过，三个 Job 均为 success。CI 已真实构建并验证 App Bundle，但**没有实际生成 DMG**；完整 DMG 仍依赖目标 Mac 上的 Python 3.12 Runtime、锁定依赖 venv、Engine 目录和 `hdiutil`，必须在隔离 Mac 上单独验收。

## 9. Phase 4 拆分前的代码边界盘点

当前规模与目标模块边界见 [PHASE4-MODULE-BOUNDARIES.md](PHASE4-MODULE-BOUNDARIES.md)。Hub 已将成员、房间、任务、规则、附件、记忆、技能、桌面、配对等大部分业务迁入领域服务，75 个 MCP tools 保留原名称/参数/返回契约；App Views 已按功能拆分，AppState 的 Connection/Members/Rooms/Tasks/Notifications/Approvals/Chat/Lifecycle/Catalog 行为已迁入同一 AppState 的领域扩展，主文件约 200 行。剩余工作聚焦 Settings 状态属性、跨域状态所有权评估和真实 Mac 验收；Catalog 加载/模板动作已独立迁入 `AppState+Catalog.swift`，但其 UI 状态仍由 AppState 托管。不以文件行数为目标，也不在缺少收益时引入独立 Store。

## 10. 每批交付记录格式

每批 PR/提交必须记录：

- 目标和问题证据
- 修改文件及行为变化
- 兼容性与迁移影响
- 自动化测试及结果
- 未验证事项
- 风险和回滚方式

“代码已提交”“CI 通过”“真实 Mac/VM 验收通过”必须分开陈述。


## 11. Phase 4 追加记录（2026-11-23：AppState 审批与 Chat/Composer）

- 审批请求识别、批准/拒绝、scoped always-allow 和任务验收/打回迁入 `AppState+Approvals.swift`；任务详情/创建/加急/取消留在 `AppState+Tasks.swift`。
- @成员提示、问答回复、附件和聊天消息生命周期迁入 `AppState+Chat.swift`。
- 这是声明边界迁移，不是状态架构重写：`@Published` 状态、依赖注入、actor 隔离、HubClient 与 UserDefaults 仍保持原有归属；不新增数据库迁移、MCP 工具、Swift Package target 或第三方依赖。
- 后续继续评估 Refresh 主循环与 Settings 的职责边界；只有出现明确跨域状态所有权和测试收益时才引入独立 Store。


## 12. Phase 4 追加记录（2026-11-23：AppState Lifecycle）

- `start()`、`refresh()` 和任务分页读取已迁入 `AppState+Lifecycle.swift`；原轮询周期、地址发现、快照读取顺序、generation 守卫、失败重试与未读/通知处理均保持原实现。
- `AppState.swift` 主要保留 `@Published` 状态所有权、少量应用级派发/派生状态和通用 action helper；尚未把状态属性拆成独立 Store。
- 后续不以文件行数为目标继续拆分；优先检查 Settings/catalog 边界、跨域状态所有权和可测性，再决定是否引入更明确的 Coordinator/Store。


## 13. Mac 安装升级与部署前盘点（2026-11-23）

- 新增 DMG 根目录 `upgrade.command` / `manager.py upgrade`，只接受可验证的完整 Pack 安装；先暂存/验证新版程序，再停止 Pack 写服务、确认端口释放，随后创建停机后的 SQLite/附件备份，最后原子替换 Hub/Engine/App。
- 新服务启动后通过 dispatcher token 执行只读 MCP `room_list`，并检查 Engine 本地端口；启动失败时停止新服务并回滚旧程序/App。**live DB/attachments 不自动回滚**，因为 Hub 可能已在健康检查期间接受新写入；经验证的预升级快照保留供人工恢复。当前升级不执行 SQLite schema migration，未来涉及表结构变更必须先实现独立、可逆的版本化迁移流程。
- 回滚状态由外层事务统一持有；文件/App rename 或恢复失败时不重新启动混合版本，保留暂存目录与恢复文件。
- `installer/test_upgrade_safety.py` 覆盖安装身份拒绝、敏感目录/凭证权限过宽、App 正在运行、备份失败、端口冲突、成功升级保留数据、Hub 启动失败后的程序回滚与 live DB/attachments 写入保留、Engine local data 符号链接拒绝、程序/App rename 回滚失败、服务未能完全停止时禁止回滚等路径。
- 新增 `installer/mac-inventory.sh`：只读盘点 Pack/Engine/App、Pack 与 legacy runtime LaunchAgent、端口、SQLite quick_check、附件和备份数量；不输出配置/Token/Keychain 值，不启停服务或改写 Pack 数据。
- macOS CI 增加 installer safety/upgrade tests，在 Apple Runner 实际执行 inventory 脚本，并单独编译 Swift Release 配置、以 Release 构建 Pack.app Bundle。Run [#45109400860](https://github.com/cyou1314/Pack/actions/runs/45109400860) 的 macOS installer regression、inventory、Swift Build/Unit Tests/App Bundle 和 Runtime 已通过；Hub Default staging regression 仍在运行，最终全绿以最新 head 的 CI 为准。
- 尚未完成：隔离 Mac 上的真实 DMG 构建/升级/失败回滚、Keychain/登录态验证和生产 Mac 部署。当前远程 Mac 设备离线；不合并 PR #7、不操作用户真实 Mac。


### 安装升级数据回滚策略修正（2026-11-23）

- 复核发现：新 Hub 可能已在 Engine/辅助服务健康检查失败前接受其他本地 Agent 的写入。若此时自动恢复停机前 SQLite 快照，会静默丢弃新消息/任务；只恢复数据库而不恢复附件也会造成 DB/附件不一致。
- 因此升级失败时只回滚程序文件与 App，不自动替换 live DB/attachments；保留已验证的 SQLite/附件快照供人工判断后恢复。失败版本产生的写入继续留在 live data 中。
- 当前整改不改变 24 张 SQLite 表和 75 个 MCP 工具契约，没有 schema migration。未来若要迁移表结构，必须单独设计版本化、可逆迁移和兼容窗口，不能复用代码升级回滚覆盖用户数据。
- 隔离 Mac 操作说明补充把旧 DMG 的 `Pack.app` 复制到测试根目录 `Applications/Pack.app`；服务安装脚本本身不负责拖放/安装 App Bundle。
- 自动化回归新增 live DB/WAL/SHM 与新附件写入保留断言，以及隔离 Mac App Bundle staging 文档检查。


### AppState Catalog 边界（2026-11-23）

- `loadCatalog` 与 `fireTemplate` 已迁入 `AppState+Catalog.swift`；不改变技能/模板列表请求、Hub MCP 参数、模板创建动作或 UI 状态。
- `AppState.swift` 继续持有 catalog 的 `@Published` 状态；本次只拆动作，不把 UI 状态迁入独立 Store。


### 升级前敏感路径权限校验（2026-11-23）

- `manager.py upgrade` 现在要求 Pack 数据根、Hub/Engine、附件、Engine data/local 与 handoff socket 目录无 group/other 权限；config、SQLite、dispatcher/owner token 与 Pack LaunchAgent plist 也不得对 group/other 开放。
- 检测到权限过宽时在停止任何服务前拒绝升级，不自动 chmod 或接管目录，避免静默改变现有安装权限。
- 新增 `test_world_readable_config_is_rejected_before_backup_or_service_stop`，验证 0644 配置会被拒绝且不 bootout、不创建升级备份。


### DMG Release / Apple Silicon 构建门禁（2026-11-23）

- `app/make-bundle.sh` 增加 `PACK_BUILD_CONFIGURATION=debug|release`，默认保持开发用 debug；DMG 构建显式使用 Release 二进制。
- `installer/build-dmg.sh` 要求在原生 arm64 macOS 主机运行，并检查 Python、venv、Pack App 与 Electron App 均包含 arm64；不匹配时在生成 DMG 前失败。
- `BUILD-MANIFEST.txt` 记录 Host/App/Python/Engine architecture 与 AppBuildConfiguration，便于验收产物和目标 Mac 是否一致。
- macOS CI 增加独立 Swift Release 编译，并用 Release 配置构建/签名 App Bundle；完整 DMG 仍需隔离 Mac 的实际 Engine payload 和 `hdiutil` 验收。
