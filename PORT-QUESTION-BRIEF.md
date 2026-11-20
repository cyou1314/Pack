# 搬运一 · 结构化提问卡施工单（staging53，2026-11-20）

出处：OPENBOT-PORTING.md 第 2 项（学 OpenBot 的 ask_ui 机制，自研实现）。
目标：成员能就当前任务向群主发一张**带选项/可输入的提问卡**，群主点选或
写一句即可答复，答复回到提问成员——补上批准卡只有「批准/拒绝」的短板。

## 中枢改动（hub.py，工具数不变）

1. **迁移**：messages 加 `payload` 列（TEXT，存 JSON），按既有
   PRAGMA table_info 检查后 ALTER 的范式。
2. **room_post 扩两个 kind**（签名加 `payload=""` 参数，JSON 字符串）：
   - `question`：提问。守卫——必须带 task_id 且任务存在；dispatcher 之外
     的提问者必须是该任务当前执行者（与 approval_request 同口径）；
     正文（问题本体）≤2000 字；payload 结构
     `{"options": [...], "allow_input": true|false}`：options 为 2–6 个
     字符串（每项 ≤60 字），或不给 options 但 allow_input=true（纯输入型），
     两者至少居其一，否则拒；**同一任务同时只允许一张未答 question**
     （已有未答的再发直接拒，防刷屏）。
   - `question_answer`：答复。守卫——只有 dispatcher / owner 能发；
     reply_to_id 必填且必须指向一条 question 消息；该 question 未被
     回答过（已有 question_answer 指向它则拒重复答）；正文 ≤2000 字。
   - 其余 kind 口径一律不动（task_card 仍仅 task_create 内部落库、
     decision 仍仅 dispatcher）。
3. **_question_state(conn, task_id)**：仿 _approval_state，返回该任务最近
   一张 question 及其答复状态（未答/已答 + 答复消息 id），供后续 App 与
   运行时消费；本片只落函数与测试，不新增工具。
4. **external 成员本片不放行**：白名单不加 question/question_answer，
   闸门自动拒 + 审计（提问面先在内部成员验证，与 approval_request 的
   external 限条件放行分开）。
5. room_read_new / inbox_pull 回读时带出 payload（external 回读不受影响，
   因其发不了也收不到 question——bound_room 里 dispatcher 发的
   question_answer 若出现在其收件箱，payload 原样带出无妨）。

## 纪律

- 隔离 staging（tests/hub-staging53/，副本 + 全新 DB + 换端口），生产
  零触碰；基线与生产 hub.py 逐字节一致起步。
- 测试覆盖：守卫全分支（无 task_id、任务不存在、非执行者提问、同任务
  重复未答、options 越界、纯输入型、答非 question、重复答、非
  dispatcher/owner 答复）、payload 落库与回读、external 闸门拒、
  _question_state 口径、既有 approval/decision/chat 回归不破。
- 慢工复检按 adversarial_hub_review 清单过一遍，结论写 REVIEW53.md。
- App 渲染（提问卡 + 选项按钮）是下一片 Mac Codex 的活，本片只定契约：
  payload 结构与 question/question_answer 语义以本单为准。
