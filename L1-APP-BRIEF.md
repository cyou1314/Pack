# L1 App 片施工简报：登录容器（调度员派单，Codex 施工）

设计正本：LOGIN-CONTAINER-DESIGN.md（本简报是其切片 L1 的 App 侧施工单）。
中枢侧已上线（staging47，工具数 64）：member_register 新增可选参数
provider；新工具 member_login_state_set(name, state)，state 只许
pending / logged_in / expired；member_list 每行带 provider 与 login_state
（老成员两字段为空）。本片只做 App，不接引擎（cookie 交接是 L2）。

## 范围

1. **模型**：MemberInfo（Models.swift:114）加 provider、loginState 两可空
   字段，解析 member_list 的 provider / login_state，缺失按 nil，不影响
   既有字段解析。
2. **登记透传 provider**：registerMember（PackApp.swift:608 起）调用
   member_register 时加 provider 参数。加成员表单（§12.6 既有流程：
   名字→头像预览→发起）在名字与头像之外加脑源一项；首批注册表只有
   muse 一项，呈现为固定单选「脑源：Muse」即可，不做空列表框架。
3. **登录容器**：MemberLoginShell（Views.swift:1435）占位换真容器：
   WKWebView（NSViewRepresentable 包装），把 1443 行「登录页容器 ·
   尚未接入」占位去掉。每个成员一个**持久** WKWebsiteDataStore，成员间
   隔离：用 WKWebsiteDataStore(forIdentifier:) ，标识 UUID 按成员名
   稳定派生（成员名→UUID 映射存 UserDefaults，首次生成后固定）。
   禁止 nonPersistent、禁止 default() 共享 store。报告里写明所选方案。
4. **provider 注册表**：App 内置映射 [provider: 登录起始 URL]，首批仅
   "muse" → https://muse.ai （登录页由服务方自身路由，App 不拼登录
   路径）。成员 provider 为空（老成员）时默认按 muse 打开；provider
   非空但不在注册表时，容器不打开网页、显示「该脑源暂不支持内嵌登录」。
5. **完成判定（双通道）**：①导航判定：WebView 当前 URL 离开登录路由
   ——host 仍为 muse.ai 且路径不再含登录段（login / sign-in / auth 等，
   具体规则施工时以实测 muse.ai 登录流为准、写进报告）；②成员点
   sheet 内的「我已登录」按钮。任一触发即调 member_login_state_set
   (name, "logged_in")，成功后关 sheet 并 refresh。回写失败要显式报错
   （沿用 memberActionError 口径），不许静默吞掉假装成功。
6. **状态呈现**：memberStatus（PackApp.swift:602 起）与成员详情
   （Views.swift:1317 起）改为 login_state 优先：
   pending →「接入中 · 等待登录」；logged_in →「已登录 · 待引擎接入」；
   expired →「登录已失效 · 待重新登录」。login_state 为空的老成员保持
   现行逻辑（心跳 + pendingMembers），不回退。成员详情加一行登录状态
   （脑源名 + 状态文案）；1368 行「成员本人登录 / 重新登录」按钮保留，
   打开容器；重登复用同一分区、不清数据。
7. **调用层**：member_register 加参、member_login_state_set 回写都走
   PackApp 层现有 client.call 直调（registerMember 先例），不改
   HubClient.swift 的传输与接口面。

## 红线

- 不注入 JavaScript 读取表单内容；App 不读、不存密码与验证码——登录
  只发生在服务方页面里，由成员本人操作。
- 会话数据只留在本机该成员的 WebView 分区里：不出 App、不上中枢
  （中枢只写状态枚举）、不进房间。
- 本片不做删分区（删成员清分区是 L3）、不做 cookie 导出（L2）。
- 禁 @State（一律 @StateObject + @Published）；数值只从 DesignTokens
  取；不动其他已验功能；连败两次停下报告。

## 验收（自验后按固定格式报告）

- 构建通过（CLT Swift 构建命令与结果写进报告）。
- 新登记一个测试成员（provider=muse）：成员列表/详情状态显示
  「接入中 · 等待登录」，中枢侧 login_state=pending（报告写明核对方式）。
- 打开该成员的登录容器：muse.ai 页面真实加载（截图为证，交调度员
  亲验）；判定规则与实测到的登录流 URL 写进报告。
- 点「我已登录」：中枢 login_state 变 logged_in，App 状态变「已登录 ·
  待引擎接入」。此步不要求真的输入账号密码（真登录由账号本人日后
  做）；但导航判定通道要在报告中说明实测覆盖到哪一步、哪一步未实证。
- 老成员 absent（provider 为空）：详情页呈现不回退，登录按钮能打开
  muse 容器。
- 验收副本照旧纪律：关掉全部验收副本、只留生产进程；实物截图交
  调度员亲验，不许只报构建通过。
