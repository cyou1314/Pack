# staging43 慢工复检：F2 kind 白名单 + D3 task_cancel 早返审计

## 改动
- room_post：在 task_card/decision 既有守卫之后加 kind 白名单——仅 chat /
  approval_request / decision 可经 room_post 落库，其余（report、system、
  任意字符串、空串）整笔 ValueError 拒绝。task_card 与 decision 的原有
  专用报错与角色校验保持在前，报错口径不变。
- task_cancel：对 done/cancelled 任务的早返分支补 audit(task_cancel_noop)
  并 commit，重复/误取消从此有痕；返回值不变。

## 对抗检查
- 内部落库路径不受影响：task_card 由 task_create 内部 INSERT、report 由
  task_complete/run 内部 INSERT、规则引擎 decision 由 _approval_gate 内部
  INSERT——均不经 room_post 工具面，白名单只拦工具入口。watcher 的确认
  回帖走 kind=chat ✔；运行时 approval_request 走既有 F1 门 ✔（定向测试
  实证其穿过白名单后被 F1 门以「existing task」拒）。
- App 侧合法 kind 只有 chat（发言）与 decision（调度员决议），均在白名单内。
- 白名单判定在房间查存之前：对不存在的房间发非法 kind 先报 kind 错——
  无信息泄漏差异（房间是否存在另有 room_list 可见）。
- D3 审计写入与主路径同事务口径：audit() 后 commit 再 close；早返不再
  静默。task_audit 归属规则（detail 以 "task <id>" 开头）与新 detail
  "task N already <status>" 兼容，定向测试以 task_audit 回读实证。
- 工具数不变（63）；无新参数、无 schema 迁移。

## 测试
- 定向 test_kind_whitelist.py 17/17（白名单 10 项 + D3 审计 7 项）。
- 全量回归见 regression.log（run-all-suites --hub 本目录 hub.py）。
