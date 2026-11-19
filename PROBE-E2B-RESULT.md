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

A 段已完成，界面应用保持打开。未点击应用界面、Continue，未提交任务；没有停止或重启桥。C 段未执行，等待调度员明确发话。
