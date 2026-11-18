# staging39 慢工复检：附件通道（D18）

定向测试 test_attachments.py 31/31（全新 DB 复跑）。

## 写路径与锁
- attachment_put：配额统计 + 文件落盘 + 元数据插入全程持全局 _lock，
  配额检查与插入无竞态窗口；文件以 tmp+os.replace 原子落盘。
- 绑定（room_post attachment_ids）：与消息插入同一事务；绑定用带守卫的
  UPDATE（message_id IS NULL AND room_id=? AND uploader_id=?）+ rowcount
  复核，任一失败整笔 rollback——并发绑同一附件 4 路竞争实测恰 1 成功、
  失败方不留孤儿消息（DB 直查 0）。
- attachment_get 只读 + 一条审计插入，无状态变更。

## 鉴权与伪造面
- 三个新工具均为在册成员可用（非调度员专属，与设计一致）；坏 token 拒绝。
- 绑他人附件、绑已绑附件、绑不存在 id、重复 id、畸形 JSON 全部拒绝。
- room_post 既有守卫（task_card/decision/approval_request）不受影响（回归项实证）。

## 内容校验
- mime 白名单（png/jpeg/webp/gif）+ 魔数双验，声明与内容不一致拒绝；
- base64 严格解码（validate=True）；大小在解码前按字符串长度预检、
  解码后复检，≤10MB；空文件拒绝。

## 性能与既有路径
- room_read_new 每页多一次按 message_id 索引的批量查询，不带本体，
  轮询体积只随附件元数据增长。
- 内容寻址去重：同图两次上传磁盘只存一份（实测）。

## 已知口径（有意为之）
- 房间不做成员级隔离：任何在册成员可 get 任意附件，与消息读取同口径。
- 配额按元数据行计（同图多行重复计数），软顶 500MB/房。
- get 写审计：量级由真人 UI 节奏决定，可接受。
