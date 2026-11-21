# PORT-ACCESSMODE-BRIEF —— 搬运四：成员级访问模式预设（staging56 施工单）

来源：OPENBOT-PORTING.md 对照表第 3 项「成员级访问模式预设」——每个
agent 一个模式开关（只读 / 仅工作区 / 全权），模式即产品表面。Pack 落
法（对照表原文）：在成员上加 access_mode 字段，先落到 external 成员与
任务认领面：只读模式禁一切写工具。排期口径：随 external 相关增量顺
带（本单即该增量）。

## 设计（冻结）

1. **字段**：members 加 `access_mode` 列（沿用 hub.py 既有 PRAGMA
   table_info + ALTER TABLE 迁移范式），取值 `full` / `workspace` /
   `readonly`，缺列/NULL 一律按 `full` 读（老 DB、老成员零行为变化）。
2. **闸门位置**：build_server 的 `_gated_tool` 包装器内，authenticate
   之后、既有 external 白名单闸（_ext_check）之后，再过 access_mode
   闸 `_mode_check(me, tool_name)`。两闸独立、任一拒即拒；external 成
   员先触发白名单审计（external_tool_denied），非 external 或过了白
   名单的再触发模式审计（access_mode_denied，detail 含 tool 与
   mode）。模式每次调用现查（authenticate 每调用查 DB），变更下一次
   调用即生效，不做缓存。
3. **readonly 语义**：只许 READONLY_ALLOWED_TOOLS，名单外一律拒
   （含日后新增工具，默认拒，与 external 白名单同哲学）。名单（纯读
   + 一个明示例外）：
   member_list, room_list, room_summary_get, room_read_new, inbox_pull,
   task_list, task_transfers_list, task_audit, run_list, rule_list,
   egress_rule_list, template_list, skill_list, skill_read, memory_read,
   attachment_list, attachment_get, desktop_status, pair_code_list,
   external_gate（仅其只读查询形态；若实现上无法区分读写形态，整工具
   移出名单并在 REVIEW 里记明），heartbeat（例外：心跳是存在性信号、
   非内容写，禁掉会让只读成员看起来掉线；明确备案）。
   施工时以 staging 副本 tools/list 的全量工具名为准逐一核对：凡不
   在上表、又非 dispatcher/owner 专属的工具，逐个在 REVIEW56.md 里
   写明归类（write）与理由；专属工具本来就进不去，名单不收。
4. **workspace 语义**：在 full 基础上只禁「出圈/系统级」写工具
   WORKSPACE_DENIED_TOOLS = {egress_check, skill_publish,
   skill_assign, skill_unassign, skill_rollback}，其余（含任务认领、
   房间发言、收件箱、附件）照常。拒时同样写 access_mode_denied。
5. **设置面**：新工具 `member_access_mode_set(name, mode)`（工具数
   74→75），调度员专属：
   - mode 不在三值内 → 报错；
   - 目标不存在 → 报错；已移除成员 → 与既有写接口同口径报错；
   - **防自锁**：目标是 dispatcher 本人 → 拒绝（参照 member_token_
     rotate 不能轮换 dispatcher 的教训）；目标 role=owner → 拒绝
     （群主的令牌不进模式系统）；
   - 成功写审计 `member_access_mode_set`（detail 含旧值→新值），返
     回 {name, access_mode}。
6. **回读**：member_list 每行带 access_mode（按第 1 条口径归一为三
   值之一）。App 本单不改（字段先行，展示随下一轮 App 重建）。
7. **不动**：member_register（新成员默认 full）、external 白名单名
   单本身、批准流、任务状态机、egress 规则判定（workspace 禁的是
   egress_check 工具入口，不是规则引擎）。

## 验收（staging 隔离，全过才算完）

- 迁移：老 DB（无 access_mode 列）启动自动补列、存量成员读作 full。
- readonly 成员：名单内工具可用；task_claim / room_post /
  inbox_mark_read / attachment_put / task_complete 逐一被拒且各有
  access_mode_denied 审计；heartbeat 可用。
- workspace 成员：task_claim、room_post 可用；egress_check、
  skill_publish 被拒且有审计。
- full 成员（含老成员）：全部既有行为不变。
- external + readonly 叠加：task_claim（白名单内）被模式闸拒；
  room_summary_set（白名单外）先被白名单闸拒（审计
  external_tool_denied，不得出现双写）。
- 设置面：普通成员调用 member_access_mode_set 被拒；对 dispatcher
  本人、对 owner、对已移除成员、对非法 mode 逐一被拒；set 后下一
  次调用立即生效（先 full 认领成功 → set readonly → 再认领被拒）。
- 并发：同一成员模式在 set 与调用并发下不产生异常状态（模式是单
  值列，验读写交错无 500/脏读即可，按 adversarial_hub_review 技能
  留痕）。
- 回归：默认回归链全绿 + staging51/52/53/54/55 各自套件复跑全绿；
  REVIEW56.md 写对抗复检（锁面无新增写路径——闸门只读判定 + 审计
  写，审计写在闸门路径上沿用 _ext_check 既有范式并说明）。

## 纪律

隔离目录 tests/archive/hub-staging56/（副本 + 全新 DB + 换端口，harness 模
式），不碰生产 hub.py 与生产 DB。diff 只许动：迁移段、_gated_tool
包装段、新 helper（_mode_check 与两张名单常量）、member_list 回读、
新工具 member_access_mode_set。写完 grep 校验无异常字面量、跑
py_compile。结果写 tests/archive/hub-staging56/REVIEW56.md。
