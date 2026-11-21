# staging36 慢工复检：加急 D15（定稿 §15.3）

## 增量内容（基于 staging35 之上叠加）
- tasks 加列 urgent INTEGER NOT NULL DEFAULT 0（init 迁移，沿既有模式）。
- task_create 加 urgent 参数（默认 False），审计 detail 带 [urgent] 标记。
- 新工具 task_set_urgent（调度员专属）：未终结任务可标/撤，done/cancelled
  拒绝，幂等，写审计。
- task_list：SELECT 带 urgent，排序改 ORDER BY urgent DESC, id ASC——
  待认领队列加急恒置顶，Mac 运行时按名轮询 open 列表即天然优先认领加急；
  App 任务板按状态分列后列内顺序亦为加急在前。

## 对抗清单逐项
- 锁覆盖：task_set_urgent 为单条 UPDATE + 审计，与 task_clear_backoff 等
  既有调度员工具同一姿态（单语句写、无读改写竞态面）；task_create 的
  urgent 只是 INSERT 多一列。
- 鉴权：task_set_urgent require(dispatcher)，成员调用实测拒绝；
  task_create 本就是调度员专属。
- 规范化：urgent 入参为 bool，落库 1/0；task_list 回 urgent 原值（0/1），
  App 侧按真值判断。
- 并发：置顶是排序口径、无状态竞争；两加急任务按 id 序（定向第 6 项）。
- 状态机零改动：urgent 不进 claim/complete/block 任何判定，退避与排班
  （next_claim_at/sched_at）口径不变——加急任务若在退避中仍须等退避到点，
  置顶只决定同批可见任务的先后。此为有意口径：加急不绕过失败退避。
- 幂等与终态：重复设同值成功；cancelled/done 拒绝（定向覆盖）。
- 回归面：task_list 排序变化影响所有消费者——运行时（得利：优先认领）、
  App 看板（列内加急在前，符合定稿）、摘要/审计工具不读 task_list 排序。
  LIMIT 100 截断口径不变（P2 遗留单独处理）。

## 定向测试
test_urgent.py 17/17（迁移/默认与回显/置顶序/组内序/幂等/成员拒/未知拒/
终态拒/认领不受影响/审计落行）。
