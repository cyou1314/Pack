# Hub MCP 工具契约清单（v1 基线）

> 来源：`hub/hub.py` 当前 `build_server()` 中直接使用 `@mcp.tool` 注册的函数。分支：`v2-product-alignment`。静态提取结果，不替代运行时 `tools/list` 导出。

## 基线统计

- 工具数量：**75**。
- 本清单用于 v2 重构前后对比工具名与 Python 参数签名；参数 schema 的最终权威来源仍是运行时 FastMCP `tools/list`。
- Hub 工具注册包装 `_gated_tool` 在工具入口执行身份认证；external 成员还需通过工具白名单；已认证成员再经过 access_mode 闸门。拆分时必须保留闸门顺序。
- `@mcp.tool` 下的 docstring 是工具描述的一部分，改写描述也可能影响 Agent 工具选择，应纳入变更审查。

## 工具清单

### 成员与身份（11）

| 工具 | 参数签名 | 源码行 | 用途摘要 |
|---|---|---:|---|
| `member_register` | `def member_register(name: str, profile: str = "", avatar_combo: str = "", provider: str = "") -> dict:` | 1816 | 调度员登记一个新成员，返回其一次性明文 token（中枢只存哈希）。 |
| `member_remove` | `def member_remove(name: str) -> dict:` | 1889 | 调度员移除成员（软删）：token 立即作废、成员列表隐藏、头像组合 |
| `member_avatar_set` | `def member_avatar_set(name: str, avatar_combo: str) -> dict:` | 1974 | 调度员为在册成员更换参数化头像组合（UI 定稿 §12.6「更换头像」写路径）。 |
| `member_quota_set` | `def member_quota_set(member_name: str, week_used_pct: float = None, week_reset_at: float = None, extra_tokens_left: int = None) -> dict:` | 2030 | 调度员维护成员额度数据（UI 用量卡/成员详情的数据源，v1 人工维护）。 |
| `member_quota_report` | `def member_quota_report(week_used_pct: float = None, week_reset_at: float = None, extra_tokens_left: int = None) -> dict:` | 2070 | 成员自报本人额度（staging48）：身份只认 Bearer token 解析出的 |
| `member_login_state_set` | `def member_login_state_set(name: str, state: str) -> dict:` | 2173 | 调度员回写成员登录状态（staging47，分发线 L1 登录容器）：App 在 |
| `member_token_rotate` | `def member_token_rotate(name: str) -> dict:` | 2363 | 调度员应急轮换成员的 token（件一）：生成新 token 覆写 |
| `member_access_mode_set` | `def member_access_mode_set(name: str, mode: str) -> dict:` | 2398 | 调度员设置成员的访问模式预设（staging56 / 搬运四）： |
| `external_gate` | `def external_gate(enabled: bool = None, reason: str = "") -> dict:` | 2440 | 调度员总开关（件二）：hub_settings.external_enabled。 |
| `member_rename` | `def member_rename(name: str, display_name: str) -> dict:` | 2467 | 调度员改成员展示名（件二）：只改 display_name，内部 name |
| `member_list` | `def member_list() -> dict:` | 3920 | List room members with role/profile and last heartbeat (epoch seconds). |

### 配对与外部成员（3）

| 工具 | 参数签名 | 源码行 | 用途摘要 |
|---|---|---:|---|
| `pair_code_create` | `def pair_code_create(member_name: str, provider: str, room: str, display_name: str = "", reclaim: bool = False) -> dict:` | 2205 | 调度员生成配对码（staging51 / 件一）：码在生成时即绑定预定 |
| `pair_code_list` | `def pair_code_list() -> dict:` | 2292 | 调度员查配对码台账（件一）：状态 unused / redeemed / expired / |
| `pair_code_revoke` | `def pair_code_revoke(code_id: int) -> dict:` | 2333 | 调度员吊销一个未兑换的配对码（件一）；已兑换/已吊销的拒绝。 |

### 房间与消息（13）

