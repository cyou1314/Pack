# Muse4AI 内部机制分析报告（Pack 适配专项）

> 分析对象：本机 `/Users/cyou/muse4ai/desktop/src/`（完整 TypeScript 开源源码，非黑盒二进制）与 `/Users/cyou/pack-headless-l2/engine.log` 现场日志。
>
>
>
> 结论口径：全文严格区分 ****（直接来自已读取源码行号或现场日志）与 ****（受限于连接中断前尚未读到的 `coordinator.ts`、`httpChannel.ts`、`accounts.ts` 局部代码）。
>
>
>
> 红线遵守情况：全程只读分析，未改动生产引擎 `/Users/cyou/pack-headless-l2`、其数据目录或回滚备份 `/Users/cyou/pack-headless-l2-preflip-20261119`。

## 0. 关键突破摘要（优先处理项）

1. **账号冻结（`paused`）且无头无法恢复的根因**
- `desktop/src/shared/accountMachine.ts:44-52`：`onGenericFailure()` 在 **15 分钟内累计 3 次通用失败** 后，直接把账号置为 `state: "paused"`、`stateUntil: null`。
- `desktop/src/shared/accountMachine.ts:60-62`：`isExpiredTimedState()` 仅自动解除 `cooling_down`（120s）与 `limit_reached`，**`paused` 永远不会自动到期恢复**。
- `desktop/src/main/api/server.ts:81-96` 与 `desktop/src/main/api/mcp.ts:207-289`：**47821 端口没有任何账号恢复（resume/unpause）或手动触发 `checkSession` 的外部接口**。
- **与 `vm_state=UNKNOWN` 的连锁关系**：`driver.ts:213` 收到 `vm_state === "UNKNOWN"` 且存在 `vm_id` 时会进入 90 秒 `wake()` 轮询，超时抛 `PRE_SUBMIT_TRANSIENT`；15 分钟内连续 3 次超时即把账号打入永久 `paused`。
2. **额度读取已有现成本地接口，Pack 运行时无需自己爬网页**
- `desktop/src/main/api/service.ts:44-63, 140-170`：`GET http://127.0.0.1:47821/v1/status`（以及 MCP 工具 `muse_status`，`read` 权限 Bearer Key 即可）**直接返回每个账号的 `quota` 对象**（含周已用百分比、周重置 ISO 时间、附加 token 余量/百分比/过期说明、观测时间、是否过期 `stale`）。
- **待补验**：需再核对 `coordinator.ts`，确认无头模式下是否会自动触发 `driver.readQuota()` 刷新该缓存。

## 1. 机器可达性、会话检查与加密网关通信

### 1.1 会话检查与唤醒流程

Muse4AI 存在两条独立的会话/唤醒路径，共用同一组 `muse.ai` 端点，但在**请求体字段**与**状态判定口径**上存在细微差异：

| 项目 | 路径 A：UI / 会话检查（`adapter/muse/driver.ts`） | 路径 B：直连加密网关（`shared/gateway/client.ts`） |
| --- | --- | --- |
| 执行上下文 | 账号主页面 `https://muse.ai/`（`webContents` 内 `fetch`） | 隐藏轻页 `https://muse.ai/robots.txt`（`page/gatewayPage.ts:5-8`） |
| 会话查询端点 | `GET https://muse.ai/api/session?vm_state=live`（`driver.ts:191-199`） | `GET /api/session?vm_state=live`（补全 `https://muse.ai`）（`client.ts:116`） |
| 查询请求头/凭证 | `credentials: "include"`, `redirect: "manual"`, `x-app-id: hatch-web` | `credentials: "include"`, `x-app-id: hatch-web` |
| 状态就绪判定 | 严格区分大小写：`vm_state === "RUNNING"` 或 `"DISABLED"` 跳过唤醒（`driver.ts:213`） | 宽松正则：`vmRunning(state)` 放行缺失/空串/`/^(running\|live\|ready)$/i`（`client.ts:42-44`） |
| 唤醒端点 | `POST https://muse.ai/api/hatch/vm/wake`（`driver.ts:221-228`） | `POST /api/hatch/vm/wake`（`client.ts:137`） |
| 唤醒请求头 | `content-type: application/json`（**不带 `x-app-id`**） | `content-type: application/json`（**不带 `x-app-id`**） |
| 唤醒请求体 | `{ vm_id, retry_count, connect_attempt_id: randomUUID()}` | `{ vm_id, retry_count}`（**无 `connect_attempt_id`**） |
| 轮询时序 | 总超时 `90s`（`TIMEOUTS.wakePoll`），每 `10s` 重发 wake，每 `2s` 重查 `/api/session?vm_state=live`；超时后由 `WakeDiagnostic` 在后台继续每 2s 采样至 90s（`wakeDiagnostic.ts:25-45`） | 总超时 `90s`（`VM_WAKE_WAIT_MS`），每 `10s` 重发 wake，每 `2s` 重查 `/api/session?vm_state=live` |

