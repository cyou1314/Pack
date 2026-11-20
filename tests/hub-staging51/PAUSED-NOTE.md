# staging51 PAUSED-NOTE（2026-11-20 14:0x 暂停）

暂停原因：群主改节奏——先与 Gemini 多商讨几轮打磨计划，不急定稿施工。本目录一切工作冻结，等新定稿再重启或调整。**未提交、未推送、未碰生产。**

## 已完成（只有准备工作，零代码改动）

1. 通读权威规格 `EXTERNAL-AGENT-BRIEF.md` v2（提交 8448ba0）全文，重点件一/件二与「施工切分 / 验收」。
2. 通读慢工复检技能 `adversarial_hub_review/SKILL.md`。
3. 精读生产 `hub/hub.py`（3905 行）的全部相关段落：SCHEMA、init() 迁移范式（PRAGMA 检查逐列 ADD COLUMN）、authenticate/require、_avatar_combo/_used_combos、_auto_relay、sweep_once（含 staging48 未认领巡检）、member_register/remove/avatar_set、room_post/list/read_new、room_summary_get、attachment_put/get/list、task_create/claim/list/transfer/accept_transfer、member_list、desktop_acquire/release/status、heartbeat、serve()。
4. **实证 FastMCP 4.1.8 支持 `custom_route`**（venv 内 inspect 确认签名 `(path, methods, name, include_in_schema)`，starlette JSONResponse/Request 可用）→ `POST /pair` 可走真公开路由，无需退到免鉴权 MCP 工具方案。
5. 看过 staging50 的 harness 范式（fastmcp Client + StreamableHttpTransport + .dispatcher_token）。
6. 建目录 `tests/hub-staging51/`，从生产复制 `hub.py` 一份，md5 与生产一致（`f1a8cff1e920e23e3b1ce0af4ec2cf9e`）。

## 改到一半的是什么

**没有改到一半的代码**：staging 副本 hub.py 与生产逐字节相同，一个字未动。未起过隔离服务（端口 18798 未用）、未建 DB、未写测试、未写 REVIEW51。生产 hub.py / pack.db / 任何生产服务均未触碰。

## 实现笔记（重启时可直接接续的设计草案）

- **迁移**（沿 init() 既有范式）：members 加 `kind` / `display_name` / `bound_room_id`；messages 加 `reply_to_id`；SCHEMA 内 `CREATE TABLE IF NOT EXISTS` 新表 `member_read_cursors`（Phase A 只建表）、`pair_codes`（只存码的 sha256 哈希；字段草案：id、code_hash UNIQUE、member_name、provider、bound_room_id、display_name、created_at、expires_at、redeemed_member_id/redeemed_at、revoked_at、fail_count、locked_until）。
- **新工具 4 个**：pair_code_create / pair_code_list / pair_code_revoke / member_rename（只改 display_name，内部 name 不动）。工具数 65 → **69**，部署时须同步 staging15 的工具数硬断言（REVIEW51 要点明）。
- **POST /pair**（custom_route，独立于 authenticate）：只验码 → 建/轮换 external 成员 → 一次性返回 token（中枢只存哈希）。防扫两层：按码失败计数超限锁定该码 + 全局失败滑窗；所有失败统一模糊报错、绝不回显码/token；全程审计。头像复用 `_avatar_combo(name, _used_combos(conn))` + `avatar_for` 合并（同 member_register 口径）。
- **重配轮换**：同 member_name 的新码兑换 → 复用成员行、轮换 token_hash，旧 token 立即失效；同码二次兑换必拒（一码一成员）。
- **硬隔离挂点**（仅 kind=external 生效，既有成员路径不动）：
  - room_read_new / room_post / room_list / task_list：限 bound_room_id。
  - attachment_get：按 attachments.room_id 校验（该表有 room_id 列）。
  - task_accept_transfer：校验任务 room_id == bound_room_id。
  - desktop_acquire：external 直拒（放在 _holder 校验之前）。
  - task_claim：external 只许 assignee == 自己，且任务带引擎标记不得认领（现成常量 `EXECUTOR_MARKERS = ("[M4A]", "[ENG]", "[ENG-image]")`）。
  - _auto_relay：候选查询现为 `role='member' AND removed_at IS NULL AND name<>'owner'…`——**external 的 role 同样是 'member'，必须加 `kind` 排除**，否则接力会把卡送给外部成员（Gemini 评审已点名此旁路）。
  - sweeper 未认领巡检：候选经 _auto_relay 出口，加 kind 排除后接力路径即堵；但巡检对「指派给 external 的卡」还会走升级消息路径，见疑点 1。
- **限流底座**：room_post 内对 external 按 messages 表 author_id + created_at 窗口计数（60s ≤5、3600s ≤20、86400s ≤100），超限硬拒 + 审计 + 错误载荷带 retry_after（可仿 desktop_busy 的 JSON 载荷报错范式）。
- **移除失效**：authenticate() 已对 removed_at 非空直接拒 → member_remove 对 external 天然成立，测试时复核即可。

## 已发现的疑点（待新定稿时一并定）

1. **未认领巡检的指派侧口径**：施工单说「巡检候选排除 external」——明确的是接力*接收方*排除；但指派给 external 的卡长期未认领时，巡检是否仍向调度员发升级提示？现巡检逻辑是「接力不成 → 发升级消息」，external 指派卡接力必不成，会每轮触发升级（有去重审计，一卡只升一次）。倾向：巡检查询层面对 external 指派卡也跳过，升级与否另定。
2. **pair_code_create 与既有名字的关系**：绑定名若已被既有 Muse 成员占用必须拒；若已是在册 external 成员则应允许（重配轮换用途）；若是已移除成员的名字，按 member_register 既有口径「reserved」拒。施工单未逐字写，属推定。
3. **display_name 何时定**：件一说码绑「内部名 / provider / 绑定房间」，未明说 display_name 是否也预绑。草案：create 时可选带 display_name，缺省留空、事后 member_rename 补。
4. **member_list 是否本期带新字段**：kind / display_name / bound_room_id 加进 member_list 返回对 App 显示有用，施工单 Phase A 未点名。加是纯增量（staging49 先例），倾向加，等定稿确认。
5. **task_claim 引擎标记判定范围**：_auto_relay 用「标题 startswith 标记」口径，桥的认领过滤是「正文含标记」。施工单只说「带引擎标记的任务」。草案取更严口径：标题+正文全文含标记即拒，与桥一致；待定稿确认。
6. **/pair 锁定语义**：按码失败锁定锁的是码本身；伪造码（库里不存在）只能靠全局滑窗兜底，两层缺一不可。锁定期间连持码者本人兑换也被拒——24h 有效期内若被爆破锁死，需调度员吊销重发，此运营后果建议在定稿里写明。
