# MAC-WORK2-BRIEF — Gemini 审计本机侧修复单（2026-11-20）

来源：2026-11-20 桌面端 Gemini 全栈审计（11 条问题清单，调度员已逐条在源码核实成立）。
中枢侧已由调度员修完上线：staging49（member_list 连接泄漏、7 处写接口 removed_at
校验、task_list 补 room 字段与按房间过滤）、staging50（task_list 多状态查询、
room_read_new tail 取尾）。本单只含本机侧（runtime/、app/、l2-handoff）。
与 MAC-WORK-BRIEF（额度自报/rpc 长连接/引擎守护/闲聊部署）同批施工、分别回报。

纪律（与前单相同）：每件独立验收独立回报；连败两次停工回报；结果写入
MAC-WORK2-RESULT.md 入仓；不动 VM 中枢与生产数据；令牌不打印不入仓；
红线目录 pack-headless-l2-preflip-20261119 绝对不碰。

## 件一（P0）task_list 调用改造，拆 100 条雷的本机侧
- runtime.mjs:271、407、538 与 bridge.mjs:156、166 现裸调 task_list({}) 只看
  第一页 100 条；总任务过百后新卡不可见，bridge.mjs:166 还会误判持有权丢失、
  调 stopLostTask() 自杀已提交的引擎任务。
- 改法：用中枢新能力——status 传 "open,claimed,working,review" 多状态一次取
  活跃集；确需全量时用 offset 翻页兜底。bridge 核对持有权改按
  assignee_name + 多状态查，或直接 task_audit 核对，不得再用全量第一页。
- 验收：构造任务总数 >100 的本地演练（可对本机单机中枢或单元桩）证明新卡
  可见、桥不再误判丢失；原有认领/交活路径回归一遍。

## 件二（P0）批准正则统一口径
- runtime.mjs:300 与 bridge.mjs:254-261 的裸正则 /批准\s*任务#(\d+)/ 无行首
  锚点且不认「拒绝」：「不要批准 任务#N」会误放行；手打「拒绝」会被无视、
  死等 2 小时占死单槽。
- 改法：统一为与中枢 _approval_state 同口径 /^(批准|拒绝)\s*任务#(\d+)(?!\d)/，
  见「拒绝」立即 task_block 结束等待。runtime.mjs:378 内层无锚点匹配一并收口；
  :370 一带已有合规实现，别改坏。
- 验收：单元用例覆盖「不要批准 任务#5」不放行、「拒绝 任务#5」立即结束等待、
  正常「批准 任务#5」放行三态。

## 件三（P0，闲聊部署前必修，与 MAC-WORK-BRIEF 件四同批）
- chat-policy.mjs classifyChat（:27-28）：owner 消息未排除「模板：」前缀、
  dispatcher 消息只看含 @ 即触发——room_watch 转卡回执（dispatcher 身份、
  正文含 @absent）会被误当闲聊召唤，模板指令也会误触发。
- 改法：classifyChat 排除「模板：/模板:」前缀；排除 room_watch 系统回执形态
  （回执文案以 hub/room_watch.py 实际输出为准逐条对齐）。
- runtime.mjs:193、216-217：processChat 失败走退避后 finally 仍强推
  chatState.last_chat_id 写盘，失败消息被永久跳过——改为仅成功后推进；
  chatSeed 写死的「你是 absent」改为按 cfg.memberName 注入。
- 验收：runtime/tests 既有闲聊用例全过 + 新增回执/模板/失败不推游标三类用例。

## 件四（P1）任务归属与房间不再靠猜
- App 任务板现靠 roomTaskIds = Set(messages.compactMap(\.taskId)) 从 200 条
  消息反推（PackApp.swift:218-220、249-251、896-903），消息超 200 条后新卡
  在任务板/待批准中心/交活通知里一起隐身。改走 task_list（已带 room 字段、
  可按 room 过滤）；消息拉取改 room_read_new tail=True 取最近 200 条。
- runtime/bridge 写死 "pack-main" 的发帖与审批路径改读 task.room：
  runtime.mjs:222、297、365、397、434、522 与 bridge.mjs:123、133、192、
  208、236、255 逐处核，是任务上下文的就改、是房间轮询配置的保留并注明。
- 验收：房间消息 >200 条时新卡在任务板可见；非 pack-main 房间的任务审批
  请求发到该任务所属房间。

## 件五（P1）L2 交接助手三修（runtime/l2-handoff/engine.cjs）
- :53 handoff() 用 toLocaleLowerCase('vi') 忽略大小写查账号、:101
  memberOperation() 用 a.alias === member 严格匹配，口径统一（二选一并在
  结果文件说明选了哪个及理由）。
- :91-95 finally 里抛错覆盖原异常——改为保留原异常、finally 只做清理。
- :134 socket .catch 静默吞错——补错误日志（记原因、不记任何凭据）。

## 件六（P2）畸形 URL 不再让桥死循环
- bridge.mjs:200、214、223、241 与 runtime.mjs:429 的 new URL() 未捕获：
  正文含 http://foo:bar/ 类畸形 URL 时同步抛 TypeError，被外层归类
  connection_or_io 并清空 hub.sid，每 20 秒重试到租约耗尽。
- 改法：URL 解析点逐个 try/catch，解析失败按无链接普通文本继续处理；
  不得因此清 hub.sid。

## 件七（P2）MemberHandoff nc 时序
- app/Sources/PackApp/MemberHandoff.swift:71-85 向 /usr/bin/nc 写请求后先
  readDataToEndOfFile() 再 close 写端，对端若等 EOF 才回话会拖满超时。
  改为写完先关写端再读（或并发读写），并留一条真实交接路径回归。
- bridge.mjs 写死 'absent'（:20、117、130、156-168、264）多成员化本轮不做，
  仅在本单备案，现网单成员无碍。

## 件八（P2）地址落盘 + 引擎出口断连排查
- runtime.mjs:178 发现新地址后只改内存 cfg.hubUrl 不落盘，重启必撞旧死址
  （runtime.log 已有 1867 条 poll error）。改：发现新地址后写回 config.json，
  保留 addrToken 等全部既有键、文件权限 600 不变。
- 排查 pack-headless-l2/engine.log 2026-11-20 03:40–08:52 的 TLS 握手失败
  （ssl_client_socket_impl.cc:949、ERR_CONNECTION_CLOSED）：只查引擎配置与
  本机代理（127.0.0.1:7897）状态，结论写入结果文件，动不动手等调度员裁。
  红线：pack-headless-l2-preflip-20261119 只许远观、绝不作为回滚或排查对象动它。

## 件九（部署提醒，并入 MAC-WORK-BRIEF 件四）
- 本机 ~/pack-runtime/ 尚缺 chat-policy.mjs 与 chat-driver.js（本机运行时与
  仓库实差 105 行即闲聊回路未部署），件四同步时别漏拷这两文件；同步后按
  件三的新 classifyChat 一起验。
