# 第二步派单：数据副本验证 safeStorage 与账号会话（2026-11-17 深夜，调度员）

背景：第一步已关账（无头入口 + 空数据冒烟全过、核心零改动）。第二步是剥壳的决定性验证：在**数据副本**上、以 **Muse4AI 命名的应用上下文**运行无头引擎，验证（1）safeStorage 能解开 vault 与分区 cookie；（2）absent 账号会话在无头引擎里仍有效。**全程只读真实数据：不启动任何指向真实数据目录的进程，不向副本提交任务，不动现行应用与桥。**

## 0. 先清尾巴

删掉第一步的冒烟临时数据目录 `~/pack-engine-smoke-data`（若还在），回报里注明删了或本来就没有。

## 1. 做副本

- 先定位现行 Muse4AI 的 roaming 与 local 两个根的实际路径（以运行中进程打开的文件或目录结构为准，不要猜），回报里写明这两个源路径。
- 整树复制到 `~/pack-headless-verify/roaming` 与 `~/pack-headless-verify/local`；复制后逐树校验文件数与总字节数，与源一致（logs 子目录可排除，排除须注明）。源在运行中，-wal / -shm 原样一并复制；对副本的 muse.sqlite 跑一次 integrity_check 并记录结果。

## 2. 以 Muse4AI 之名运行

- 新建最小应用目录（如 `~/pack-headless-app/`）：package.json 的 name=muse4ai、productName=Muse4AI、version 与原应用一致（2.0.0-beta.35）、main 指向 headless.cjs；把 dist-headless/main/ 的 headless.cjs 与 gateway-page.js 放进去（复制或链接，注明哪种）。
- 用 `~/muse4ai/desktop` 的 electron 跑这个应用目录。环境：`PACK_ENGINE_ROAMING=~/pack-headless-verify/roaming`、`PACK_ENGINE_LOCAL=~/pack-headless-verify/local`、`PACK_ENGINE_PORT=47823`。
- **启动前先自检落点**：回报里写明本次运行实际生效的 userData、sessionData、roaming、local 四个路径；凡承载数据的路径必须全部落在副本目录内，**任一仍指向真实数据目录就停下报告、不许启动**。headless.ts 与 build-headless.mjs 是本项目新文件，为满足落点要求可以改；原应用文件仍零改动。
- 若启动时弹出钥匙串授权系统对话框：**停下报告，不要点**（等群主本人处理）。
- app.getName() 必须回报，本步应为 Muse4AI；不是就说明原因并停下。

## 3. 验证（只读）

- 用桥的 key 文件（`~/pack-runtime/muse4ai-bridge/.api-key`，只在本机读取使用，**不许把 key 写进回报、日志或仓库**）调副本引擎：GET `127.0.0.1:47823/v1/health` 与 `/v1/status`。
- 判定材料：status 里 absent 账号的登录/会话状态、额度是否可读；再对现行应用（47821）用同一 key 调一次 `/v1/status` 做对照，两边账号状态应一致。
- vault 侧证据：回报引擎日志里 vault 解密是否成功（如 secure 标志、dpapi 条目读取结果）；只报成败与条数，不报内容。
- 不提交任何任务、不调用写接口。状态含糊就如实说含糊、列出还缺哪种探针，等裁决，不要自行加测真实任务。

## 4. 收尾

- SIGTERM 关掉无头引擎；**副本目录保留**（第三步可能复用），回报里写明副本占用大小。
- 现行应用健康接口复核 200、桥仍在运行。

## 回报格式

源路径两条；副本校验数字（文件数 / 字节数 / integrity_check）；四个生效路径；app.getName()；health / status 结果与两边对照；vault 解密成败；钥匙串弹窗有无；偏离与原因。连败两次停下报告。
