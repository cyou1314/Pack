# REVIEW52 — EXTERNAL-AGENT Phase B（件三收件箱 + 件四 pack-agent.mjs）

日期：2026-11-20。施工单正本：仓库 `EXTERNAL-AGENT-BRIEF.md`（冻结 1fdbda4）。
工作区：`tests/hub-staging52/`（隔离实例端口 18798、全新 DB、harness 模式）。
生产 `hub/` 零触碰，repo-mirror 未动（入仓由调度员做）。

## 基线核对

| 文件 | md5 | 说明 |
|---|---|---|
| hub_phaseA_baseline.py | `4a3626eaeae064ddac12144c1064752a` | = 提交 e552bc7 的 tests/hub-staging51/hub.py，逐字节一致 |
| room_watch.py | `e50827b2c9b4754ed06958a3a9c3191e` | 与 Phase A 一致，本期未改 |
| hub_prod_baseline.py | `f1a8cff1e920e23e3b1ce0af4ec2cf9e` | = 生产本体 md5（对照留证，生产未动） |
| hub.py（改后） | `d633af60487e7ea56c6edbc269630cf7` | 对基线 diff 约 331 行，py_compile 通过 |
| pack-agent.mjs | `3254bceff23db900069a10bac8b577f6` | node --check 通过 |

## hub.py 改动清单

1. **白名单**：`EXTERNAL_ALLOWED_TOOLS` 加入 `inbox_pull` / `inbox_mark_read` / `inbox_send`，工具总数 71 → 74。external 之外成员行为零变化。
2. **新常量**：`MIN_AGENT_VERSION="1.0.0"`、`INBOX_PULL_LIMIT=100`、`INBOX_WINDOW_SECONDS=10 天`、`INBOX_CHECKIN_AUDIT_GAP=300`、`INBOX_BLIND_REPLY_PREVIEW=20`。
3. **脚本版本门 `_agent_version_gate()`**：经 FastMCP 的 `get_http_request` 取 User-Agent（FastMCP 4.1.8 已验可用）。只拦自称 `pack-agent/<版本>` 且低于 1.0.0 的调用，返回 `{error: "agent_outdated", min_version, ...}`；inbox 三工具入口都过门。
4. **`_mentions_member`**：与 room_watch.resolve_mention 同口径（精确匹配优先、唯一前缀兜底、歧义不算命中）；差别仅在 watcher 取首命中派单、此处扫全部 token 答布尔。
5. **`_room_post_impl` 加 `reply_to_id` 参数**（默认 0，既有调用行为不变）并落库——Phase A 只加了列，本期补上写入路径，inbox_send 与 room_post 共用。
6. **inbox_pull**：下限取 max(游标, 入群水位) 硬截断；10 天时间窗；上限 100 条 + `has_more`；保留 `since_id` 回看（仍受水位与时间窗约束）；每条带 author 的 role/kind 服务端标签、`mentions_me` 标记、任务卡的 status/title 摘要、附件表；同时刷新 `last_heartbeat`（check-in 语义）；距上次 pull <5 分钟写 `inbox_checkin_frequent` 审计，内容照常返回（见取舍 1）。
7. **inbox_mark_read**：游标只进不退，推进量封顶为房内最大消息 id。
8. **inbox_send**：全程持 `_lock` 串行「幂等查 → 防盲回查 → 落库 → 记 op_key」：
   - op_key 先查 `message_op_keys`，命中回原 message_id + `deduplicated: true`，不重复落库；
   - reply_to 校验：目标须在本房，external 不得指向入群水位之前的消息；
   - 防盲回：游标之后有他人新消息且未 `ignore_new` 时不抛错，返回 `{sent: false, blocked: "new_messages", new_count, new_messages: [摘要 ≤20 条], cursor}`；
   - 落库走 `_room_post_impl`，与 room_post 共用房间绑定、kind（恒 chat）、频控窗口与附件绑定规则。

## pack-agent.mjs（staging52/ 下先行，入仓时落位 runtime/）

- 单文件、零依赖、Node 18+；裸 JSON-RPC 说 /mcp（initialize 握手 + `Mcp-Session-Id` 头、SSE/JSON 双解析取最后一条 data 负载，范式对齐 runtime/ 现有实现）。
- 子命令：`pair` / `inbox` / `read` / `send` / `check-in` / `mcp`（薄代理）。
- config 存 `~/.pack-agent/<profile>.json`：目录 0700、文件 0600（写后补 chmod）；`--profile` 隔离多身份。
- 每个请求带 `User-Agent: pack-agent/1.0.0`；pair 响应中的 token 不回显 stdout。
- pair 防串号：落盘前先用新 token 调 heartbeat 查身份，与既有 config 身份不一致且无 `--force` → 退出码 4、不动既有 config。
- 退出码约定：send 被防盲回拦 → 3；check-in 有待办（mentions>0 或 open_tasks>0）→ 2 并打印一行 JSON 摘要（供两层轮询的本机定时器判断是否拉起 agent）；pair 串号拒绝 → 4。

## 与施工单逐条对照

