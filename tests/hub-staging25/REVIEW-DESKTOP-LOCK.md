# staging25 桌面锁慢工复检（2026-11-16）

增量：desktop_acquire / desktop_release / desktop_status 三工具 + 单例
desktop_lock 表 + 六处生命周期挂钩（claim 兜底清理、complete、block、
cancel、transfer、sweeper 两条回收支路）。工具数 53 → 56。

## 写路径锁覆盖审计（对抗技能第一项）
- desktop_acquire / desktop_release：临界区整体走全局 _lock ✓
- task_transfer 挂钩：该工具本体即在 _lock 内 ✓
- task_claim 挂钩：在其 _lock 段内、与认领 UPDATE 同事务 ✓
- task_complete / task_block / task_cancel 挂钩：与各自带持有人守卫的
  UPDATE 同连接同事务、同一 commit 落盘——放锁与状态变更原子 ✓
- sweeper 挂钩：在守卫 UPDATE（rowcount=1）之后、同连接、轮末统一
  commit；并发的持有人交活先行时 helper 按 task_id 匹配不到即 no-op ✓
- 唯一标识口径：holder 只存 authenticate() 给出的成员名（规范名），
  不接受调用方自报 ✓

## 授权与伪造探针（专测内）
- 调度员调 acquire 被拒（锁只给持任务的成员）；成员错令牌 acquire/
  release 被拒；拿别人任务的令牌被拒；非持锁任务 release 被拒；
  四类拒绝后锁仍在原持有人手里 ✓

## 并发探针
- 两名成员各持一卡同时 acquire：3 轮均恰好一个赢家、另一个收到
  desktop_busy 载荷（holder/task_id/since 均为房间内公共协调信息，
  与 desktop_status 的可见范围一致，无令牌类信息泄漏）✓

## 自动释放全出口（专测内逐条实证）
complete / block / cancel / transfer / 租约到期回收（sweep 子命令
实跑）五条全过；强放（dispatcher 无参 release）与空闲 no-op 过；
审计四类动作（acquire/release/force_release/auto_release）均留痕。

## 残留与口径
- 无等待队列：抢不到锁的成员收到 busy 载荷后自行重试（与认领退避
  同一模式）；成员数 ≤3、锁随任务结束必放，饥饿风险可接受。若日后
  成员变多再加 FIFO 队列，工具契约不变（acquire 可内部排队）。
- E4 宿主桌面执行体接入时，执行体须在首个 GUI 动作前 acquire、
  段末 release——此纪律已同步进 delivery-envelope 技能 v2。
