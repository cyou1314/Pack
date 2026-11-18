# REVIEW-RT1（staging37 慢工复检）：回池保留指派

## 缺陷回顾
活群验收 #26 实证 RT1：task_release 把 blocked 任务放回 open 时清掉 assignee，
而 Mac 运行时按 task_list(status=open, assignee_name="absent") 轮询——NULL 指派
永不命中按名过滤，放回的任务再也无人认领，重试链断裂。同型问题共四处：
task_release、task_review 打回、sweeper 租约过期回收、sweeper 转交过期回收
（后者任务挂在从未接受的接收方名下，过期后变无主）。

## 修法
- 前三处：UPDATE 时 assignee 改为 CASE 表达式——指派成员仍在册
  （members 行存在且 removed_at IS NULL）则保留原指派，已移除才解指派为 NULL。
- 转交过期回收：任务回到转交发起人（from_member）名下；发起人已移除则 NULL。
- sweeper 接力前置条件同步放宽：回池后 assignee 为空 **或仍是原持有人** 都可
  尝试 _auto_relay（接力内部自行换人，前置只是防抢占复检）。

## 对抗复检逐项
1. 原子性：四处均为单条 UPDATE，CASE 在同一语句内求值，无读-改-写窗口；
   task_claim 的条件 UPDATE（status=open AND (assignee IS NULL OR assignee=me)）
   未动，并发认领语义不变。
2. 接力耦合：_auto_relay 本身不依赖 assignee 为空（task_block 的 quota 接力
   一直在带指派的 blocked 任务上运行）；sweeper 路径前置已同步放宽。
   接力成功后任务转 claimed，原持有人 open 轮询看不见，不会双跑；
   接力转交再过期时按新规回到接力发起人（=原持有人），账面闭环。
3. 成员移除一致性：member_remove 自身的解指派逻辑未动（claimed/working 回池、
   open 解指派、review 不动、blocked 不动——定向测试 5 实证 blocked 保留原
   assignee id）；其后 task_release 的 CASE 守卫会把已移除成员的指派清为 NULL，
   任务回到无主池可被他人认领，不会钉死在已移除成员名下。
4. dispatcher/owner 被指派：两者的 members 行在册未移除，CASE 会保留指派；
   二者不按名轮询执行，任务停在 open 待调度员处置——与修前「变无主后可能被
   任意成员抢走」相比更安全，接受。
5. 语义变化申报：带指派任务在失败周期后不再变「无主池」任务，只有原指派人
   （或接力/转交链）能接；无指派建卡的任务行为不变（测试 6 实证）。
   这是本次修复的目的本身，不是副作用。
6. 数据形状：无 schema 变更，task_list 返回形状不变；App 侧 open 任务将如实
   显示原指派人（修前显示无指派），无契约破坏。
7. 残余（已评审接受）：sweeper 回收与 member_remove 并发时存在与既有代码同级
   的竞态窗口（移除先行处理 claimed/working，sweeper UPDATE 带租约复检守卫）；
   本次未扩大该窗口。

## 定向测试
test_rt1.py 27/27：release 保留+他人不可认领+本人可再认领；review 打回保留；
租约过期 sweep 保留；转交过期回发起人；已移除成员守卫回无主池；无指派池不变。
部署前须再过全量回归（run-all-suites 对 staging37/hub.py）。
