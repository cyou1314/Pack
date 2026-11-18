# 对抗复检（2026-11-16，staging21）

对象：线上 hub.py（53 工具）。方法：成员令牌视角的系统性攻击面复检 +
轻量压力。套件 test_adversarial.py 25 项，24 过、1 项为测试口径问题（见下）。

## 实证缺口（已修复）：room_post 的 kind 无人管

room_post 的 kind 参数由调用方任意填写、无白名单：任何成员都能发出
kind='decision'（伪造决议）或 kind='task_card'（伪造任务卡）的消息，
与中枢内部落库的真决议/真卡片在房间流里无法区分。App 按 kind 渲染，
伪造决议可误导群主与成员。

修复：room_post 守卫——task_card 一律拒（只能 task_create 内部落库）；
decision 仅调度员可发（App 以调度员身份代发决议是既定路径，B1-5），
成员拒。内部决议走直接 INSERT（_approval_gate / review_file），不受影响。

## 攻击面逐项结果（全过）

- 越权矩阵：29 个调度员专属工具逐个用成员令牌调用，全拒；成员表/房间
  无污染（无 mallory、无 evil-room）。
- kind 伪造：成员 decision/task_card 拒；调度员 decision 放行、
  task_card 仍拒；房间里最终只有 1 条合法决议。
- 令牌冒用：bob 持 alice 的 claim_token 对 task_update / task_complete /
  task_block / task_transfer / run_start / run_finish 六个工具全拒；
  alice 本人令牌照常可用。
- 记忆隔离：bob 读 alice 的 private-core 被拒（且写审计）；各自读自己正常。
- 技能门禁：未指派 skill_read 被拒，指派后可读。
- 压力：300 张卡全生命周期（建/认领/交活）300/300 完成，1500 条消息
  落库（实际 2104 条含系统消息），库 integrity ok。顺序调用约 0.37 秒/次
  是测试 harness 每次新建会话的开销，非中枢瓶颈（真实执行体是长连接）。

## 口径澄清（非缺陷）

- 套件里「review 应有 300 条」断言只拿到 100：task_list 有 LIMIT 100
  硬顶，300 张卡都在（生命周期计数为证）。观察项：App 任务板若单列超
  100 张会截断，v1 规模下可接受，日后量大再加分页，不在本次修复范围。

## 回归

- decision_kind 专测 17/17（对修复后代码）。
- watcher 模板直发与 @ 流程不受 room_post 守卫影响（均走 chat kind）。