| 工具 | 参数签名 | 源码行 | 用途摘要 |
|---|---|---:|---|
| `room_post` | `def room_post(room: str, body: str, kind: str = "chat", attachment_ids: str = "", reply_to_id: int = 0, payload: str = "") -> dict:` | 2691 | 在房间发一条消息。kind: chat/approval_request/question/ |
| `room_create` | `def room_create(name: str) -> dict:` | 2716 | 调度员新建房间。name 校验：非空、长度 ≤40、不含空白与斜杠、 |
| `room_rename` | `def room_rename(room: str, new_name: str) -> dict:` | 2742 | 调度员给房间改名。new_name 校验与 room_create 同口径（非空、≤40、 |
| `room_archive` | `def room_archive(room: str, archived: bool = True) -> dict:` | 2771 | 调度员归档/恢复房间。归档只是列表可见性状态：room_list 默认不返回 |
| `room_list` | `def room_list(include_archived: bool = False) -> dict:` | 2794 | 列出房间（id、name、created_at、archived）及每房最新消息 id |
| `room_summary_get` | `def room_summary_get(room: str) -> dict:` | 2820 | 读房间摘要卡（定稿 §14.1）。任意成员可调；没设过返回三组空列表、 |
| `room_summary_set` | `def room_summary_set(room: str, summary: str) -> dict:` | 2845 | 调度员写房间摘要卡。summary 为 JSON 字符串，结构 |
| `room_read_new` | `def room_read_new(room: str, since_id: int = 0, limit: int = 50, tail: bool = False) -> dict:` | 2874 | 读房间里 id 大于 since_id 的新消息（含作者名）。limit 钳制在 |
| `inbox_pull` | `def inbox_pull(since_id: int = -1, room: str = "") -> dict:` | 2941 | 拉收件箱（staging52 / 件三）：默认拉绑定房内 id 大于服务端 |
| `inbox_mark_read` | `def inbox_mark_read(up_to_id: int = 0, room: str = "") -> dict:` | 2990 | 标已读（staging52 / 件三）：把服务端已读游标推进到 up_to_id |
| `inbox_send` | `def inbox_send(body: str, reply_to_id: int = 0, attachment_ids: str = "", op_key: str = "", ignore_new: bool = False, room: str = "") -> dict:` | 3021 | 从收件箱发消息（staging52 / 件三）：发到绑定房（既有成员须 |
| `message_delete` | `def message_delete(message_id: int) -> dict:` | 3268 | 删除自己发的一条消息（软删）：仅作者本人、仅 kind='chat'。 |
| `message_edit` | `def message_edit(message_id: int, new_body: str) -> dict:` | 3306 | 编辑自己发的一条消息正文：仅作者本人、仅 kind='chat'、未删除、 |

### 附件（3）

| 工具 | 参数签名 | 源码行 | 用途摘要 |
|---|---|---:|---|
| `attachment_put` | `def attachment_put(room: str, filename: str, mime: str, data_base64: str) -> dict:` | 3101 | 上传一张图片附件（staging39 / D18，v1 只收图片）。mime 须为 |
| `attachment_get` | `def attachment_get(attachment_id: int) -> dict:` | 3197 | 取一张附件图片的本体（base64）。任何在册成员可取（房间不做 |
| `attachment_list` | `def attachment_list(room: str, message_id: int = 0) -> dict:` | 3234 | 列房间附件元数据（不含本体）；message_id>0 时只列该消息的。 |

### 任务与调度（20）

