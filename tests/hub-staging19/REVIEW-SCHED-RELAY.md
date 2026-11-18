# 排班 + 自动接力 慢工复检（2026-11-16，staging19）

范围：task_schedule（sched_at 标记 + next_claim_at 退避闸复用）、task_claim /
task_clear_backoff 清 sched_at、_auto_relay 助手 + task_block(quota) 与
sweeper（租约过期回池）两处钩子。

## 六项复检

1. **鉴权边界**：task_schedule 仅调度员；排班目标须 role=member 且非 owner
   （owner 在 DB 里 role=member，名字硬排除——存量复检同款教训）；接力无新增
   外部入口，task_block 仅多回一个 relayed_to 字段（附加字段，老执行体忽略）。
2. **并发原子性**：两处接力记账都走 _lock + 状态复检（block 路径复检
   blocked 且 assignee 仍是本人；sweeper 路径复检 open 且 assignee/令牌为空），
   与 task_transfer / task_accept_transfer 的锁口径一致；sweeper 原有守卫
   UPDATE 未动。
3. **资格口径（保守）**：候选 = role=member、非 owner、非原持有人、心跳
   180 秒内、额度数据 900 秒内（与 App 15 分钟过期口径一致）且周已用 <90%；
   数据缺失/过期一律不合格（未知不当可用）；多名合格取已用最低者。
4. **纪律一致性**：接力走 §4.8 同一本账（pending 转交行 + 审计 + 房间消息，
   接收方 task_accept_transfer 才算接手，过期由 sweeper 既有分支回收）；
   TRANSFER_CAP=3 链上限与手工转交共用计数；带执行体标记
   （[M4A]/[ENG]/[ENG-image]）的任务执行体钉死、不接力；attempts 不因接力
   变化（与转交语义一致）。
5. **数据完整性**：sched_at 走 PRAGMA 先检后加列；认领成功与
   task_clear_backoff（取消排班）都清 sched_at；排班到期闸门复用
   next_claim_at——task_claim 的既有原子 UPDATE 天然执行，无新闸口。
6. **审计**：task_schedule 写 task_schedule；接力写 actor='auto-relay' 的
   task_auto_relay + 转交行 actor 同名 + 调度员名义房间消息，三处留痕。

## 已知残留（接受并记录）

- 接力记账段 try/except 尽力而为：若记账中途异常（如转交行已插、归属未改），
  残留 pending 行会被 task_accept_transfer 的状态校验挡下，并由 sweeper 的
  转交过期分支在 1800 秒内清掉、自愈；block/回收本身不受影响。本机 sqlite
  简单语句，中途失败概率极低，换取主动作（block/回收）永不被接力连累。
- sweeper 的 MAX_ATTEMPTS 阻塞分支不接力：任务已达重试上限，属调度员处置
  范围，不自动换人。
- 排班只支持「指定成员 + 指定时刻」：周期性排班不在 §14.2 定稿内，不做。

## 测试证据

- 专测 test_schedule_relay.py 26 项 ×3 轮（第 1 轮 25/26，唯一失败是测试件
  把 envelope 传成 JSON 字符串——工具签名是 dict，测试已改、与中枢无关）：
  排班开卡/未到期拒认领/到期可认领/取消排班/清标记；quota 接力全程
  （block→pending 转交→接受→信封交活）；四种不合格候选（数据过期/已用
  ≥90%/非 quota 阻塞/带标记任务）全部不接力；sweeper 掉线路径实测接力
  （子进程直调 sweep_once）。
- 回归：functional 78/78。
- 写文件教训复现并已防：_auto_relay 的 SQL 曾被写盘路径篡改成字面
  `claim_token=<redacted>`（grep 字节级校验抓出并修复）；部署前对 hub.py
  做 `grep -c redacted == 0` 硬校验。
