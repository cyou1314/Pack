# M1c：Agent Registry 只读 API 评估

- 状态：接口评估草案；未注册 MCP 工具、未修改现有 member_list、未切换读路径。
- 日期：2026-11-24
- 前置：M1a/M1b 已合入 Foundation；PR #24 的 quick_check / foreign_key_check 预检已合并；PR #34 的 task index migration v3 已合并。
- 发布约束：没有在用户 Mac/生产数据库完成 shadow report 前，不实施或启用 Agent Registry API。

## 1. 目标与非目标

目标是为未来 Pack Agent 管理界面提供稳定 Agent ID 的只读查询面，同时保留 v1 members 兼容层。

本阶段不做：
- 不替换或修改 member_list。
- 不增加 Agent 创建、编辑、删除、连接、凭据或 Session 写接口。
- 不启动 Provider 进程，不触发 Codex/Gemini/OpenAI/Doubao 请求。
- 不读取或合并 Provider 原生私人记忆。
- 不开放 external member 或普通成员访问 Agent Registry。
- 不在本 PR 中改变 App 数据源或 Runtime 主循环。

## 2. 当前接口审计

现有 member_list 是 v1 成员目录接口：返回 member identity、profile、quota、provider login state、access mode、avatar 和 week_done；external 成员有独立字段白名单脱敏。它不是 Agent Registry 的稳定 API，不应直接改成查询 agents。

当前 Hub MCP gate 顺序为 authenticate → external allowlist → access-mode allowlist → handler。现有只读模式与 external allowlist 均按工具名控制；新增 Registry 工具必须显式设计权限，不可默认加入这两个 allowlist。

M1a 的 agents 是 members 的兼容投影，不是认证事实来源。M1b 尚未在真实用户数据库验证，因此当前只允许测试 fixture 和人工只读诊断。

## 3. 建议接口

### 3.1 agent_list

建议签名：agent_list(include_removed=false, provider_id="", limit=100, offset=0)。

返回 agents 数组和 total/limit/offset。每个元素仅包含 agent_id、provider_id、Provider 展示名、display_name、connection_mode、status、created_at、updated_at。

默认不返回 removed Agent；固定排序 updated_at DESC, agent_id ASC；limit 最大 200，offset 不得为负。

### 3.2 agent_get

建议签名：agent_get(agent_id)。

返回 Agent 摘要，以及经过白名单过滤的连接和 Session 元数据：
- Connection：connection_id、mode、auth_kind、status、创建/更新时间与撤销时间。
- Session：session_id、room_id、status、last_event_sequence、创建/更新时间与最后使用时间。

找不到时返回稳定的 not-found 错误，不返回底层 SQL 或路径信息。

## 4. 权限与敏感数据

首版仅 dispatcher 可调用，继续使用 Hub 现有 authenticate() / require(me, "dispatcher")；external 和普通成员不得访问。不将工具加入 EXTERNAL_ALLOWED_TOOLS 或 READONLY_ALLOWED_TOOLS。

绝不返回：
- credential_ref、safe_metadata_json、API Key、OAuth/refresh token、CLI session、Cookie、环境变量；
- Provider 原生 provider_session_id；
- legacy_member_id 和原始 legacy_provider_label；
- members.profile、token_hash、quota、heartbeat、bound room、joined message；
- member_memory / member_memory_versions 及消息、任务正文。

只允许显式列字段，不允许把 SQLite Row 或整行 dict 直接序列化。

## 5. 数据库访问约束

新服务必须使用 SQLite URI mode=ro 并设置 PRAGMA query_only=ON，不得复用默认可写连接。所有输入使用 SQL bind 参数；多表 detail 查询使用同一只读事务快照。成功请求不写业务表、审计表或事件表；拒绝访问沿用 Hub 现有 gate 的拒绝审计。

## 6. 启用门槛

以下条件必须全部满足，才能开启后续 API 实施 PR：
1. 用户 Mac 数据库先完成独立备份；shadow CLI 只对备份副本运行。
2. migration checksum 匹配。
3. SQLite quick_check 为 ok。
4. foreign_key_check 返回 0 行。
5. mismatch_count=0、unmapped_member_count=0、unexpected_legacy_mapping_count=0。
6. 人工检查报告不包含任何凭据、Profile、消息正文、任务正文或私人记忆。
7. 记录数据库版本、报告时间和备份恢复演练结果；不将数据库内容或敏感字段上传到 CI。

## 7. 实施与回滚

通过门槛后，agent_list / agent_get 由单独 PR 实现，默认 feature flag 关闭。CI 必须覆盖 dispatcher/member/external 权限、只读连接写入拒绝、字段白名单、分页稳定性、未知 Agent、空 Registry 和 MCP contract snapshot。

回滚时关闭 API feature flag 或移除新工具；保留 Agent Registry 表和迁移 ledger，不删除或回写旧 members。M1c 完成不代表 M2 Runtime/Event Gateway 已完成，也不授权任何 Provider 高风险工具自动执行。