| 工具 | 参数签名 | 源码行 | 用途摘要 |
|---|---|---:|---|
| `task_create` | `def task_create(room: str, title: str, body: str, assignee_name: str = "", urgent: bool = False) -> dict:` | 3345 | 调度员建任务卡。assignee_name 非空 = 直接指派（仍需成员 claim 才算数）。 |
| `task_set_urgent` | `def task_set_urgent(task_id: int, urgent: bool) -> dict:` | 3396 | 调度员给未终结任务标/撤加急（定稿 §15.3）。加急只影响 |
| `task_schedule` | `def task_schedule(room: str, title: str, body: str, member_name: str, at_epoch: float) -> dict:` | 3418 | 调度员排班开卡（UI 定稿 §14.2）：任务先开出来、挂到 member_name 名下， |
| `template_upsert` | `def template_upsert(name: str, title_tpl: str, body_tpl: str, assignee: str = "") -> dict:` | 3472 | 调度员新建/更新任务模板（设计稿 TASK-TEMPLATES-DESIGN §2）。 |
| `template_list` | `def template_list() -> dict:` | 3509 | 列出全部任务模板（调度员）。模板是调度员资产，成员不可见。 |
| `template_delete` | `def template_delete(name: str) -> dict:` | 3523 | 调度员删除模板。只删模板本身，已开出的任务不受影响。 |
| `template_fire` | `def template_fire(name: str, arg: str = "", room: str = "pack-main", assignee_override: str = "") -> dict:` | 3540 | 调度员按模板开卡：{arg} 纯文本替换后走 task_create 同路径（状态机/ |
| `task_claim` | `def task_claim(task_id: int) -> dict:` | 3590 | 认领任务（原子）：只有当前为 open、已过退避时间（next_claim_at） |
| `task_update` | `def task_update(task_id: int, claim_token: str, note: str = "") -> dict:` | 3703 | 持有人汇报进度：状态转 working 并续租，可附一句进度说明（进房间消息）。 |
| `task_complete` | `def task_complete(task_id: int, claim_token: str, result: str, envelope: dict &#124; None = None, attachment_ids: str = "") -> dict:` | 3715 | 持有人交活：状态转 review，等调度员验收关单。 |
| `task_block` | `def task_block(task_id: int, claim_token: str, reason: str, fail_class: str = "") -> dict:` | 3801 | 持有人报卡住：状态转 blocked 并说明原因，等调度员处理。 |
| `task_review` | `def task_review(task_id: int, approve: bool, note: str = "") -> dict:` | 3862 | 调度员验收：approve=True 关单(done，清 fail_class/退避)；False 打回 |
| `task_cancel` | `def task_cancel(task_id: int) -> dict:` | 3893 | dispatcher cancels an unfinished task: status=cancelled, claim freed. |
| `task_list` | `def task_list(status: str = "", assignee_name: str = "", offset: int = 0, room: str = "") -> dict:` | 3977 | 列任务（可按状态/被指派人过滤）。运行时靠它找指派给自己成员的活。 |
| `task_clear_backoff` | `def task_clear_backoff(task_id: int) -> dict:` | 4071 | 调度员手动清退避（合成清单第 3 项配套）：任务须为 open，清掉 |
| `task_release` | `def task_release(task_id: int, clear_backoff: bool = False) -> dict:` | 4095 | 调度员把 blocked 任务放回池（合成清单第 3 项配套；补齐既有缺口—— |
| `task_transfer` | `def task_transfer(task_id: int, claim_token: str, to_member: str, reason: str = "") -> dict:` | 4134 | 留痕转交（2026-11-15 群主拍板）：当前持有人把 claimed/working 的 |
| `task_accept_transfer` | `def task_accept_transfer(task_id: int) -> dict:` | 4275 | 接收方领令牌（留痕转交的接手一半）：转交只把任务挂到接收方 |
| `task_transfers_list` | `def task_transfers_list(task_id: int) -> dict:` | 4335 | 调度员查某任务的转交链（留痕转交）：按发生顺序列出每次转交的 |
| `task_audit` | `def task_audit(task_id: int) -> dict:` | 4356 | 调度员查某任务的完整轨迹（定稿 §11.1 任务详情的审计入口）：任务基本 |

### 执行记录与审批策略（11）

| 工具 | 参数签名 | 源码行 | 用途摘要 |
|---|---|---:|---|
| `run_start` | `def run_start(task_id: int, claim_token: str) -> dict:` | 4398 | 持有人开工登记（附录 G.1 第 4 条）：认领成功后、真正动手前调用。 |
| `run_finish` | `def run_finish(task_id: int, claim_token: str, outcome: str = "completed", detail: str = "") -> dict:` | 4442 | 持有人结束本次执行：关闭 run 记录（outcome: completed/failed/aborted）。 |
| `run_list` | `def run_list(task_id: int = 0, limit: int = 50) -> dict:` | 4466 | 调度员查 Run 层执行台账（附录 G.1 第 4 条）：每次执行按 fence 逐次留档， |
| `rule_list` | `def rule_list() -> dict:` | 4487 | 调度员查看批准规则库（附录 G.1）：全部规则，含已停用的。 |
| `rule_upsert` | `def rule_upsert(action: str, target: str, effect: str, source: str = "") -> dict:` | 4499 | 调度员新增/翻转一条批准规则。键=(action, target)：action 是任务标题的方括号 |
| `rule_delete` | `def rule_delete(rule_id: int) -> dict:` | 4523 | 调度员删除一条批准规则；删后同键动作恢复走原有弹问流程。 |
| `egress_rule_list` | `def egress_rule_list() -> dict:` | 4538 | 调度员查看出口站点规则（附录 G.1 第 3 条）：全部规则，含已停用的。 |
| `egress_rule_upsert` | `def egress_rule_upsert(pattern: str, effect: str, source: str = "") -> dict:` | 4551 | 调度员新增/翻转一条出口站点规则。pattern 是域名（如 example.com）， |
| `egress_rule_delete` | `def egress_rule_delete(rule_id: int) -> dict:` | 4575 | 调度员删除一条出口站点规则；删后该站点恢复走点名/弹问判定。 |
| `egress_check` | `def egress_check(url: str, task_id: int = 0) -> dict:` | 4590 | 成员抓取前判定（附录 G.1 第 3 条）：这个 URL 现在能不能直接抓。 |
| `review_file` | `def review_file(task_id: int, target_url: str, owner_quote: str, member_desc: str, verdict: str) -> dict:` | 4635 | 调度员以独立审查者身份对待批任务留痕决议（附录 G.1 第 2 条）。 |

