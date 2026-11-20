# staging55 复检记录：room_read_new 补 reply_to_id 回读

## 缘起

staging53 疏漏（Mac Codex 施工 App 提问卡时实证）：room_read_new 的
回读行只有 payload、没有 reply_to_id，App 无法把 question_answer 并回
对应的 question 卡（inbox_pull 的 _msg_view 一直有该字段，漏的是
room_read_new 这条路）。Codex 提问卡请中枢补齐，调度员核源码属实后
立本微增量。

## 改动（基线 = 生产 staging54 部署版，md5 bcfae697…；diff 7 行）

- room_read_new 两条 SELECT（tail 与正序）各加 `m.reply_to_id`；
- 回读行加 `"reply_to_id": None if deleted else x["reply_to_id"]`
  （已删消息与 payload 同口径回 None，不漏原值）；
- 工具数不变（74），无新参数、无行为变化：其余 kind 的 reply_to_id
  本就落库（inbox_send 路径），此次只是把已有数据读出来。

## 测试（run_tests.sh 四段，每段全新 DB、端口 18797）

- test_staging55.py 11/11：工具数 74；question 落卡后 answer 的
  reply_to_id 在正序与 tail 两种读法下都精确等于 question 的 id；
  question/chat 行 reply_to_id 为 None；payload 回读不破。
- 回归链全绿：test_staging53.py 56/56、test_question_state.py 29/29、
  test_staging54.py 20/20（均在本目录 hub.py 上跑）。
- 调度员亲跑一遍即上述结果（本片由调度员自建自测，改动面 3 处、
  无并发/鉴权面变化，对抗复检简化为：字段只读出不写入、无新入口、
  external 读路径同一函数同一水位截断，reply_to_id 不携带正文外信息）。

## 部署

待 staging54 部署后回归（pack-reg54）ALL GREEN 后按序部署；
部署后回告 Mac Codex（它在等这个字段解开 App 提问卡的作答关联）。