### 1.2 加密网关握手与传输协议

源码位置：`desktop/src/shared/gateway/client.ts:148-340`、`desktop/src/shared/gateway/noise.ts:1-162`。

1. **换取连接凭证**：
- `POST https://muse.ai/api/hatch/token`（头：`content-type: application/json`, `x-app-id: hatch-web`；体：`{ vmAddress: endpoint_url, vmName: vm_id}`）→ 取得 `token`（及可选 `notary_token`）。
- `POST https://muse.ai/api/hatch/noise-notary-token`（同请求头；体：`{ vmName: vm_id}`）→ 可选刷新 `notaryToken`（失败静默跳过）。
2. **建立 WebSocket**：
- 端点：`wss://hatch.metaaivm.com/v1/noise`
- Query 参数：`vm_id`、`auth_token`、`notary_token`（若有）、`app_id=hatch-web`、`request_id=<uuid>`；`binaryType = "arraybuffer"`，建连超时 30s。
- 若 WebSocket 建连失败（`step: "socket"`），`GatewayClient.connect()` 会以 `forceWake = true` 强制走一遍 `wake()` 后重试一次（`client.ts:106-113`）。
3. **Noise XX 加密握手（`Noise_XX_25519_AESGCM_SHA256`）**：
- 基于 WebCrypto X25519 + AES-GCM + SHA-256，空 prologue。
- **msg1（Client → Server）**：`e.pub (32B)` + `encryptAndHash(fieldBytes(1, randomBytes(32)))`。
- **msg2（Server → Client，30s 超时）**：`re (32B)` + `enc_rs (48B)` + `enc_payload2`。若 `payload2` 解析出 challenge（字段 1 存在、`kind === 1 || kind === 2`、字段 3 为 32 字节），客户端直接中止并抛 `GatewayError(connect_failed, step: "challenge")`。
- **msg3（Client → Server）**：`encryptAndHash(s.pub)` + `encryptAndHash(empty)`；随后 `split()` 得到独立的 `send` / `recv` AES-GCM `CipherState`（12 字节 IV，后 8 字节大端递增 nonce）。
4. **隧道内多路复用协议**：
- 每条应用消息按 `MAX_FRAME_PAYLOAD = 65_489` 字节分片，封装为自定义 Protobuf 帧（`cid`, `idx`, `total`, `payload`）加密发送。
- 内部承载虚拟 HTTP 请求（默认附带头 `x-app-id: hatch-web`、`accept-encoding: identity`），核心内部端点：
- `POST /chat/stream`（发送消息，NDJSON ack）
- `POST /chat/subscribe`（订阅会话事件流，NDJSON）
- `GET /chat/history?session_id=...&limit=...`
- `GET /api/session/list?limit=...`（`probe()` 健康检查也打此端点，`limit=1`）
- `POST /fs/{op}`（工作区文件上传，仅限 `workspace/user/files/`）
- `GET /fs/raw{path}`（分块下载结果文件，单块 1 MiB）