### 记忆（4）

| 工具 | 参数签名 | 源码行 | 用途摘要 |
|---|---|---:|---|
| `memory_read` | `def memory_read(scope: str, key: str = "", member: str = "") -> dict:` | 4696 | 读记忆当前值（附录 G.1 第 5 条）：scope = private-core（L0 核心笔记）/ |
| `memory_write` | `def memory_write(scope: str, key: str, text: str &#124; None = None, source_refs: list &#124; None = None, origin: str = "self", member: str = "") -> dict:` | 4725 | 写记忆（附录 G.1 第 5 条）：整篇改写、每次成功写入全量留版。 |
| `memory_versions` | `def memory_versions(scope: str, key: str = "", limit: int = 20, before_id: int = 0, include_text: bool = False, member: str = "") -> dict:` | 4819 | 查版本链（附录 G.1 第 5 条）：新→旧返回 [{version_id, author, origin, |
| `memory_rollback` | `def memory_rollback(scope: str, key: str, version_id: int, member: str = "") -> dict:` | 4857 | 回滚记忆（附录 G.1 第 5 条）：调度员专属。把目标版本全文作为新版本 |

### 技能（6）

| 工具 | 参数签名 | 源码行 | 用途摘要 |
|---|---|---:|---|
| `skill_publish` | `def skill_publish(name: str, body: str, description: str = "") -> dict:` | 4903 | 发布 skill（Skill 模组 S1，调度员专属）：name 不存在则新建（version=1）， |
| `skill_list` | `def skill_list() -> dict:` | 4961 | 列 skill 目录：调度员看全部；成员只看指派给自己且 enabled 的。 |
| `skill_read` | `def skill_read(name: str) -> dict:` | 4998 | 读 skill 当前版本正文：调度员可读全部；成员只能读指派给自己且 |
| `skill_assign` | `def skill_assign(name: str, member_name: str) -> dict:` | 5068 | 把 skill 指派给成员（调度员专属）：skill 与目标成员都须存在。 |
| `skill_unassign` | `def skill_unassign(name: str, member_name: str) -> dict:` | 5074 | 收回 skill 指派（调度员专属）：置 enabled=0，指派行保留可查； |
| `skill_rollback` | `def skill_rollback(name: str, version: int) -> dict:` | 5080 | 回滚 skill（调度员专属）：把指定旧版本正文作为新版本写回 |

### 桌面锁与心跳（4）

| 工具 | 参数签名 | 源码行 | 用途摘要 |
|---|---|---:|---|
| `desktop_acquire` | `def desktop_acquire(task_id: int, claim_token: str) -> dict:` | 5115 | 拿桌面锁：涉及 GUI 操控（点屏幕 / 打字 / 切窗口）的任务动手前 |
| `desktop_release` | `def desktop_release(task_id: int = 0, claim_token: str = "") -> dict:` | 5155 | 放桌面锁：活干完（或不再需要 GUI）就放，别等到交活。 |
| `desktop_status` | `def desktop_status() -> dict:` | 5190 | 看桌面锁现在在谁手里：{held, holder, task_id, since}。 |
| `heartbeat` | `def heartbeat() -> dict:` | 5202 | 成员心跳：刷新在线时间（调度侧据此判断成员在不在）。 |

## 重构验收规则

每次拆分或迁移后，至少对比：

1. `tools/list` 工具总数、名称集合、描述与 inputSchema。
2. 每个工具的认证身份、角色要求、external 白名单和 access_mode 结果。
3. 读写副作用、事务边界、审计事件与错误返回结构。
4. App、Runtime、Bridge 三类现有调用方的 contract tests。
5. 默认 staging、staging56 聚合回归和 macOS App 构建。

不得以 Python 函数签名相同推断 FastMCP JSON schema、权限或行为相同。