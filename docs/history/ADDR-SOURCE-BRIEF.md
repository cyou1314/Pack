# ADDR-SOURCE-BRIEF：读取端切换到私有地址源（hub-url 挪仓方案 A · 读取端片）

## 背景
- 中枢地址分发已挪仓：私有仓 `cyou1314/pack-addr`，地址文件为仓根 `hub-url.txt`（内容与公开仓 `runtime/hub-url.txt` 同格式：完整 https 地址、含 /mcp 后缀、无尾随空白要求按各端现有 trim 逻辑）。
- VM 发布脚本已双发上线（提交 b7eb349）：公开仓与私有仓同步发布，两边内容一致。
- 读取端共三处，现行均为匿名拉公开仓 raw 文件：App（PackApp.swift 的 followPublishedHubURL）、Mac 运行时（runtime/runtime.mjs）、桥（runtime/muse4ai-bridge/bridge.mjs 与两份 pack-contracts.mjs）。
- 本片目标：三处改成**优先带只读令牌拉私有仓**，令牌缺失或拉取失败时**回退现行公开源**（过渡期保底）；切换验收（调度员在 VM 侧强制隧道轮换、三处跟随）在本片之后单独进行；停旧发布与删公开文件由调度员做，不在本片。

## 令牌与红线
- 令牌 = 群主新建的 fine-grained PAT（仅 pack-addr 仓、Contents 只读）。**令牌值由群主本人粘贴到三处本机位置，Codex 全程不经手、不读取、不打印、不写日志、不入仓**；Codex 只验「在/不在 + 长度区间 + 带令牌拉取返回 200（只报状态码）」。
- 三处存放：
  1. App：设置页新增一项「地址源令牌」（安全输入框），存钥匙串（service 沿用 App 现行凭证 service，account 用 `addr-token`）；留空 = 未配置。
  2. 运行时：`~/pack-runtime/config.json` 新增键 `addrToken`（文件本机 0600 沿用）。
  3. 桥：`~/pack-runtime/muse4ai-bridge/.addr_token`（纯文本一行、0600），缺文件 = 未配置。

## 改动要求
### App（PackApp.swift 地址发现段）
- 新增私有源拉取：`GET https://api.github.com/repos/cyou1314/pack-addr/contents/hub-url.txt`，请求头 `Authorization: Bearer <钥匙串令牌>`、`Accept: application/vnd.github.raw`、`X-GitHub-Api-Version: 2022-11-28`。
- 顺序：钥匙串有令牌 → 先拉私有源；成功且内容合法（现有校验：trim 后为合法 https URL 且与当前存储不同）才采纳；私有源失败/未配令牌 → 走现行公开源逻辑，一字不改其语义（60 秒节流、手动保存本会话优先、失败静默、采纳走现有保存路径）。
- 设置页加令牌项与一行小字（口径自拟、须说明：用于从私有仓获取中枢地址，可留空）。禁 @State、数值走 DesignTokens、不动 HubClient 接口面。
### 运行时（runtime/runtime.mjs）
- 地址发现改为：config 有 addrToken → 先拉私有源（同上 API 与请求头）；失败/无令牌 → 现行公开源逻辑不变。其余行为（重试节奏、落盘、重连）一字不改。
### 桥（bridge.mjs + 两份 pack-contracts.mjs）
- SOURCE 改为可切换：有 .addr_token → 拉私有源（同上）；无/失败 → 现行公开 SOURCE 不变。三份文件的取址逻辑保持一致（两份 pack-contracts.mjs 同步改）。

## 验收（本片内）
- 构建：App swift build 通过并打包；运行时/桥 node --check 通过。
- 令牌粘贴环节：三处就位后由群主本人粘贴（Codex 停下发提问卡等群主完成，不要代粘、不要看值）。
- 功能验（令牌已配状态下）：逐端制造一次「存储地址过期」场景（改本地存储为失效地址或断连触发发现），确认该端经**私有源**取回当前地址并恢复连接（证据：日志/行为能区分来源——私有源走 api.github.com；只报域名与状态码、不报令牌）。
- 回退验：临时移走一处的令牌（如桥的 .addr_token 改名），确认该端回退公开源仍能取址；验完恢复。
- 生产 App 换包；运行时/桥按现行方式重启生效；旧公开源此刻仍在双发，不许停。

## 纪律（照旧）
- 连败两次停下报告；构建通过才许报；报告固定格式（改了哪些文件 / 构建与部署 / 验收证据 / 差异自查 / 未实证项）；不碰 VM 侧任何东西；不发布 Release；令牌红线见上。
