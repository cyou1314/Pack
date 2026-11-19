# 原版应用终测结果

## A 段（2026-11-19，北京时间；遇弹窗暂停）

### 候选及运行时

- 原版带界面的 Muse4AI 应用目录：`/Users/cyou/muse4ai/desktop`，应用版本 `2.0.0-beta.35`；入口 `dist/main/main.cjs` 构建时间为 2026-11-18 21:05:57。
- 该目录原配 Electron：`/Users/cyou/muse4ai/desktop/node_modules/.pnpm/electron@45.2.1/node_modules/electron/dist/Electron.app`，版本 45.2.1，Cookie 加密 fuse 关闭。
- 现行加密版 Electron：`/Users/cyou/pack-headless-l2/Electron.app`，版本同为 45.2.1，Cookie 加密 fuse 开启。当前真实数据的只读计数：absent 的 Cookie 16 条中 3 条加密，l1-check-1119 的 12 条全部加密。为避免关闭 fuse 的运行时损伤现有登录态，群主明确批准使用此加密版运行时启动上述原版界面构建；这是与原配运行时的差异。
- `/Applications/Muse.app` 是 `com.meta.endo`、版本 12.1 的另一款应用，不是上述 Muse4AI 构建，未启动。

### 已执行

- 约 21:44，47821 由无头进程 PID 41228 监听，health HTTP 200；桥 PID 60515 在运行。
- 正常终止无头进程后，确认 PID 41228 已退出且 47821 无监听；没有停止或重启桥。
- 通过 Launch Services 启动加密版 Electron，应用参数指向原版目录 `/Users/cyou/muse4ai/desktop`。界面应用 PID 63874 监听 127.0.0.1:47821，health HTTP 200；桥 PID 60515 未变。
- 21:48，群主反馈启动后出现钥匙串或系统安全类弹窗。弹窗的具体类型、原文与原因尚未核实。

### 暂停点与未完成

按简报红线，遇弹窗立即停止：未点击或处理弹窗，未点击应用界面、Continue，未提交任务。界面应用保持打开；弹窗之后未再读取账号状态或日志。A 段的 `/v1/status`、两账号当前状态及最近检查的 `vm_state` / 错误分类尚未记录，等待群主处理弹窗并发话后继续。C 段尚未执行。
