# 原版应用终测结果

## A 段（2026-11-19，北京时间；已完成）

### 候选及运行时

- 原版带界面的 Muse4AI 应用目录：`/Users/cyou/muse4ai/desktop`，应用版本 `2.0.0-beta.35`；入口 `dist/main/main.cjs` 构建时间为 2026-11-18 21:05:57。
- 该目录原配 Electron：`/Users/cyou/muse4ai/desktop/node_modules/.pnpm/electron@45.2.1/node_modules/electron/dist/Electron.app`，版本 45.2.1，Cookie 加密 fuse 关闭。
- 现行加密版 Electron：`/Users/cyou/pack-headless-l2/Electron.app`，版本同为 45.2.1，Cookie 加密 fuse 开启。当前真实数据的只读计数：absent 的 Cookie 16 条中 3 条加密，l1-check-1119 的 12 条全部加密。为避免关闭 fuse 的运行时损伤现有登录态，群主明确批准使用此加密版运行时启动上述原版界面构建；这是与原配运行时的差异。
- `/Applications/Muse.app` 是 `com.meta.endo`、版本 12.1 的另一款应用，不是上述 Muse4AI 构建，未启动。

### 已执行

- 约 21:44，47821 由无头进程 PID 41228 监听，health HTTP 200；桥 PID 60515 在运行。
- 正常终止无头进程后，确认 PID 41228 已退出且 47821 无监听；没有停止或重启桥。
- 通过 Launch Services 启动加密版 Electron，应用参数指向原版目录 `/Users/cyou/muse4ai/desktop`。界面应用 PID 63874 监听 127.0.0.1:47821，health HTTP 200；桥 PID 60515 未变。
- 21:48，群主最初反馈出现弹窗，按红线暂停；随后更正“没有弹窗说错了”，于是继续 A 段只读核对。没有点击或处理弹窗。
- 21:50 前后确认 PID 63874 仍监听 127.0.0.1:47821，health HTTP 200；打开文件路径显示使用 `~/Library/Application Support/MuseStudio/local/profiles`，桥 PID 60515 未变。

### 状态与会话日志（只读）

21:51:04 调用 `/v1/status`：absent 和 l1-check-1119 的 API 原文分组均为 `waiting`；读取本地账号状态原文，两者均为 `cooling_down`。API 的 `waiting_until` 分别为 21:52:36 和 21:52:33（北京时间）。

| 账号 | 最近失败日志（北京时间） | 错误分类原文 | 最近后台续查日志（北京时间） |
| --- | --- | --- | --- |
| absent | 21:50:36 | `PRE_SUBMIT_TRANSIENT`，`countsAsGeneric=false` | 21:51:33，HTTP 200，`status=assigned`，`vm_state=UNKNOWN` |
| l1-check-1119 | 21:50:33 | `PRE_SUBMIT_TRANSIENT`，`countsAsGeneric=false` | 21:51:33，HTTP 200，`status=assigned`，`vm_state=UNKNOWN` |

两条失败日志的错误消息原文均为 `Máy ảo Muse chưa khởi động xong`，来源为 `timed-state-expired`。日志续查时间晚于状态读取时间；不应把两者视作完全同时的采样。

### 交接

A 段已完成，界面应用保持打开。未点击应用界面、Continue，未提交任务；没有停止或重启桥。当时 C 段尚未执行，后续恢复记录见下方。

## 界面段（2026-11-19 21:56–21:58，调度员经设备操控 + 群主亲手点击）

- 额度新鲜读数（原版应用 21:48/21:49 自行刷新，原 stale 快照作废）：absent 周已用 19%、25/11 重置，附加 token 剩 370M（已用 63%、不过期）；l1-check-1119 周已用 1%、26/11 重置，附加 1B 未用。
- absent 的 View screen 显示活的 Muse 网页会话画面（每秒更新），账号网页会话在应用内是活的。
- 队列：10 单，1 waiting——即 job b33ecf11（账号 l1-check-1119，15:13 创建，API · pack-bridge），详情为 Waiting / No attempts yet。
- 群主在详情面板亲手点击 Prioritize：约 1 分钟后复查，该 job 仍为 Waiting、Attempts 仍为 No attempts yet；两账号转为新一轮 Cooling down until 21:57（点击前后各发生一次会话检查失败，分类与此前一致）。Prioritize 未能使任何账号进入可派发状态。
- 未再新建 Studio 任务：新单同样要过账号可用性闸门，等待单本身即是证明样本，再建一单只会多一条 Waiting。

## E2 终测结论（调度员，2026-11-19）

官方壳的全部路径均已实测：网页聊天可正常回复、原版应用可刷新额度、可看活会话画面；但两账号的机器状态恒为 assigned / UNKNOWN，会话检查恒 PRE_SUBMIT_TRANSIENT，任务派发零尝试；此前改判定放行后的真实连接为 HTTP_CONNECT_FAILED。**E2 最后一公里卡在 Muse 服务侧机器不可达，非 Pack / 桥 / 引擎 / 原版应用本地问题。E2 就此冻结为 v1.0 已知限制**；账号侧恢复后，用 probe_card3.py（正文带 [M4A]）重发文本+图卡即可补验桥路。


## C 段（2026-11-19，北京时间；已完成）

- 通过系统正常退出界面应用（原 PID 63874），确认进程退出、127.0.0.1:47821 端口释放。
- 按现行 nohup 方式恢复无头构建：`/Users/cyou/pack-headless-l2/Electron.app/Contents/MacOS/Electron /Users/cyou/pack-headless-l2/app`，使用原 MuseStudio roaming/local 路径及 47821 端口；未开启副本验证模式。无头进程 PID 65409，health HTTP 200，日志 `/Users/cyou/pack-headless-l2/engine.log`。
- 原桥进程在无头恢复后仍运行，使用桥凭证只读访问 `/v1/status` 返回 HTTP 200、scope 为 `generate`；但空闲桥无新的引擎握手日志，不能仅凭这项代理检查声称桥自身已重连。确认没有活动任务后重启桥一次；新 PID 65514，22:02:17 日志依次记录 `hub connected`、`hub heartbeat ok`、`bridge up: absent; Muse4AI authenticated`，证实桥进程已连接并认证恢复后的引擎。
- 未修改代码、未手动修改账号数据或删除文件，未提交任务；界面应用没有再次启动。
