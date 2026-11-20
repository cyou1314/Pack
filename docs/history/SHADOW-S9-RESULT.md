# SHADOW-S9-RESULT：影子双成员 §9 演练结果（2026-11-19 深夜，VM staging）

## 演练环境与口径

- staging 中枢：`~/workspace/pack/shadow-s9/hub/hub.py`，与生产 hub.py 逐字节一致（md5 aa74d6bc21f66d1aa6dfc03506478da2），**未改一字**；独立目录、独立全新 DB、端口 127.0.0.1:18794，演练完已按 pid 关停。
- 唯一与生产不同的运行参数：`PACK_LEASE_SECONDS=25`（生产 1800）——把租约缩短到秒级，才能在分钟尺度上观察「租约过期回收 + 接力」全程；sweeper 60 秒周期、退避与接力候选口径等其余机制均为源码原样。时延类结论按此参数解读，文中逐处注明。
- 成员：调度员在 staging 内登记 shadow-a / shadow-b 两个 stub 成员，驱动脚本以双客户端 0.2–0.5 秒轮询模拟双运行时（比真实运行时约 20 秒轮询更快，属加速演练）；交活一律走标准信封（verdict/summary/artifacts/self_check）。
- 身份说明（与简报字面的一处出入，如实记）：staging DB 独立，生产调度员 token 在 staging 不互通，故调度员身份用 staging `init` 自签的 token；生产 `.dispatcher_token` 未被读取、未用于任何调用。成员 token 只在驱动进程内存中流转，未落盘。
- 生产无损核对：演练前生产 pack.db 计数 tasks=54 / members=12 / messages=181 / runs=40，演练后复核**四项完全一致**，生产 8794 监听正常，全程未碰生产 DB、端口与 systemd 服务。

## 逐场景结论

### ① 并发抢单 —— 已实证
12 张未指派卡双 stub 同时开抢：12/12 全部被认领，shadow-a 6 张、shadow-b 6 张；逐卡直查 DB：attempts 均为 1、每卡恰好 1 条 run 且 status=closed / outcome=completed、run 执行成员与认领赢家一致；零双认领。全部经 review→approve 走到 done。整轮约 16 秒。

### ② 并行交活 —— 已实证
两卡分别指派 a、b：双双 claimed 后同时置 working（DB 快照双卡同为 working），各自 complete 进 review，调度员逐一 approve 后双双 done。

### ③ 留痕转交 —— 已实证
a 认领后 task_transfer 给 b：转交行落 task_transfers（actor=shadow-a、reason 留痕），任务 assignee 换为 b、claim_token 置空；a 用旧 token 调 task_update 被拒（not the current holder）；b task_accept_transfer 领新令牌后 run_start→complete→approve→done。task_transfers_list 链完整（1 行，终态 accepted）。

### ④ 自动接力 —— 分三条路径如实记录
- **④a 额度耗尽（持有人自报）—— 已实证**：a 认领后 task_block(fail_class=quota)，调用内即触发接力（block 返回 relayed_to=shadow-b，整调用 0.5 秒）；转交行 actor=auto-relay、reason 为「auto-relay: 额度耗尽」、状态 pending；b accept 后完成并关单 done。前置条件按源码口径备齐：b 心跳新鲜、额度数据新鲜且周已用 20%（<90%），a 额度置 95%。
- **④b 持有人掉线失联（租约过期）—— 已实证**：a 认领后彻底静默（停心跳、不续租）。认领后 35 秒，接力落行（租约 25 秒过期 + sweeper 60 秒周期内首轮命中），审计链含 lease_expired_requeue 与 task_auto_relay（reason=「auto-relay: 掉线失联」）两条；b accept 后完成 done。注：生产租约 1800 秒时，同路径的时延量级为「30 分钟租约 + ≤60 秒 sweeper」，机制相同、只是等得久。
- **④字面变体（未认领的指派卡）—— 未触发，属机制无此路径（非故障）**：卡指派给 a、a 从不认领、a 额度置 95% 并停心跳，观察 140 秒（跨 2 个以上 sweeper 周期）：任务纹丝不动（status=open、assignee 仍 a、attempts=0、转交行 0 条）；期间 b 尝试认领被指派校验拒绝。源码依据：自动接力只挂在两条路径上——持有人 task_block 自报 quota、已认领任务租约过期被回收；一张从未被认领的 open 指派卡两条都不沾。**产品含义**：成员「还没接活就先掉线/额度见底」时，指派给它的卡不会自动改派，只能等调度员人工改派或放池——这是现行机制的真实边界，留作已知项。

