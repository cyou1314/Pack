# staging53 · 结构化提问卡（搬运一）施工与复检记录

施工单：PORT-QUESTION-BRIEF.md（学 OpenBot ask_ui 机制、自研实现）。
隔离目录 tests/hub-staging53/，基线 = 生产 hub.py 逐字节复制
（起步 md5 三方一致：d633af60487e7ea56c6edbc269630cf7），全新 DB、
端口 18798（迁移测试另用 18795/18796），生产文件与生产 DB 零触碰
（收尾复核：生产 hub.py md5 不变、room_watch.py 未动、本片未连过
生产端口 8794）。

## 改动点（hub.py，diff +184 / −17 行，工具数不变 = 74）

1. **迁移**：init() 按既有 PRAGMA 范式给 messages 补 `payload TEXT`
   列（与 reply_to_id 同范式：只走迁移、不动 SCHEMA）；存量行 NULL。
2. **模块级新增**（紧跟 _approval_state）：
   - 常量 QUESTION_BODY_LIMIT=2000、OPTIONS 2–6、单项 ≤60 字（写死，
     同记忆/技能限额纪律）。
   - `_validate_question_payload(raw)`：解析并规范化
     `{"options":[...],"allow_input":bool}`——options 给出时须 2–6 个
     非空字符串、每项 ≤60；allow_input 给出须为 bool；两者至少居其一；
     多余键丢弃，输出键序固定。
   - `_question_state(conn, task_id)`：仿 _approval_state——按 task_id
     补写列或正文「任务#N」定位最近一张 question，再按 reply_to_id
     找其第一条 question_answer；返回 (question, answer|None)。
     本片只落函数，不新增工具。
3. **_room_post_impl**：签名尾加 `payload=""`；kind 白名单加
   question / question_answer；两 kind 正文上限 2000 字（全局 20000
   不变）；question 守卫——正文须含「任务#N」且任务存在（与
   approval_request 同口径引用方式）、非 dispatcher 提问者须为该任务
   当前执行者（assignee 本人且 claimed/working）、payload 过校验、
   同任务已有未答 question 直接拒；落库补写 task_id 与规范化 payload。
   question_answer 守卫——仅 dispatcher / owner（role='dispatcher'
   或 name='owner'，与 _task_mention 的群主侧口径一致）、reply_to_id
   必填且指向本房一条 kind='question' 消息、已有答复则拒重复答；
   落库 task_id 继承自被答 question。INSERT 统一带 task_id/payload
   两列（其余 kind 恒 NULL，approval_request 的 task_id 仍由
   _approval_gate 补写，旧行为不变）。
   external 在既有 kind 面被拒（chat/approval_request 之外皆拒），
   两 kind 另写一条 `external_kind_denied` 审计再拒。
4. **room_post 工具**：签名加 `reply_to_id: int = 0`、`payload: str = ""`
   （question_answer 的 reply_to_id 在旧签名里没有出口，不加则答复
   根本发不出去——这是对施工单「签名加 payload」的必要补齐，已在
   此备案）。reply_to_id 仅对 question_answer 生效，其余 kind 传入
   即清零（维持 room_post 旧行为：chat 回复走 inbox_send 既有路径，
   不新增跨房引用面）。question / question_answer 与 external 一样
   整体走 _lock，保证「检查→落库」串行。
5. **回读**：room_read_new（两条 SELECT）与 inbox_pull
   （_INBOX_MSG_SQL + _inbox_items）带出 payload 原文（已删消息回
   None，与 body 占位口径一致）；其余 kind 恒 None。

## 测试结果（全部 0 失败）

run_tests.sh 四段（每段独立全新 DB）：