### 1.3 `vm_state` 来源与一直为 `UNKNOWN` 的原因分析

- **无本地缓存**：`GatewayClient.readSession()` 与 `MuseDriver.sessionInfo()` 每次均实时请求 `GET /api/session?vm_state=live`，客户端代码**没有对 `vm_state` 做任何本地缓存**；`UNKNOWN` 是 `muse.ai` 服务端在该次 HTTP 200 JSON 中直接返回的值（且 `wakeDiagnostic.ts:5` 白名单已显式收录 `"UNKNOWN"`）。
- **客户端两侧处理口径**：`"UNKNOWN"` 既不满足 `driver.ts:213` 的 `"RUNNING"`，也不满足 `gateway/client.ts:43` 的 `vmRunning()`，因此两侧都会立即进入 90 秒 `wake()` 轮询。
- **现网日志异常**：`/Users/cyou/pack-headless-l2/engine.log` 在 2026-11-20 03:40–08:52 持续报错 `ssl_client_socket_impl.cc:949 handshake failed; returned -1, SSL error code 1, net_error -100` 及 `Failed to load URL: https://muse.ai/ with error: ERR_CONNECTION_CLOSED`，表明无头引擎到 `muse.ai` 的代理/TLS 链路本身处于高频断连状态。
- **为什么请求级对齐后仍返回 `UNKNOWN`**：
1. **页面上下文差异**：网关通道注入在 `https://muse.ai/robots.txt`（轻页），并未加载 Muse 主前端应用与 hydration 流程；若服务端依赖完整前端会话或特定风控信号才真正拉起/报告 VM 状态，轻页或裸 `session.fetch` 可能持续拿到 `UNKNOWN`。
2. **代理/TLS 中断**：无头引擎出口代理频繁 TLS 握手失败，可能导致 `/api/hatch/vm/wake` 静默返回 `0`（两处代码都 `.catch(() => null)` / `return 0`，不会中断循环，只会在 90 秒后报超时）。
3. **待补验文件**：需在连接恢复后补读 `desktop/src/main/accounts.ts`（`sessionFor(accountId).fetch`）与 `desktop/src/main/adapter/muse/httpChannel.ts`，确认主进程侧是否另有缓存或退避状态机。

## 2. 额度读取机制与本地现成入口

### 2.1 Muse4AI 内部如何读取额度

源码位置：`desktop/src/main/adapter/muse/driver.ts:433-510`、`desktop/src/main/adapter/muse/selectors.ts:41-81, 210-226`。

- **不是 HTTP API，是 DOM 解析**：Muse4AI 没有任何调用 `muse.ai` 额度 JSON 接口的代码。
- **读取步骤**：
1. 在 `https://muse.ai/` 页面按两下 `Escape` 清菜单，点击 `[data-testid="hatch-dock-more"]`（或 `aria-label` 为 `Settings` / `設定` / `设置` 的按钮）。
2. 点击弹出菜单中的 `Settings` / `設定` / `设置` 菜单项。
3. 轮询读取 `[role=dialog],[aria-modal=true]` 的 `innerText`，交由 `parseQuota(text, now)` 解析 `Usage/用量` 到 `Language/语言` 之间的文本，提取：
- 周额度：`plan`、`usedPercent`（0–100）、`resetsOn` 及推算的 `resetAt`（epoch 毫秒）
- 附加 token：`Additional tokens` / `额外代币` 段落中的 `usedPercent`、`remainingLabel`（如 `5B tokens left`）、`remainingApprox`、`expiryLabel`（如 `Never expires`）
4. 首次读到 `usedPercent` 后会再等 `600ms`（`TIMEOUTS.quotaSettle`）重读一次，防止附加 token 晚渲染导致误判为 `additional: null`。

### 2.2 Pack 运行时可直接使用的本地入口

源码位置：`desktop/src/main/api/service.ts:44-63, 140-170`。

- **入口**：`GET http://127.0.0.1:47821/v1/status`（或 MCP 工具 `muse_status`），Header 携带 `Authorization: Bearer msk_...`（`read` 权限即可）。
- **返回结构（`accounts.items[]`）**：

