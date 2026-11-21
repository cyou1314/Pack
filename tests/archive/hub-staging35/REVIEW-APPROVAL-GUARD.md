# staging35 慢工复检：approval_request 门口守卫（深查 F1 + S2）

## 缺口回顾
- F1（第 1 轮，中）：_approval_gate 在校验发帖人是否为任务持有人之前就把
  task_id 补写到消息上；非持有人冒发的 approval_request 虽不触发规则自动
  决议（staging14 C7 已修），但消息本身带着 task_id 落库，进 App 批准中心
  冒充真待批项；且 _approval_state 还按正文「任务#N」兜底匹配，伪造请求
  一旦被真人误批即算已决议，可抢先消费真执行者的批准门。
- S2（第 7 轮）：任务不存在时也补写 task_id，留下悬空引用。

## 修法（调度员 2026-11-17 依群主「你来安排就行」定口径）
- room_post 在 INSERT 之前对 kind='approval_request' 设门：
  必须能解析出「任务#N」且任务存在（任何身份，含调度员）；
  非调度员还须是该任务当前执行者（assignee 本人且 status 为
  claimed/working）。不满足整笔 ValueError 拒绝、不落库。
- _approval_gate 改为先查任务、存在才补写 task_id（防悬空引用）。

## 对抗清单逐项
- 锁覆盖：room_post 全线历来不持全局 _lock（消息为追加型写入，无读改写），
  本增量不改变该姿态。守卫的 SELECT 与 INSERT 之间存在微秒级 TOCTOU
  窗口（任务恰在此时离 claimed/working），后果上限是给已结束任务多落
  一条待批卡：无运行时在等它、规则求值在 _approval_gate 内还会再查
  assignee；记录为已评审接受的残余风险，不在本增量重构 room_post 锁。
- 身份规范化：身份只由令牌解析（authenticate），入参不自报；assignee
  以成员 id 数值比对，无名字符串比对面。
- 错误信息：拒绝文案只说规则，不回显任务标题/他人信息。
- 并发探针：bob 6 路并行伪造同一任务 → 6/6 拒绝、零落库（定向测试第 7 项）。
- 伪造面：decision/task_card 既有守卫复测仍在（定向测试第 8 项）；
  approval_request 的四种伪造（他人任务/不存在/无引用/未认领）全拒。
- 合法路径回归：持有人 claimed 态发请求成功且 task_id 补写；调度员代发
  成功；chat/report 不受影响。定向测试 16/16。
- 负载：本增量只加一条前置 SELECT，无新写路径放大；全量回归套件
  （run-all-suites）另行验证（含 staging15 批准链与规则库用例）。

## 残余与边界
- 运行时若在任务被取消后仍发 approval_request 会收到工具错误——语义
  正确（任务已取消）；其静默搁浅问题属 RT2（运行时侧），不在本增量。
- owner 身份发 approval_request 按非持有人处理（拒绝）：App 与 watcher
  均无此合法流（决议走调度员身份、聊天走 owner 身份），已核。