| 套件 | 结果 | 内容 |
|---|---|---|
| test_staging53.py | **56/56**（连跑 3 轮均全过） | 工具数 74 与 schema、question 守卫全分支（无引用/任务不存在/非执行者/未认领/正文超长 + payload 11 个坏分支）、落库与 room_read_new/inbox_pull 回读、纯输入型、dispatcher 豁免、answer 守卫全分支（成员答/无 reply_to_id/指向 chat/指向不存在/跨房/正文超长/重复答）、owner 可答、已答后再问、question 不可编辑删除、并发双答/双问各只成一、external 两 kind 被拒 + 审计 2 条、水位后 question/answer 对 external 回读带 payload 且水位前不可见、approval/decision/chat/report/task_card 旧口径回归、chat 不带 reply_to_id 与 payload 的旧行为 |
| test_question_state.py | **29/29** | 进程内直测：payload 列迁移存在；_validate_question_payload 5 正例 + 17 坏分支；_question_state 6 个口径（已答/旧式正文引用/聊天提及不算/最近一张优先/无题/串答不算） |
| test_staging51.py | RC=0 | Phase A 全回归（含工具数 == 74 断言，即 staging15 口径） |
| test_fixes51.py | RC=0 | 红队六修回归 |
| test_concurrency.py | RC=0 | 既有并发探针 |
| test_watcher.py | RC=0 | watcher 两道防线（room_watch.py 未改） |
| test_staging52.py | RC=0 | Phase B 收件箱回归 |
| test_agent_script.py | RC=0 | pack-agent.mjs 脚本套件（脚本未改） |
| test_direct.py | **9/9** | staging51 附件配额进程内直测 |
| test_migration53.py | **6/6** | 生产基线库（无 payload 列、74 工具）→ 本片 init 迁移补列 → 旧成员/旧消息可读（payload=null）→ 迁移库上 question 全链可用 |
| test_load53.py | **5/5** | 50 张任务 × create→claim→question→answer 全周期 43.7s；DB 计数精确（任务/问/答各 50、孤儿答 0、未答 0） |

## 对抗复检（adversarial_hub_review 清单逐项）

- **锁覆盖**：本片唯一新写路径是 room_post 的两 kind，工具层强制
  _lock；「执行者检查 + 未答检查 → 落库」「角色检查 + 重复答检查 →
  落库」全程在锁内同一 impl 调用完成。其余写消息路径（inbox_send
  恒 chat、task_create 内部 task_card、task_update 的 report、
  _approval_gate 的 decision）kind 全部写死，不存在绕过 room_post
  伪造 question/question_answer 的第二入口；message_edit/delete 只
  认 kind='chat'，提问卡不可被作者改删。
- **并发探针**：双答并发（dispatcher 与 owner 同时答同一题）与双问
  并发（同成员同任务同时发两问）各跑 3 轮（主套件整轮重跑），每轮
  恰一成一败，结果稳定。
- **鉴权与伪造**：非执行者提问、成员答复、external 两 kind 均被拒
  （external 另有审计）；答复指向 chat/不存在消息/跨房 question 均
  被拒；错误文案不含他人姓名与归属信息（不存在与无引用同文案）。
  残余：question 的执行者检查与 task_transfer 若真并发，问题卡可能
  落在刚转走的任务上——提问卡本身不授予任何权限（答复权恒在
  dispatcher/owner），影响仅为一条卡挂错任务，与 approval_request
  既有残余同级，接受。
- **限额口径**：正文 2000 / options 2–6 / 单项 60 均为服务端硬校验，
  边界值（恰 60 字 option）直测通过；payload 规范化丢弃多余键，
  防 App/运行时读到未定义字段。
- **数据完整性**：_question_state 与落库补写的 task_id 双通道（列
  优先、正文正则兜底，与 _approval_state 一致）；负载探针核对全库
  计数无孤儿答、无漏答。
- **性能口径**：_question_state 全表扫 kind='question'（与
  _approval_state 扫 approval_request 同姿势），量级与既有待批面
  一致，不另建索引——与本片规模相称，备案。
- **external 面**：EXTERNAL_ALLOWED_TOOLS 未动（room_post 在名单内
  但 kind 面在 impl 内拒 + 审计，符合施工单第 4 条「闸门自动拒 +
  审计」）；external 回读带 payload 仅限其入群水位之后、绑定房内
  的消息，水位截断未受影响（套件实证水位前 Q1 对 external 不可见）。

## 与施工单的偏差备案

- room_post 签名除 payload 外另加 reply_to_id（理由见改动点 4：
  question_answer 的必填项在旧签名无出口）。工具数不变、旧调用
  行为不变（非 question_answer 的 reply_to_id 恒清零）。
- options 单项除 ≤60 字外另要求非空（空串选项无意义，校验器与
  直测均按此执行）。

## 未做 / 后续

- App 提问卡渲染与运行时消费 _question_state 是下一片（Mac Codex），
  本片只定契约。
- 未 commit、未 push、未部署：改动只在 tests/hub-staging53/ 工作区，
  等调度员审 diff 后按既有流程部署。
