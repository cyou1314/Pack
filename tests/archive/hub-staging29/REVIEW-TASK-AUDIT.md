# staging29 慢工复检：task_audit（任务完整轨迹查询）

增量内容：新增只读工具 `task_audit(task_id)`（调度员专属），一次返回任务基本信息 + 该任务的 audit 事件流 + runs 台账 + 转交链，供 App 任务详情（定稿 §11.1）渲染时间线与审计入口。工具数 57 → 58。

## 复检六面

1. **鉴权边界**：`authenticate()` + `require(me, "dispatcher")`，与 run_list / task_transfers_list 同口径。定向测试实证：成员 token 调用被拒、伪造 token 被拒。返回内容（任务标题、审计 detail、转交理由）均为房间内数据，调度员本就有权读全部房间数据，无权限放大。
2. **归属匹配正确性**：audit.detail 以 `task <id>` 开头记账。本工具用 `detail = ? OR LIKE '<id>:%' OR LIKE '<id> %'` 三段精确分隔匹配——task 1 不会误配 task 12/100（分隔符后一位必为串尾、冒号或空格）。定向测试以 task 1 与 task 12 并存实证：task 1 只返回自己的 task_create。已知边界：个别 detail 形如 `task <id> rule#…`（规则引擎）以空格分隔、命中第三段，属正确归属；egress_check 等 detail 不带 task 前缀、不归本工具，符合预期（出口检查不是任务轨迹）。
3. **只读性**：工具体仅 SELECT，无任何写入/提交；并发下走 SQLite WAL 读，无锁升级。对照检查：函数内无 INSERT/UPDATE/DELETE/commit 字样（部署前 grep 复核）。
4. **限额与截断**：事件流 LIMIT 201、多取一条作截断探针，返回 `events_truncated` 标志，不静默丢数据；runs/transfers 按任务天然有界（attempts 硬顶 3、转交链硬顶 3）。
5. **注入面**：全部参数化查询；task_id 为 int 类型（FastMCP 层校验），LIKE 模式由服务端以任务 id 拼装、不含用户自由文本。
6. **与既有纪律一致性**：不存在的任务抛 ValueError（与 task_transfers_list 同口径）；返回 dict 结构与 run_list/task_transfers_list 风格一致（runs/transfers 字段直接复用两表列名）。App 侧接线归 B1-10，本增量只补中枢查询面，不动 HubClient。

## 测试证据

- 定向套件 test_task_audit.py：19/19（全轨迹事件齐全且正序、前缀防碰撞、权限三拒、错误路径）。
- 全量回归：run-all-suites 对 staging29 hub.py 复跑（含 staging15 工具数断言更新 57 → 58），结果见部署记录。
