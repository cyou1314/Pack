# staging45：D1 引用完整性断言（不做表重建迁移）

## 口径
全表零 FOREIGN KEY 声明是现状；深查第 3 轮定案不做表重建迁移，改以断言
套件兜底：orphans.py 对任意 pack.db 跑 20 项 LEFT JOIN 反连接检查，任一
孤儿即退出码 1。

## 检查面（20 项）
tasks/messages/runs/attachments/task_transfers/member_skills/
member_memory_versions/room_summaries/review_log/desktop_lock 的全部
引用列。注意 task_transfers.from_member/to_member 存的是成员**名字**
（非 id），检查按 name 连接——首跑曾按 id 误报 3 行，核对生产数据后修正。

## 实测
- 生产备份（pack.db.bak-staging43）：20/20 全清，零孤儿。
- 自证测试 test_orphans.py 5/5：工具链造的正常数据零孤儿； raw SQL 注入
  消息/运行/记忆版本三类孤儿后逐项精确命中、无误报。
- 用法：python3 orphans.py <db>；建议每次部署后对当次备份跑一遍。