### ⑤ 桌面锁互斥 —— 已实证
a 持卡 acquire 成功；b 持另一卡 acquire 被拒，错误载荷为 desktop_busy 且点名 holder=shadow-a；desktop_status 显示 a 持有（task 对应）；a 显式 release 后 b acquire 成功；b 交活后锁被自动释放（desktop_status held=false，complete 出口放锁实证）。

### ⑥ 批准全程 + 打回回池 —— 已实证
批准卡：a 认领【需批准】卡后发 approval_request，中枢自动把消息 task_id 回填为该卡；调度员在房间回「批准 任务#N」后 a 读到决议、续租、交活、approve 关单 done。
打回卡：a 交活进 review 后调度员 task_review 打回——任务回 open、**指派保留 a**、退避 next_claim_at 置位（fail_class 缺省 unknown、attempts=1 → 300 秒）；调度员 task_clear_backoff 后 a 重新认领（attempts=2）、交活、approve、done。

### ⑦ 连接卫生（FD）—— 已实证（含 Gemini 分开统计口径补测）
驱动内总数口径：FD 总数 setup 后 10 → 抢单轮后 10 → 全部场景结束 9，无增长。
驱动结束后、同一 staging 进程未重启，按 Gemini 口径补测一轮 8 卡并发小高峰，分开统计（/proc/<pid>/fd 软链指向 pack.db 的句柄数 vs ss 按 pid 计的 TCP socket 数）：
- 高峰前（idle）：总 FD 7，db 句柄 0，TCP 1（仅监听口）；
- 高峰峰值（0.2 秒采样）：总 FD 15，db 句柄 5，TCP 4（监听 + 3 个客户端连接）；
- 高峰后（客户端仍连接）：db 句柄已回 0，TCP 3；
- 客户端断开 20 秒后：总 FD 7，db 句柄 0，TCP 1——与高峰前完全一致。
结论：两类句柄均无持续泄漏。SQLite 连接是每调用短开短关（并发瞬间峰值 5、活一干完即回 0，Gemini 点名的早返漏关在本轮负载下未抓到现行）；TCP 只随在线客户端增减、不堆积。口径说明：原⑦在驱动内只测了总数，分开统计为事后同进程补测（演练后 idle 基线与补测高峰前后各测一次），未编造驱动期内的分开数字。

## 边界：影子销账 vs 留给 eva 真人

**本轮影子演练已销账（中枢侧机制）**：并发认领原子性与 runs 台账、双成员并行状态机、留痕转交全链（含旧令牌失效）、两种自动接力触发路径（额度自报 / 掉线失联）与候选口径、桌面锁互斥与各出口自动放锁、批准决议链与打回回池保留指派、连接卫生（FD 双口径）。

**留给 eva 真人验收（影子替不了）**：
- 内嵌登录与交接闭环：真人亲手在 App 登录容器完成登录、钥匙串授权、cookie 加密交接落盘；
- 过期重登闭环：真会话过期 → expired 呈现 → 同分区重登 → logged_in；
- 同机双 Profile 网页并发：两个 ego Profile 同时驱动 muse.ai 网页的真实并发（桌面串行、网页会话互扰、回复提取稳定性）；
- 真实账号风控与回复提取：网页改版、验证码/风控拦截、长回复截断等执行体侧现实问题；
- 真实额度数据链：成员额度目前靠调度员人工维护（member_quota_set），接力/闲聊限流依赖其新鲜度，真人成员上线后须验自动采集或接受人工口径；
- 运行时守护：Mac launchd 托管下的运行时崩溃自愈与断线重连（本轮 stub 无此层）。

**本轮新暴露的已知项**：未认领的指派卡无自动改派路径（见 ④字面变体）——成员接活前掉线时卡会一直挂着，建议列入 v1.1 议题（调度员巡检发现后人工改派，或给 sweeper 加一条「指派成员离线超时未认领」的处置）。
