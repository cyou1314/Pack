# staging46 慢工复检：task_complete 交活绑附件（D18 尾巴）

## 改动
- task_complete 新增可选参数 attachment_ids=""（JSON 数组字符串），
  其余签名与返回不变；带附件时返回追加 attachments 计数，审计 detail
  追加 attachments=N。不带参数的老调用行为逐字不变（定向第 8 项实证）。

## 对抗检查
- 校验口径与 room_post 逐项一致：JSON 数组、1–4 个、整数非 bool、不重复；
  绑定 UPDATE 带 message_id IS NULL + room_id=任务房间 + uploader_id=本人
  四重守卫，rowcount!=1 即整笔回滚——他人附件、已绑定、跨房间在定向
  2/3/4 项分别实证被拒。
- 原子性：绑定在 report INSERT 之后、commit 之前；失败路径显式 rollback，
  任务状态 UPDATE 与 report 一并撤销（定向第 7 项实证：混合好坏 id 时
  任务仍 working、无 report、好附件仍未绑定）。
- 权限：task_complete 本身经 _holder 校验（当前持有人 + claim_token），
  附件再限本人上传，双层归属一致，无越权面新增。
- 与信封并存：envelope 校验在绑定之前，非法信封先行打回、不触附件
  （定向第 9 项实证合法组合）。
- 工具数不变（63）；无 schema 迁移；App 零改动（任务详情已渲染 report
  附件，B1-14）。

## 测试
- 定向 test_complete_attach.py 11/11 全过。
- 全量回归见 regression.log。
