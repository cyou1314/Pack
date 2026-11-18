# staging27 慢工复检：task_list 补 created_by + review_note

增量性质：纯只读。task_list 的 SELECT 增加两列输出（cr.name AS created_by，
经 members 主键 LEFT JOIN；t.review_note 直读）。无新工具、无新写路径、
无权限变化、无状态变化。工具总数仍 56。

## 对照 adversarial_hub_review 清单

- 写路径/锁覆盖：本增量零写路径，diff 全量审过（仅 docstring + SELECT 两处）。
- 名称规范化：created_by 由成员 id JOIN 出成员名（唯一规范形），不引入任何
  字符串比较；创建者成员不存在时 LEFT JOIN 给 NULL，不炸列表（成员在本
  系统不可删，此为兜底）。
- 行数不膨胀：JOIN 对象是 members 主键，不可能扇出重复行；定向测试两卡
  列表恰两行，回归套件的计数断言另行兜底。
- 鉴权/信息面：task_list 本就对全体已认证成员开放；created_by 是房间内
  公开的成员名，review_note 是验收意见（决议消息本就落房间），不扩大
  可见面。定向测试含成员（alice）视角读取两字段，与既有口径一致。
- 伪造面：两字段均为服务端派生，task_create 等任何工具不接受客户端
  传入 created_by/review_note，无伪造路径。
- 并发/负载：只读 SELECT 走 SQLite WAL，无锁语义变化；全量回归套件
  （run-all-suites --hub）作为部署门再跑一遍。

## 定向测试

test_task_fields.py 19/19 全过：建卡→created_by=成员名（非 id）、
review_note 初始 None、打回后 status=open + review_note=理由原文 +
指派清空、不串卡、清退避后二次交活、通过后 review_note=通过备注。
