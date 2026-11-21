# Pack 文档索引

> 维护原则：README 作为入口；根目录保留当前产品规格、设计基线和正在执行的施工单；已结束批次和历史验收记录归档在 docs/history/。本索引只做导航，不代表文档中的每项能力都已部署。

## 当前项目基线

| 文档 | 用途 | 状态口径 |
|---|---|---|
| [README](../README.md) | 项目入口、现有 v1 能力和开发说明 | 需持续校准 |
| [SPEC](../SPEC.md) | v1 产品与中枢机制规格 | 历史基线，修改需标注版本 |
| [Pack v2 产品规格与改造路线](../PACK-V2-PLAN.md) | v2 产品定位、架构和分阶段改造 | 规划，不等于已实现 |
| [Pack v2 仓库审计](../PACK-V2-AUDIT-2026-11-21.md) | 目录、架构与技术债盘点 | 静态审计 |
| [Pack v2 测试治理](../PACK-V2-TEST-GOVERNANCE.md) | 回归分层、staging 分类与 CI 方向 | CI #146 已通过 |\n| [Pack v2 架构图与模块边界](../PACK-V2-ARCHITECTURE-MAP.md) | App / Hub / Runtime 现状、耦合点与拆分顺序 | 静态源码审阅 + CI #146

## 设计与施工基线（根目录）

| 文档 | 主题 |
|---|---|
| [MODULES-DESIGN](../MODULES-DESIGN.md) | v1 扩展模组/插件设计 |
| [TASK-TEMPLATES-DESIGN](../TASK-TEMPLATES-DESIGN.md) | 任务模板机制 |
| [ATTACHMENTS-DESIGN](../ATTACHMENTS-DESIGN.md) | 附件机制 |
| [ATTACH-COMPLETE-DESIGN](../ATTACH-COMPLETE-DESIGN.md) | 交付附件与完整性 |
| [LOGIN-CONTAINER-DESIGN](../LOGIN-CONTAINER-DESIGN.md) | 登录容器设计 |
| [ROOM-SESSION-DESIGN](../ROOM-SESSION-DESIGN.md) | 房间会话设计 |
| [INSTALLER-DESIGN](../INSTALLER-DESIGN.md) | 安装器设计 |
| [UI-IMPL-BRIEF](../UI-IMPL-BRIEF.md) / [UI-REALIGN-BRIEF](../UI-REALIGN-BRIEF.md) | App UI 实施与对齐 |
| [EXTERNAL-AGENT-BRIEF](../EXTERNAL-AGENT-BRIEF.md) | 外部 Agent 接入施工规格 |
| [PORT-QUESTION-BRIEF](../PORT-QUESTION-BRIEF.md) | 结构化提问卡移植规格 |
| [PORT-QUEUE-BRIEF](../PORT-QUEUE-BRIEF.md) | 任务队列显示移植规格 |
| [PORT-ACCESSMODE-BRIEF](../PORT-ACCESSMODE-BRIEF.md) | 成员访问模式移植规格 |
| [OPENBOT-PORTING](../OPENBOT-PORTING.md) | OpenBot 对照与移植研究 |
| [PACHAT-AGENT-INTEGRATION](../PACHAT-AGENT-INTEGRATION.md) | PaChat Agent 集成研究 |

## docs/external-agent/

- `LOCAL-PAIR-EVIDENCE.md`：本地配对验证证据。
- `agent-skill.md`：外部 Agent 技能说明。
- `install-codex.md`：Codex 接入说明。
- `install-external.md`：通用外部 Agent 安装/接入说明。

## docs/history/

此目录存放已结束批次、阶段施工简报、复检与历史研究记录。它们是历史证据，不应被当作当前生产状态的唯一依据。

主要类别：
- **产品/架构演进**：`B2-PLAN.md`、`CHAT-BRIEF.md`、`CHAT-RESULT.md`、`E2-PRECHECK.md`。
- **Mac 与本机执行**：`MAC-BATCH.md`、`MAC-DAY.md`、`LOCAL-BRIEF.md`、`L1-APP-BRIEF.md`、`L2-BRIEF.md`、`L3-BRIEF.md`。
- **Headless 与安装**：`HEADLESS-STEP*-BRIEF.md`、`INSTALLER-BRIEF.md`、`V1-DMG-BRIEF.md`、`V1-DMG-RESULT.md`。
- **E2 探针与验收**：`PROBE-E2-*`、`PROBE-E2B-*`、`SHADOW-S9-RESULT.md`。
- **历史审计/计划**：`AUDIT-2026-11-16.md`、`ARCHIVE-PLAN.md`、`ADDR-SOURCE-BRIEF.md`。

## 根目录文档治理规则

1. 新增施工单前，先确认是否已有同主题规格；避免重复定义接口和验收口径。
2. 进行中的正式规格保留在根目录；批次完成后，将结果和复检记录归入 `docs/history/`，同时保留根目录链接或索引。
3. 不覆盖历史结果；纠错以新增勘误或后续版本说明处理。
4. 文档中的“已完成/已部署/已验收”必须注明证据来源与验证环境；静态审阅、CI 通过、Mac 本机验收和生产部署是不同状态。
5. 暂不移动根目录现有文件。迁移前必须完成 Markdown 链接扫描、脚本路径扫描和外部引用检查。