# staging47 慢工复检：provider / login_state（分发线 L1 中枢侧）

增量：members 加 provider、login_state 两列（init 迁移）；member_register
可选 provider（给了则 login_state 初始 pending）；新工具
member_login_state_set（调度员专属、枚举校验、_lock 内 UPDATE+审计）；
member_list 带两字段。工具数 63 → 64。

## 写路径与锁
- member_register：沿用既有全局 _lock（查名→选组合→插入原子），本次只在
  同一临界区内多写两列，无新竞态面。
- member_login_state_set：require(dispatcher) 后在 _lock 内 SELECT→校验
  removed→UPDATE→audit→commit，检查与写同临界区，无 check-then-write 缝隙。
- init 迁移：启动期单线程 ALTER，与既有 quota/removed_at 列同一模式。

## 对抗探针（review_probe.py，隔离 staging 实跑）
- P1 同成员 10 路并行回写不同状态：10/10 成功，审计行数 == 10（无丢失
  写入），终态为被写过的状态之一（无撕裂）。
- P2 同名 8 路并行登记（带 provider）：恰 1 成功（既有原子性未被新 INSERT
  破坏；开发中曾因占位符多一枚全量失败，定向测试首跑即抓到、已修）。
- P3 20 成员 × 10 次回写并行：20/20 终态与各自最后一次写入一致。
- P4 成员 token 伪造：对 member_login_state_set 与 member_register
  （带 provider）均被拒；无伪造成员产生；在册状态未被篡改。

## 定向测试（test_login_state.py）18/18
工具数 64 与新工具在列；无 provider 老登记完全兼容（两字段空、返回值
形状不变）；带 provider 登记 list/返回值一致；状态流转
pending→logged_in→expired→pending 落库；非法枚举（hacked/空/大写/
连字符）全拒；不存在与已移除成员拒；provider >64 拒；无 provider 成员
也可独立回写状态。

## 迁移实证
旧 schema DB（无两列 + 存量行）跑 init：两列自动补齐、存量行保留且两
字段为 NULL。

## 口径与残余
- provider 为不透明标签（≤64 字符），中枢不校验其取值域——合法值集合
  属于 App 内置注册表（设计缺口 2），中枢侧强校验反而会挡住未来新脑源
  登记；与 profile 的处理口径一致。
- login_state 只许经 member_login_state_set 写；全库无其他写路径
  （grep 实证：quota_set/avatar_set/remove 均不触碰该列）。
- 错误信息只回显调用者自己的入参（name/state），不泄他人数据。