```
{
"alias": "absent",
"group": "ready",
"waiting_until": null,
"quota": {
"plan": "Free plan",
"weekly_used_percent": 51,
"weekly_resets_label": "Nov 2",
"weekly_resets_at": "2026-11-02T00:00:00.000Z",
"additional_remaining_label": "5B tokens left",
"additional_used_percent": 0,
"additional_expiry_label": "Never expires",
"observed_at": "2026-11-19T12:00:00.000Z",
"stale": false,
"tier": "ok"
}
}
```

- **与 Pack 中枢 `member_quota_set` 的直接映射**：
- `weekly_used_percent` → `week_used_pct`
- `weekly_resets_at`（转为 epoch 秒）→ `week_reset_at`
- `additional_remaining_label`（需解析近似数值，或直接读 `quota` 缓存）→ `extra_tokens_left`
- ****：`GET /v1/status` 只返回内存/数据库里**已缓存**的最近一次额度快照（`service.ts:41` 明确注释 `never a read`），超过 6 小时标 `stale: true`。需补读 `coordinator.ts` 确认无头模式下 `driver.readQuota()` 是否按次/定时自动触发。

## 3. 本地 47821 端口接口全貌与队列调度逻辑

### 3.1 47821 端口完整 HTTP 路由清单

源码位置：`desktop/src/main/api/server.ts:81-96`。

| 路由 | 方法 | 鉴权要求 | 作用域/限流桶 | 用途 |
| --- | --- | --- | --- | --- |
| `/v1/health` | `GET`, `HEAD` | 无 | 无 | 存活检查，返回 `{ ok, service, instance}` |
| `/v1/openapi.json` | `GET` | 无 | 无 | OpenAPI 3 完整契约文档 |
| `/v1/status` | `GET` | Bearer Key | `read` | 服务版本、能力开关、**账号状态与额度快照**、直连桥状态、限额说明 |
| `/v1/jobs` | `GET` | Bearer Key | `read` | 分页列当前 Key 的任务（`?status=&limit=&cursor=`） |
| `/v1/jobs` | `POST` | Bearer Key | `generate` / `create` | 提交生成或 `kind: "chat"` 任务（支持 `Idempotency-Key`） |
| `/v1/jobs/{id}` | `GET` | Bearer Key | `read` | 查单个任务（支持 `?wait=` 长轮询与 `?after_version=`） |
| `/v1/jobs/{id}/cancel` | `POST` | Bearer Key | `generate` / `read` | 取消任务（`needs_review` 与终态不可取消） |
| `/v1/references` | `POST` | Bearer Key | `generate` / `upload` | 上传图片/视频/文档参考文件（≤20 MB） |
| `/v1/assets/{id}/content` | `GET`, `HEAD` | Key 或签名链接 | `read` | 下载产物文件（支持 `ETag` 与单段 `Range`） |
| `/v1/assets/{id}/extend` | `POST` | Bearer Key | `generate` / `create` | 基于已有视频资产发起延长链 |
| `/v1/conversations/{id}` | `GET` | Bearer Key | `read` | 查对话轮次或视频对话头状态 |
| `/v1/conversations/{id}/extend` | `POST` | Bearer Key | `generate` / `create` | 基于对话最新视频发起延长链 |
| `/v1/extend-chains/{id}` | `GET` | Bearer Key | `read` | 查延长链进度（支持 `?wait=` 长轮询） |
| `/v1/extend-chains/{id}/cancel` | `POST` | Bearer Key | `generate` / `read` | 停止延长链 |

**MCP 工具清单（8 个，`desktop/src/main/api/mcp.ts:207-289`）**：`muse_status`、`muse_generate`、`muse_chat`、`muse_get_job`、`muse_list_jobs`、`muse_cancel_job`、`muse_extend_video`、`muse_get_extend`。另提供 1 个只读资源 `GUIDE_RESOURCE`。

