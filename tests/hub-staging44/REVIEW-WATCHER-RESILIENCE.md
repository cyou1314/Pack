# staging44 慢工复检：watcher 韧性束（W5 + P5 + P4）

改动对象：hub/room_watch.py（副本开发，未动生产）。

## W5 模板名前缀大小写不敏感
- parse_template_command 前缀候选改 n.lower().startswith(token.lower())，与 @ 提及
  匹配器（find_mention 同口径）对齐；精确命中仍优先（token in names 先行）。
- 影响面：仅模板直发解析。模板名为纯中文时行为不变（lower 无副作用）。

## P5 进程内重连（替代「退出靠 systemd 拉起」与「会话楔住」）
- run() 改为外层重连环：_watch_session 抛错（启动连不上 / 会话死亡）→ 退避
  5s→10s→20s→30s 封顶重连；会话存活 >120s 后退避复位 5s。
- 会话内连续轮询失败计数：达 MAX_POLL_ERRORS=3 判定楔住、主动 raise 触发重连。
- 状态安全：重连后 load_state() 以落盘为准重读；游标只由 save_state 推进、
  单调不回退，重连不会重放已处理消息（state 文件在每条消息处理后落盘）。
- fail_counts 跨会话保留（内存），毒消息计数不因重连清零绕过上限。
- systemd Restart=always 保留作进程级兜底（进程真崩时仍拉起），与进程内
  重连不冲突。

## P4 poll error 节流 + 日志轮转
- 连续轮询错误只记第 1 条与第 3 条（触发重连），中间条抑制；错误风暴下
  每轮会话至多 2 行 poll error + 1 行 session ended。
- room-watch.log 配 logrotate（日轮转、留 7、压缩）：watcher 的 log() 每次
  open/append/close，rename 式轮转安全，不需 copytruncate。

## 实测（hubtest 8844，POLL_SEC=2 的测试副本；生产补丁 POLL_SEC 不变）
- 纯函数 W5：8/8（大小写命中/歧义/唯一前缀/未知/非指令）。
- 演练 A：hub 未启动时先起 watcher——进程存活、退避重连日志 5s/10s 递增；
  hub 起来后自动接入（watch start）。
- 演练 B：@alice 转卡 ✔；「模板：weekly」小写触发 Weekly 模板开卡 ✔。
- 演练 C：SIGKILL 硬杀 hub 后重启——poll error 1/3、3/3 两行、会话退出、
  20s 退避后重连成功（watch start 二次出现），其后 @ 转卡恢复 ✔。
  （staging41 记录的「重启后长连接楔住」自此修复，生产不再依赖部署连带重启。）
- 演练脚本首轮 A2/A3/C1 曾报 FAIL，系脚本读日志的符号链接被清理命令误删、
  读到空文件所致；以 watcher 真实日志复核三项均实际通过。

## 残余
- 中枢长时间宕机时 watcher 每 ~30s+ 重连一次、每次 1 行日志，量级可接受。
- 退避在「短命会话反复失败」时涨到 30s 封顶，恢复延迟上限约 30s+轮询间隔，
  对 @ 转卡场景可接受。
