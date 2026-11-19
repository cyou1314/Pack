# L3 简报：过期重登闭环 + 删成员清分区（登录容器最后一片）

2026-11-19。承接 LOGIN-CONTAINER-DESIGN.md 切片定义与 L1（1286f3d）、
L2（6617b42）关账状态。L2 关账口径：生产引擎已翻 cookieEncryption
fuse、交接产品路径真机实证（15:10 已加密交接）、实卡 job 在队列中
等账号可调度——服务侧 vm_state=UNKNOWN 为与 E2 共享的外部阻塞，
不算 L2/L3 未完成；该 job 后续自行成败均无需处理。

## 现状锚点（开工前自行核对）

- 中枢 login_state 枚举 pending / logged_in / expired 已就位
  （staging47），写回工具 member_login_state_set 为调度员专属，
  App 以调度员身份调用（L1 已用此路写 logged_in）。
- L1：登录容器 + 分区（WKWebsiteDataStore 按成员名稳定派生标识，
  映射存 UserDefaults "pack.memberWebStores"）+ 导航完成判定 +
  「成员本人登录 / 重新登录」按钮已在。
- L2：交接助手 socket 在 ~/.pack/l2-ipc/handoff.sock，只传成员名 +
  随机钥匙串票据；助手验加密落盘、跑会话检查、如实返回状态。
- 删成员现行行为：App 调 member_remove（中枢软删）即完事，
  本地 WebView 分区与引擎侧账号/分区都还留着——本片补齐。

## 第 1 步：过期检测与重登闭环

1. 助手协议加一个只读查询：App 传成员名，助手读引擎**当前**账号
   状态回给 App（只读现状，不新触发会话检查——引擎自己的周期
   检查一直在跑）。回值至少区分：无此账号 / needs_login（会话
   已失效）/ 其他现有状态原文。
2. App 成员详情（provider 成员且已交接过）加显式动作
   「检查引擎会话状态」：点后调上述查询并呈现结果；若为
   needs_login，App 调 member_login_state_set(name, expired)，
   详情状态行转「登录已失效 · 待重新登录」。
3. 重登闭环：失效后成员点「成员本人登录 / 重新登录」（复用同一
   分区、不清数据）重新登录；L1 的完成判定须对老分区同样触发，
   成功后写回 logged_in，会员可再次点交接。核对 L1 判定在
   「已有分区的二次登录」场景确实会触发，不会就修。
4. 交接时助手回 needs_login 的现行路径同样联动 expired 写回
   （若 L2 已有此联动，核对留证即可，不重复造）。

## 第 2 步：删成员清分区

成员移除确认通过后（现行二次确认保留），App 在调 member_remove
之外依次执行并逐项记录结果（失败项如实呈现、不假装清干净）：
1. 删该成员 WKWebsiteDataStore 全部数据，并清 UserDefaults 里
   该成员的分区标识映射；
2. 经助手删引擎侧该成员账号及其 persist 分区（只许按成员名
   精确命中；查无此账号算正常结果，不是失败）；
3. 中枢 member_remove 照旧最后执行或先行均可，但三项结果要在
   移除完成呈现里逐项可见。
红线：绝不许误伤其他成员——absent（无 provider、非容器成员）
与 l1-check-1119 的分区/账号在本片验收前不许动。

## 验收

1. 过期闭环演练：对 l1-check-1119 点「检查引擎会话状态」，
   呈现与引擎当时实际状态一致（以引擎侧读数为准留证）；expired
   写回路径用可控方式验一次（例如对未交接过的 provider 成员
   查询应回「无此账号」而非 expired——口径边界要分清：无账号
   不等于过期）。真 expired 的完整重登闭环若当场无法构造失效
   会话，列为未实证项如实标注，不许假验。
2. 清分区演练：新建一次性成员（provider=muse）→ 打开一次登录
   容器（分区生成）→ 移除该成员 → 逐项核验：分区数据已删、
   映射已清、引擎侧无其账号、中枢成员已移除；且 l1-check-1119
   与 absent 的分区/账号/状态完好如初。
3. 构建通过、生产包换新、提交推仓并报 commit 号；报告按固定
   格式（改了哪些文件 / 构建与打包 / 差异自查 / 未实证项）。

## 红线与纪律（照旧）

- cookie 只走钥匙串票据、永不进中枢/房间/日志/git/聊天；
  状态查询只回状态枚举与计数，不回任何 cookie 内容。
- 不碰 absent 既有账号与桥；不动引擎 fuse 与加密运行时
  （回滚口径见 runtime/l2-handoff/README.md）。
- 禁 @State、数值只走 DesignTokens、不动 HubClient 接口面
  （member_login_state_set / member_remove 均现成，不算改面）、
  构建通过才许报、同层连败两次停下报告。
