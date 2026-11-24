# Pack v2 文档索引

本目录是 Pack v2 的产品与技术基准。

- PRD.md：产品定位、范围、功能需求和验收场景。
- ARCHITECTURE.md：控制平面、执行平面、事件和安全架构。
- PROVIDER-MATRIX.md：各 Provider 的已知能力、待验证事项和限制。
- IMPLEMENTATION-PLAN.md：迁移策略、实施批次、任务优先级和架构决策。
- REMEDIATION-ROADMAP.md：仓库级安全、部署、打包与验收整改路线。
- MAINLINE-RECONCILIATION-AUDIT.md：main 与 v2 Foundation 的基线差异、冲突风险和分批移植队列。
- PIBOT-INTEGRATION-EVALUATION.md：PiBot 的 MCP / ACP 接入价值、ChatGPT OAuth 边界与安全 POC 方案。
- PHASE4-MODULE-BOUNDARIES.md：Hub 与 macOS App 的模块边界和拆分进度。
- MAC-MINIMAL-CHANGE-DEPLOYMENT.md：Mac 既有安装保护、升级/回滚和隔离验收要求。
- M1C-AGENT-READ-API-DESIGN.md：Agent Registry 只读 Shadow preflight 门槛和 API 启用条件。
- M2-RUNTIME-SUPERVISOR-DESIGN.md：Runtime Supervisor 生命周期、事件持久化与审批边界。
- M2-AGENT-EVENT-GATEWAY.md：Agent 专属配对凭证、事件写入 HTTP 契约和安全限制。
- M2-OUTBOX-DELIVERY-WORKER.md：本地 Outbox 到 Hub Event Gateway 的持久化投递、重试与 ACK 语义。
- M2-INBOX-CONSUMER-DESIGN.md：Hub Inbox 下游 Consumer 的 Lease、连续游标、部分 ACK、幂等和故障注入设计（尚未实现）。
- PIBOT-REFERENCE-ASSESSMENT.md：PiBot 源码级参考评估、可借鉴模式与不引入边界。

文档优先级：
1. v2 产品方向以 PRD 和架构决策为准。
2. v2 技术边界以 ARCHITECTURE 和 Agent Core 代码契约为准。
3. Provider 能力以 PROVIDER-MATRIX 的实测证据为准。
4. SPEC.md 与历史 Brief 保留 v1 协议和兼容要求，不自动视为 v2 产品约束。

所有未通过真实连接和端到端验收的 Provider 能力必须标记为待验证。