# MAC-WORK-BRIEF：Mac 侧四件施工（调度员派单，2026-11-19 深夜）

执行者：Mac 上 Codex。纪律：每件独立验收、独立回报；同层连败两次停下写明原因等调度员裁决；不许动 VM 中枢、不许动生产 App 源码（件四只同步 runtime/）；令牌/token 一律不打印、不入仓、不进日志正文。全部做完后把结果写 `MAC-WORK-RESULT.md` 入仓（逐件：做了什么、验收证据、未实证项）。

## 件一：运行时接 member_quota_report（额度自报上报）

背景：中枢已上线新工具 `member_quota_report`（staging48，生产工具数 65）：成员凭自己的 token 自报本人额度，无 member_name 入参；服务端有防伪造四规则（同周期只升不降、reset 只许后移、调度员 set 权威、跳涨 >30 点无交活写 anomaly 审计），运行时只管如实上报。

要做（`runtime/`，落点 `~/pack-runtime/`）：
- 在 ego 运行时里加额度采集：优先拦截成员服务方网页会话中用量接口的 XHR/fetch JSON 响应解析（抗改版优先），拿不到再退回页面 DOM 读取；解析出 week_used_pct、week_reset_at（epoch 秒）、extra_tokens_left。
- 上报时机：每次任务完成/闲聊回复后搭便车读一次并调 member_quota_report；距上次成功上报 >10–12 分钟且当前无任务在跑时，轻量补采一次。
- 读失败/解析失败：不上报（宁可让旧数据过期转灰），绝不回填 0 或猜测值；失败只记本地日志一行。
- 验收：本地测试（runtime/tests 新增解析与时机用例）全过；部署后由调度员在中枢侧核对 absent 的 quota_source=self 且数值与账号页一致。

## 件二：rpc 长连接改造

背景：`runtime.mjs` 两处（约 :46 与 :138）写死 `Connection: "close"`，每次请求新建 TCP/TLS，是隧道请求量大头之一。

要做：
- 去掉两处 Connection: close，走 undici 默认长连接复用。
- socket 复连类错误（ECONNRESET、UND_ERR_SOCKET、socket hang up 等）必须归入既有 hubNetworkFailure 重试路径（带地址重发现），不得误判为业务失败；业务错误（4xx/工具返回 error）维持原口径不重试。
- 验收：runtime/tests 全过 + 新增用例覆盖「服务端主动断开长连接后下一请求自动重连成功」；部署后观察 runtime.log 一轮轮询无新增连接错误。

## 件三：无头引擎 launchd 守护

背景：桥（com.muse.pack-muse4ai-bridge）与运行时（com.muse.pack-runtime）已在 launchd 下；无头引擎仍是 nohup 裸跑（现行产物在 /Users/cyou/pack-headless-l2，health 127.0.0.1:47821）。

要做：
- 先 `ps` 读现行引擎进程的完整启动命令与工作目录，原样复刻进 LaunchAgent plist（~/Library/LaunchAgents/，标签 com.muse.pack-headless-engine），KeepAlive=true、ThrottleInterval=10、stdout/stderr 落现有 engine.log 位置（或同目录新日志文件，写明）。
- 切换顺序：停 nohup 进程 → 确认 47821 释放 → launchctl load 新 agent → health 200 → 桥日志出现重连成功（hub connected / heartbeat ok）。切换期间不动数据目录、不动 fuse 运行时、不碰 L2 备份链。
- 验收：kill 引擎进程一次，launchd 自动拉起且 health 恢复；桥随之重连；absent/l1-check 两账号仍在引擎登记内（只读核对）。

## 件四：闲聊回路部署重试

背景：闲聊回路代码已在仓库 runtime/（提交 278cf6a，node --check 与 runtime/tests 6 项、五种模拟回归全过）；上次部署在「部署前直连中枢」一步撞 ConnectError / RemoteProtocolError 两连败停工，**未复制任何文件、未重启 launchd**，生产运行时未受影响。

要做：
- 先探活：用运行时现有发现路径连中枢，确认心跳与工具列表正常（65 工具）再动手；若仍连续两败，停工回报、不许硬上。
- 同步仓库 runtime/ → ~/pack-runtime/（保留 Mac 本地 config.json，含 addrToken，勿覆盖），launchctl 重启 com.muse.pack-runtime。
- 验收：runtime.log 显示新版启动且心跳正常；调度员随后在房间发一条无 @ 普通消息做活体验收（absent 接话 + 调度员钩子兜底），此步等调度员配合、不在本单内自验。

## 排期

件二、件一同属 runtime/ 改动，一次施工一次部署；件三独立；件四最后（依赖件一/件二部署后的运行时）。每件完成在 MAC-WORK-RESULT.md 追加一段再继续下一件。
