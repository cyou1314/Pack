# staging41 慢工复检：watcher W3/W4 修复

改动只在 room_watch.py 的 run() 与新增 _handle_message/_post_best_effort；
纯函数（resolve_mention / parse_template_command / strip_reply_metadata）未动，
既有单测全过（reply OK、mention 15/15）。

- W3：成员名单改每轮（10s）随 room_list/template_list 一并刷新。
  实证：watcher 启动后新登记成员 newbie，@newbie 消息下一轮即转卡，
  无需重启 watcher。
- W4：游标只在单条消息处理成功（或本就不需处理）后推进；处理抛错
  不推进、同条连续失败计数（内存态），<5 次下一轮重试，>=5 次记
  日志放弃并越过（防毒消息堵死整房）。提交动作（task_create /
  template_fire）成功后的确认回帖改尽力而为（失败只记日志），
  杜绝「卡已开、回帖失败 → 整条重试 → 重复开卡」。
  实证：注入 task_create 必失败的测试中枢后，msg 8 依次记
  failed 1/5..4/5（游标停 7）、第 5 次 giving up 并越过（游标 8），
  其后正常消息照常转卡（msg 9 → task 3）。
- 处理函数桩测 5/5：提交失败上抛（可重试）、回帖失败吞掉且只开一卡、
  正常路径一卡一回帖。
- 已知残余：watcher 进程崩溃恰在「开卡成功、游标未存」窗口内会重复
  开一卡（at-least-once 取舍，优于原静默丢消息）；watcher 长连接在
  中枢重启后可能楔住不自愈（演练实证）——生产由 systemd
  pack-room-watch 与部署流程的连带重启覆盖，本次部署即连带重启。
