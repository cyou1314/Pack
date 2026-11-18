# REVIEW-WEEK-DONE（staging38 慢工复检）：member_list 加 week_done

## 目的
D12 余项：App 成员详情「本周已交活」一直如实标注「暂无完整统计」——task_list
有 LIMIT 100 且按房间/状态过滤，App 侧算不出全局周口径。中枢侧聚合是唯一
正确数据源。

## 实现
- member_list 每人加 week_done：status='done' 且 updated_at >= 本周一
  00:00（UTC+8）的任务数，按 assignee 分组一次查完再映射，无 N+1。
- SELECT 顺带加 members.id（映射键）；输出新增 id 与 week_done 两个字段，
  纯增补，既有客户端按名取字段不受影响。
- 完成时刻以 tasks.updated_at 为准：task_review 关单时写 updated_at=now，
  即验收通过时刻——与「交活」口径（成员交付+验收关单）一致。

## 对抗复检逐项
1. 口径：relay/转交后 assignee 为最终执行者，完成数记在关单时的 assignee
   名下——即实际干完活的人，正确。
2. 边界：周一 00:00:00 整点含入本周（>=），定向测试实证；上周回拨不计。
3. 时区：固定 UTC+8（产品单用户、群主在 CST），无夏令时问题；helper 独立
   函数可单测。
4. 性能：一条 GROUP BY 聚合走 tasks(status, assignee) 扫描，成员列表低频
   调用，量级无虞。
5. 权限：member_list 本就全员可读，week_done 是房间内公开的工作量数据，
   不涉及私人信息。
6. 残余：updated_at 也被其他写路径刷新（如 urgent 切换只对未终结任务，
   done 任务不可切换——task_set_urgent 对 done/cancelled 拒绝，故已完成任务
   的 updated_at 不会被加急操作污染）。已核对。

## 定向测试
test_week_done.py 7/7：初始全 0、完成计数、review 态不计、上周排除、
周一零点边界含入。部署前过全量回归。
