# 第一步派单：Muse4AI 无头入口 + 构建改造（2026-11-17 深夜，调度员）

背景：剥壳嵌入路线已由群主拍板；可拆性评估正本见仓库外文件 CORE-EXTRACTION-ASSESSMENT.md（调度员自做，结论：换入口不动核心——引擎闭包 88/150 文件、组装全靠依赖注入、Electron 运行时耦合仅 accounts.ts 与 extraPages.ts）。本单只做第一步：写无头入口、配构建、用全新空数据目录冒烟。**不碰现行 Muse4AI 进程、dist 与真实数据目录；不做数据副本验证（第二步）；不切桥。**

## 交付物（全部新增，不改任何现有文件）

1. `~/muse4ai/desktop/src/main/headless.ts` —— 无头入口。
2. `~/muse4ai/desktop/scripts/build-headless.mjs` —— 构建脚本：照 build-main.mjs 改，入口换 headless.ts，输出 `dist-headless/main/headless.cjs`；gateway-page 照旧打到 `dist-headless/main/gateway-page.js`；不打 preload、不跑 vite。
3. 构建产物 `dist-headless/`（headless.cjs + gateway-page.js）。

**核心零改动纪律**：现有文件（含 main.ts 与 88 个核心文件）一律不改。若发现必须改现有文件才能跑通，停下报告，不要改。

## headless.ts 的组装（照 main.ts 现行写法，只取引擎部分）

- 路径：app.setPath("userData", appData/MuseStudio) 与 makePaths 的根计算照抄 main.ts（约 79–150 行）；另加环境变量覆盖：`PACK_ENGINE_ROAMING` / `PACK_ENGINE_LOCAL` 给了就用给定的根（第二步副本验证要用）；`PACK_ENGINE_PORT` 给了就用它作 API 端口（覆盖设置值）。
- 依次组装：Logger → openDatabase → Repo → Vault（safeStorage Cipher 照 main.ts 约 274 行的注入法）→ MediaStore → AccountManager（含 channelFor：ElectronHttpChannel + 读同目录 gateway-page.js，照 main.ts 约 437 行）→ Coordinator（ui 端口传 accounts 本人，adapterFor / workAdapterFor / releaseUiWork / testProxy 照抄非 TEST 分支）→ Services（os 端口给无头实现：trashItem / showItemInFolder / openPath / copyFileToClipboard 一律安全空实现并记日志）→ ApiController（notify 转 logger、emit 转内部 EventEmitter、subscribeJobs 照 main.ts 的 jobBus 法、tunnel.fetch 用 net.fetch、binDir 照旧）→ api.start()。
- 砍掉：一切窗口创建、Tray / Menu / Notification / toast、ipcHandlers、updater、protocols、skillInstall、prewarm 的 UI 部分。若组装段里另有引擎必需的初始化（如 sweepOrphanProfiles），保留并在回报里注明。
- 进程行为：启动后常驻（API 在监听即就绪）；SIGTERM / SIGINT 时优雅退出（关 API、关 db）；不创建任何可见窗口、不抢焦点；能 hide Dock 就 hide。

## 冒烟（全新空数据，不碰真实数据）

- 环境：`PACK_ENGINE_ROAMING` 与 `PACK_ENGINE_LOCAL` 指向新建空目录（如 `~/pack-engine-smoke-data/roaming` 与 `~/pack-engine-smoke-data/local`），`PACK_ENGINE_PORT=47822`（避开现行 47821）。
- 用 `~/muse4ai/desktop` 的 electron 跑 `dist-headless/main/headless.cjs`（直接以文件为入口即可；本步不要求 app 名正确）。
- 通过标准：进程起来后至少 30 秒不退出；`GET http://127.0.0.1:47822/v1/health` 返回 200 且 ok；`GET /v1/status` 无 key 返回 401（证明鉴权层活着）；无可见窗口出现。冒烟后关掉进程、删临时数据目录。
- 若单实例锁等机制阻止与现行应用并存，停下报告；不要改核心、不要杀现行进程。

## 回报格式

新增文件清单与各自字节数；headless.cjs 与 gateway-page.js 字节数；冒烟时 app.getName() 的实际值；冒烟四项结果逐项（进程存活 / health / status 401 / 无窗口）；「核心零改动」是否守住（~/muse4ai 的 git status 应只有新增文件）；偏离与原因。连败两次停下报告。
