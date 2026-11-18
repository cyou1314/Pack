# staging31 慢工复检：idx_messages_room 索引（第 6 轮深查 P1）

增量内容：SCHEMA 增加一行 `CREATE INDEX IF NOT EXISTS idx_messages_room ON messages(room_id, id);`
（放在 messages 建表后，与既有 idx_memver / idx_transfers_task / idx_skillver 同口径）。
工具数不变（58）。部署机制：serve() 每次启动都跑 init() → executescript(SCHEMA)，
重启后索引自动建在生产库上，无需单独迁移脚本。

## 为什么只加这一个索引

- 实测的热查询：room 消息读取 `WHERE m.room_id=? AND m.id>? ORDER BY m.id ASC LIMIT ?`
  （watcher 每 10 秒轮询 + App 读消息）原计划全表 SCAN；加索引后
  `SEARCH m USING INDEX idx_messages_room (room_id=? AND id>?)`。
- room_list 的 `MAX(m.id) ... WHERE m.room_id=r.id` 子查询同样受益
  （`USING COVERING INDEX idx_messages_room`）。
- audit 表曾怀疑需 `ts` 索引：复核发现全部 audit 查询都按主键 id 排序
  （task_audit `ORDER BY id ASC LIMIT 201`，detail 三段 LIKE），没有任何查询
  按 ts 排序或过滤——**不加 audit 索引**，避免无用索引。
- messages 按 kind 的查询（approval_request）只有一种且表极小，不加。

## 复检六面（对照 adversarial_hub_review）

1. **写路径/锁**：本增量不新增任何写路径、不改任何工具逻辑；索引创建发生在
   init() 的 executescript 内，与既有三个索引同一条路径。无并发写面可探。
2. **归属/鉴权**：不改鉴权、不改任何工具的输入输出；索引只改查询计划，不改结果。
3. **结果等价性**：定向测试 T5/T6 在索引库与原始库上逐行比对两条热查询
   （3 个房间 × 读消息 + MAX）与 task_audit 的 audit 查询，结果完全一致。
4. **只读性**：索引是 SQLite 内部维护结构；hub 代码无新增 INSERT/UPDATE/DELETE。
5. **限额与截断**：LIMIT 语义不变；索引不改变返回行数与顺序（id ASC 由索引本身保证）。
6. **注入面**：索引名与列名均为服务端常量，无用户输入进入 DDL。

## 测试证据

- 定向套件 test_idx_messages_room.py：14/14（T1 SCHEMA 与 redacted=0、T2 索引已建、
  T3/T4 计划走索引、T5/T6 结果逐行一致、T6 audit 计划与结果不受影响）。
- 启动冒烟：staging hub 在 8830 端口起服，list_tools = 58，含 task_list / task_audit。
- 全量回归：run-all-suites.sh --hub hub-staging31/hub.py（见 regression-summary.txt）。

## 部署步骤（回归全绿后）

1. 备份生产 hub.py 与 pack.db 为 .bak-staging31；
2. 拷 staging31/hub.py 覆盖生产 hub.py；
3. sudo systemctl restart pack-hub（重启时 init() 自动建索引）；
4. preflight 应全绿 tools=58；生产库 EXPLAIN 复核走索引；
5. dispatcher token 直连本地 MCP 冒烟 task_list / task_audit(task 1)；
6. 推仓：repo-mirror 同步 hub.py（工具数不变，README/SPEC 无需改）。
