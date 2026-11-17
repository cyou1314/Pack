# staging40 慢工复检：深查硬化小件（S1/S3/S4/F3/F4）

定向 test_hardening.py 12/12；S4 另行文件系统实证（全新 init：
.dispatcher_token mode=600、无 tmp 残留）。

- S1 room_read_new limit 钳制 1..200：App 固定用 200、watcher 用 200、
  运行时用 50/200，均在界内，行为不变；超大 limit 不再整表拉取。
- S3 member_register profile ≤2000 字符：超限整笔拒绝（在 _lock 之前，
  不占锁）；正常登记不受影响。
- S4 init 写 dispatcher token 改 os.open(0600)+os.replace 原子落盘，
  消除「先 644 后 chmod」的窗口与半写风险。
- F3 _holder 的 claim_token 比较改 hmac.compare_digest（Python 侧唯一
  比较点；authenticate 走 token_hash 数据库查询、无明文比较）。
- F4 room_post 正文与 message_edit 新正文 ≤20000 字符：运行时汇报走
  task_complete 不经 room_post；watcher/App 消息远低于上限。

写路径/锁：五处均为校验前置或比较方式替换，不新增写路径、不改事务边界。