**账号恢复入口缺失**：47821 端口的 REST 与 MCP 接口**均不提供**任何账号管理能力（无 `resume`/`unpause`、无 `checkSession`、无 `readQuota`、无账号增删改）。若需无头恢复 `paused` 账号，必须走以下方案之一：

- 在 Pack 的无头扩展（`pack-repo/runtime/l2-handoff/engine.cjs` 的本地 Unix Socket `handoff.sock`）中新增 `resume` / `check` 操作，直接调用 `deps.coord`；
- 或在无头构建中调整 `coordinator` 对 `paused` 的策略 / 暴露仅限回环的恢复入口。

### 3.2 任务队列调度判定逻辑

源码位置：`desktop/src/shared/scheduler.ts:74-171`、`desktop/src/shared/accountMachine.ts:34-62`。

1. **任务出队顺序**：`sortQueue()` 按 `priority` 降序 → `seq`（创建序号）升序逐个尝试分配；无法分配的任务留在队列等待，**不占用全局槽位**，后续任务可继续调度。
2. **账号准入条件（`baseEligible`，全部满足才可接单）**：
- `proxyHealthy === true`
- `!acc.locked`（未处于登录、检查、接管、切代理、删号等独占锁）
- `acc.running < acc.maxConcurrent`
- Muse 侧 `acceptsWork(acc.state)` 为真——**仅 `ready` 与 `busy` 可接单**；`paused`、`cooling_down`、`limit_reached`、`needs_login`、`checkpoint`、`proxy_error`、`takeover`、`new`、`logging_in` 一律拒接。
- 对应 `CapabilityKey` 已验证且账号未标 `false`。
- 若任务绑定了 `boundAccountId`（如续聊/延长）或指定了 `accountPref!== "auto"`，则只过滤该账号。
3. **执行通道选择（HTTP 直连 vs UI 页面）**：
- `choosePath()` 根据账号 `execMode`、`httpUsable`、`uiSlotFree`（需满足 `!mainPageBusy` 且不超全局 `uiPagesCap` 与内存阈值）及任务附件需求决定走 `http` 还是 `ui`。
4. **多候选账号排序（`auto` 模式下取第 1 名）**：
1. `httpDegraded`（直连通道刚失败降级的账号排最后）
2. 负载率 `running / max` 升序（最空闲优先）
3. 额度档位 `quotaRank` 升序（0=充裕或未知 … 3=快照已用尽）
4. `lastActiveAt` 升序（空闲最久优先）
5. `account.id` 字典序兜底。

## 4. 待连接恢复后补验的 3 个收尾项

1. 读取 `desktop/src/main/coordinator.ts`：确认无头模式下 `driver.readQuota()` 的触发条件，以及 `resumeAccount` / `checkSession` 的精确内部方法签名（方便在 `engine.cjs` 扩展无头恢复）。
2. 读取 `desktop/src/main/adapter/muse/httpChannel.ts` 与 `desktop/src/main/accounts.ts`：核实 `sessionFor(accountId).fetch` 与 `robots.txt` 轻页在 `vm_state` 探测和降级恢复中的全部细节。
3. 读取 `pack-repo/runtime/l2-handoff/build-headless.mjs`：核对无头构建相较桌面端裁剪了哪些调度/额度/恢复逻辑，并将最终定稿提交推送到 `pack-repo`。

---

> 入仓说明（调度员 2026-11-20 补记）：本报告由 Mac 上桌面端 Gemini 逆向 Muse4AI 源码产出；其本机文件夹连接中途断开，未能自行入仓与补完第 4 节 3 个收尾项，群主将全文转交调度员入仓。报告中「15 分钟内 3 次 PRE_SUBMIT_TRANSIENT 超时即打入 paused」一条，须与调度员已落地的分类修复对读：coordinator.ts 已将 PRE_SUBMIT_TRANSIENT 标记为不计入通用失败（countsAsGeneric=false，2026-11-18 落地并实证），该连锁在现行构建中已断开；Gemini 因未读到 coordinator.ts 未纳入此点。