| 施工单条目 | 落点 | 状态 |
|---|---|---|
| inbox_pull：游标后未读 / 10 天 / 上限 100 | `_inbox_pull_impl`（水位与游标双下限取 max） | ✅ |
| inbox_pull：@ 本人标记、任务卡状态摘要、author role/kind 标签 | `_inbox_items` 统一组装 | ✅ |
| inbox_pull：受入群水位硬截断、保留 since_id 回看 | 同上，回看同样受水位+时间窗约束 | ✅ |
| inbox_mark_read：只进不退 | 封顶房内最大 id、回退请求不生效 | ✅ |
| inbox_send：reply_to_id / 绑附件（口径同 room_post） | 共用 `_room_post_impl` 落库与绑定校验 | ✅ |
| inbox_send：防盲回默认拒并附回新消息摘要，ignore_new 强发 | 返回结构化 blocked 结果（不抛错），摘要上限 20 条 | ✅ |
| inbox_send：op_key 幂等，同 key 回原结果不重复落库 | `message_op_keys` 先查后写，全程 _lock 串行 | ✅ |
| check-in：pull 刷心跳；<5 分钟只审计不硬拦 | 刷 `last_heartbeat` + `inbox_checkin_frequent` 审计 | ✅（取舍 1） |
| 限流复用既有窗口（5/分、20/时、100/天），inbox_send 计入同一窗口 | 与 room_post 混发实测共用 5/分窗口 | ✅ |
| approval_request 独立硬顶不变 | 未触碰，staging51 套件回归覆盖 | ✅ |
| inbox 三工具加入 EXTERNAL_ALLOWED_TOOLS，名单外仍拒 | 白名单放行 inbox_*、memory_read 仍拒（实测） | ✅ |
| 版本门：UA 低于 MIN_AGENT_VERSION 返回过旧错误 | `_agent_version_gate`，三工具入口全过门 | ✅（取舍 2） |
| pack-agent.mjs：单文件零依赖、子命令集、config 0600、--profile 隔离 | 见上节 | ✅ |
| pair 防串号（先查身份、他人无 --force 拒覆盖） | 退出码 4 + config 不动（实测） | ✅（取舍 3） |
| mcp 薄代理透传 | stdio ↔ /mcp 转发，含会话代管 | ✅（取舍 4） |

## 测试结果

套件编排 `run_tests.sh`（全新 DB → 隔离实例 → 八套件串行）：

- **首跑**：main 142/142、fixes 18/18、concurrency 8/8、watcher 10/10、migration 10/10、direct 9/9、inbox52 41/42、script 16/18 —— 3 个 FAIL，定性如下，全部修复：
  1. `op_key: only one row persisted n=2` —— **测试自身 bug**：计数口径 `body LIKE '幂等%'` 把调度员干扰消息（正文同前缀）一并计入；实现无误（「重试回原 message_id」断言已 PASS）。改测试为按两条发送正文精确计数。
  2. `mcp proxy: mark_read round-trips`（Missing session ID）—— **脚本真 bug**：代理未做上行握手就透传 tools/call。按取舍 4 修复。
  3. `send: succeeds after mark_read` 被防盲回拦 —— 第 2 项连带（游标未推进），修复后自愈。
- **修复后全量复跑**：`EXIT=0`，八套件全绿，共 **257 项 PASS / 0 FAIL**——main 142/142、fixes 18/18、concurrency 8/8、watcher 10/10、migration 10/10、direct 9/9、inbox52 42/42、script 18/18。
- 工具数断言同步：staging51 套件副本的 `tools == 74` 与新工具集合、migration 升级后工具数均已按 71→74 更新并通过。
- Phase A 全部套件在新 hub.py 上复跑无回退。

## 取舍标注（拿不准、按保守口径处理，待调度员/Gemini 复核）

1. **check-in 频度**：任务书抓手写「<5 分钟不重复推送内容」，但施工单正文口径是「check-in 过频只审计不硬拦」——按正文执行：重复 pull 内容照常返回，只多写一条 `inbox_checkin_frequent` 审计。若部署前要改回「不推内容」，改动点在 `_inbox_pull_impl` 一处。
2. **版本门降级**：传输层拿不到 UA、或 UA 不是 pack-agent 时**降级放行**。理由：App、Mac 运行时、测试 harness 等既有 MCP 客户端不带这个 UA，硬拦会误伤；版本门只对自称 pack-agent 的脚本生效。代价：改 UA 即可绕过版本门——它定位是「提醒升级」而非安全边界，真正的边界仍是 token + 白名单。
3. **pair 防串号的兑换消耗**：防串号校验发生在兑换之后（须先用新 token 查身份），被拒落盘时该配对码已在服务端消耗，需重新发码。宁可废一张码也不串号，未做「先验身份再兑换」的改造（服务端无此接口）。
4. **mcp 代理会话策略**：initialize 由下游客户发送时原样透传（响应头捕获 session id）；下游不发 initialize 直接发 tools/call 时，代理先自行完成一次握手再转发。代理自身握手用 `clientInfo: pack-agent/1.0.0`——服务端审计里这类会话会记为 pack-agent，与「纯透传」的语义有一点出入，换取的是对不发 initialize 的客户（如简单脚本）也能用。
5. **防盲回摘要上限**：`new_messages` 附回上限取 20 条（常量 `INBOX_BLIND_REPLY_PREVIEW`），`new_count` 给真实总数；施工单只说「附回摘要」未给数字，20 是自选值。
