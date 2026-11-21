# Pack v2 仓库结构审计（2026-11-21）

> 范围：cyou1314/Pack，分支 v2-product-alignment。静态 Git Tree 与文档审计；不代表本机编译、生产服务或真实 Agent 接入验证。
> 纪律：本轮不删除文件、不改生产代码、不迁移历史测试、不触碰生产数据。

## 1. 仓库盘点

| 指标 | 数量 |
|---|---:|
| 仓库条目 | 508 |
| 文件 / 目录 | 440 / 68 |
| tests/hub-staging15–56 | 42 个阶段目录 |
| tests 目录文件 | 249 |
| tests 目录体积 | 约 9.0 MiB |
| Python / Markdown / Swift | 200 / 115 / 17 个文件 |

主要目录：app 56 文件、约 8.74 MiB；hub 19 文件、约 319 KiB；runtime 28 文件、约 146 KiB；tests 249 文件、约 9.02 MiB；docs 30 文件、约 124 KiB。

## 2. 架构轮廓

- app：SwiftUI 客户端、HubClient、数据模型、视图、登录容器、附件、通知和 UI 样机。
- hub：单文件 hub.py 中枢、room_watch.py、SQLite 运维脚本和 systemd 服务。
- runtime：主运行时、闲聊回路、额度采集、Muse4AI bridge、L2 本机交接助手。
- tests：42 份 hub staging 快照，包含历史基线、专项测试和阶段验收证据。

当前仍是“单体 Hub + SwiftUI 客户端 + 多条 v1 执行体/桥接路径”。v2 的 Agent Registry、Adapter SDK、Context Router、本地 Task Engine 尚未形成独立正式模块。

## 3. 审计发现

### A-01 测试快照多且重复（P1）

tests/hub-staging15 至 hub-staging56 共 42 个目录。后期快照会重复包含 hub.py、room_watch.py、pack-agent.mjs 和多轮测试文件。tests 总计约 9 MiB。

这些目录不能直接判定为垃圾：可能含唯一回归用例、历史验收记录、迁移基线或回滚证据。先建立阶段索引（目的、提交、生产部署状态、测试入口、依赖关系），再讨论归档或压缩；禁止直接批量删除。

### A-02 回归入口未覆盖后期 staging（P0）

run-all-suites.sh 默认只执行 15–19、23、24、38–46；--full 额外执行 21。仓库已有 staging47–56，但这些阶段未进入该 runner。GitHub Actions 的 Default staging regression 只运行该脚本。

风险：CI 可能显示全绿，但并未覆盖最近新增的登录态、额度反伪造、成员权限/任务列表等后续阶段测试。下一步必须逐份核对 staging47–56 的 REVIEW 与测试依赖，分类为常驻回归、一次性专项或历史证据，再决定抽取或加入 runner。

### A-03 CI push 分支范围未包含 v2 分支（P1）

.github/workflows/ci.yml 的 push 分支只有 main 与 codex/**，不包含 v2-product-alignment。PR 指向 main 时仍会触发，但直接推送 v2 分支不会自动运行 CI。进入代码改造前，应明确采用 v2 分支 push CI，或 PR-only + 强制检查。

### A-04 README 状态陈述落后（P1）

README 仍把 Pack 定位为“多 Muse 群聊 + 任务调度系统”，现状段写 App 第一批待 Mac 开工；但 UI-REALIGN-BRIEF.md 记录 B1-1…B1-10 多个客户端切片完成，MAC-WORK-RESULT.md 也记录 App 构建和启动。需要把 README 拆为 v1 已验证能力、v2 目标/未实现项、开发测试入口和安全边界，避免重复施工与误导。

### A-05 根目录文档导航拥挤（P2）

根目录包含多个 BRIEF、RESULT、设计稿、研究笔记和产品规格；另有 docs/。直接移动会破坏链接、脚本路径和既有施工约定。先建 docs/INDEX.md，只建立索引；迁移前扫描 Markdown 链接、脚本硬编码路径和外部引用。

### A-06 v1 执行链与 v2 通用架构耦合（P1）

runtime 同时包含通用运行时、Muse4AI 专属桥、额度网页采集、L2 IPC 助手；App 通过 HubClient 直接耦合中枢工具协议；Hub 仍是单文件 hub.py。v2 应把旧桥/ego runtime 视为 v1 adapter，建立通用 Agent Adapter、Context Router 和本地 Task Engine；迁移前保留兼容层。

### A-07 UI 资产体积较大但有验收价值（P2）

app 约 8.74 MiB，包含样机图、头像、动效 GIF 和 App 图标。不能仅按体积删除；应区分运行时资源、设计样机和历史对照资产，再评估非运行时资产是否归档或使用 LFS。

### A-08 CI 测试层次不完整（P1）

当前 CI 覆盖 Hub Python/回归、Runtime Node 语法、macOS Swift build；未见 Runtime 行为测试、Agent Adapter contract tests 或完整端到端流程进入 CI。建议补充 Runtime 行为测试、mock provider contract tests，并区分 Linux 与 macOS 专属测试。

## 4. 清理决策

| 对象 | 建议 | 风险 |
|---|---|---|
| tests/hub-staging15–56 | 先索引，后分类归档 | 高：可能含唯一证据 |
| staging47–56 | 核对后加入常驻回归或标记专项 | 高：漏测 |
| 根目录阶段文档 | 建索引，暂不移动 | 中：链接失效 |
| app/ui-mock | 保留为视觉基准，区分运行时资源 | 中：误删样机 |
| runtime/muse4ai-bridge | 保留为 v1 兼容适配器 | 高：生产执行链依赖 |
| hub/hub.py | 先补领域边界和契约测试，再渐进拆分 | 极高：生产服务 |
| run-all-suites.sh | 核对后期阶段后更新覆盖范围 | 高：误报全绿 |

## 5. 下一步顺序

1. P0：核对 staging47–56 的 REVIEW、测试依赖和生产部署记录，确定常驻回归集合。
2. P1：校准 README 与施工状态，区分 v1 已验证能力和 v2 未实现目标。
3. P2：建立 docs/INDEX.md，只加索引不移动文件。
4. P1：绘制 App / Hub / Runtime 依赖图，明确 v1 兼容层与 v2 模块边界。
5. P1：补齐 Runtime 行为测试 CI 和 Adapter contract tests。

## 6. 未执行/未验证

- 未在 Mac 本机执行 swift build、打包或 UI 实测。
- 未执行 Hub staging 回归、数据库迁移、备份恢复或生产冒烟。
- 未运行 Runtime 行为测试。
- 未验证 ChatGPT / 豆包 Native Adapter 的真实可用通道。
- 未删除文件、移动文档或重构代码。
- 远程 Mac 当前离线，不能进行本机执行验收。

## 7. 结论

当前最重要的技术债不是简单的“文件太多”，而是测试入口没有覆盖后期阶段、项目状态文档落后、v1 专属执行链尚未与 v2 通用 Agent 架构分离。先治理测试与状态，再做可回滚的文档归档和代码拆分。