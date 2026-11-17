# 设计：成员交活绑附件（task_complete 侧，D18 尾巴）

## 现状
附件只能经 room_post(attachment_ids) 绑到聊天消息；task_complete 内部落库的
report 消息不能绑图——成员交活带图只能先发一条 chat 再交活，图与交付分离。

## 方案（staging46 拟）
- task_complete 新增可选参数 attachment_ids（JSON 数组字符串，口径与
  room_post 完全一致：1–4 个）。
- 校验（与 room_post 同规则）：附件存在、属本任务所在房间、上传者必须是
  调用者本人、当前未绑定（message_id IS NULL）。
- 原子性：绑定 + report 消息插入 + 任务状态更新同一事务；任一校验失败
  整笔回滚——任务保持 working、无 report 残留、附件仍未绑定可重试。
- 审计：task_complete 的 audit detail 追加附件计数。
- 运行时衔接：成员先 attachment_put 上传、再 task_complete 带 ids，
  与聊天发图同一心智模型，无新工具。
- App 侧零改动：任务详情已渲染 report 消息附件（B1-14）。

## 定向测试清单（拟 10 项）
 happy path 绑 2 图、report 带附件回读；他人附件拒；已绑定附件拒；
 跨房间附件拒；>4 拒；非法 JSON 拒；坏 id 混入时整笔回滚（任务仍
 working、无 report、好附件未被绑）；不带参数的旧调用行为不变；
 交活信封与附件并存；审计 detail 含计数。

## 不做
- 不给 task_block / 转交加附件；不改 room_post 既有口径。
