# Pack 测试治理与回归矩阵（2026-11-21）

> 基于仓库静态审阅，不代表本轮实际运行测试。所有历史 staging 目录保留不动。

## 1. 关键结论

当前默认 CI 回归入口 run-all-suites.sh 只跑到 staging46；后期 staging47–56 没有被入口调度。最新 staging56 的 run_tests.sh 实际聚合了 access_mode、新旧迁移、staging51/52/53/54/55 与负载测试，但它写死本机路径 ~/workspace/pack/hub/.venv/bin/python，并依赖 ss、nohup 等 Linux/VM 环境工具，不适合直接作为 GitHub Actions 通用入口。

因此不能简单把 staging56/run_tests.sh 原样挂进 CI。应先把测试执行器与环境配置解耦，再让 CI 使用可注入的 Python 路径、临时目录和端口分配。

## 2. 后期阶段静态分类

| 阶段 | 主题 | 代码/测试特征 | 当前入口状态 |
|---|---|---|---|
| 47 | provider / login_state | 成员 provider、登录状态迁移和写接口 | 未进入根 runner |
| 48 | quota self-report / unclaimed sweeper | 新工具 member_quota_report、额度反伪造、未认领任务巡检；REVIEW 标注未部署/未提交 | 未进入根 runner；需区分候选与生产 |
| 49 | Hub 修复 | member_list 连接关闭、removed_at 写校验、task_list room 字段 | 未进入根 runner |
| 50 | task_list 多状态 / room_read_new tail | 两个既有工具参数扩展；REVIEW 记录已部署 | 未进入根 runner |
| 51 | External Agent Phase A | 配对码、外部成员、令牌轮换、收件箱底座 | 其后续 staging52/56 runner 会复用测试 | 未进入根 runner |
| 52 | External Agent Phase B | inbox 工具与 pack-agent.mjs | staging56 runner 会复用测试 | 未进入根 runner |
| 53 | 结构化提问卡 | question/question_answer、payload、迁移、负载 | staging56 runner 会复用测试 | 未进入根 runner |
| 54 | queue_ahead | task_list 队列位次字段 | staging56 runner 会复用测试 | 未进入根 runner |
| 55 | reply_to_id readback | room_read_new 回读引用字段 | staging56 runner 会复用测试 | 未进入根 runner |
| 56 | access_mode | full/workspace/readonly 权限模式、迁移、审计与回归链 | 有专用 run_tests.sh；REVIEW 标注仅 staging、未部署/未提交 | 未进入根 runner |

注意：阶段目录是开发/验收快照，不等于生产当前代码。尤其 staging48、51、52、53、54、55、56 的 REVIEW 中包含“未部署/未提交”或待裁定记录；必须以最新部署记录和 main 的 hub.py 为准，不能把候选能力写成已上线。

## 3. 根 runner 的现状与问题

- run-all-suites.sh 使用临时副本执行历史套件，这个隔离设计应保留。
- 默认集合：15、16、17、18、19、23、24、38、39、40、41、42、43、44、45、46；--full 额外执行 21。
- 该 runner 不执行 47–56，也不执行 runtime/tests 下的 Node 行为测试。
- staging48 REVIEW 记载当时以候选 hub.py 跑旧回归时，唯一失败为 staging15 对工具数 64 的硬断言；说明工具数量硬编码会使新工具演进时产生噪音，宜改为针对能力/清单的断言，或由版本化 manifest 管理。
- staging51 REVIEW 同样提示 staging15 的工具数断言需要随新增工具同步；后续副本出现 74/75 口径，进一步说明断言漂移是重复维护成本。
- staging56/run_tests.sh 聚合了较完整的链，但路径、端口和进程管理方式绑定原始 VM，不可直接视为 CI-ready。

## 4. 建议的目标测试分层

### Layer 1：快速 PR Gate（每次提交）

- Python compile + Hub 单元/工具契约测试。
- Node --check + runtime/tests 行为测试。
- Swift build（macOS runner）。
- Shell syntax、JSON/plist 格式检查。

### Layer 2：隔离 Hub 回归（PR 或 nightly）

- 保留 run-all-suites.sh 的临时副本策略。
- 由 staging56 代表性回归链提供当前 schema/权限/收件箱/任务/消息兼容性覆盖。
- 专项迁移测试每个版本独立 DB，不共享夹具。
- 并发、权限绕过、外部 Agent 访问边界作为不可跳过的安全测试。

### Layer 3：端到端与本机验收（发布前）

- macOS App + 本地 Hub + Runtime 的完整任务闭环。
- Agent adapter mock contract tests 与至少一个真实 Native / 一个 API 适配器的连接验收。
- Keychain 凭证、备份恢复、断网重连、任务取消/重试、文件权限与审计检查。
- 真实第三方账号验证由用户本人完成，不在 CI 中使用个人 Cookie 或账号凭证。

## 5. 执行前必须修复的测试基础设施

1. 所有 runner 支持 PYTHON=/path/to/python 或 PY 环境变量，不写死开发机绝对路径。
2. 端口由系统分配或通过参数注入，避免多个 CI job 并发冲突。
3. 禁止使用宽泛 pkill -f；仅按 PID/进程组或专属临时端口清理，防止误杀生产服务。
4. 所有测试运行在临时目录，测试 DB、token placeholder、附件和日志退出时清理。
5. 统一输出机器可读的 suite / pass / fail / skip 摘要；被跳过的测试必须显式列出原因。
6. 工具数不应以散落在多个历史测试文件中的常量维护；改用当前工具 manifest 或能力断言。
7. 将 runtime/tests 纳入 CI 行为测试，不止执行 node --check。

## 6. 本轮未做

- 未修改 run-all-suites.sh 或任何 staging 测试文件。
- 未执行 staging56/run_tests.sh 或根 runner。
- 未修改 CI workflow。
- 未判断 staging48、51–56 的候选改动是否应部署；需要以 main 当前代码和生产部署记录再核。

## 7. 推荐下一步

先把 staging56 聚合链拆成可移植 runner（环境变量、动态端口、临时目录、PID 精确清理），在隔离环境跑通后，将其作为当前 Hub 回归主链；再把旧 runner 的历史专项按需保留为兼容/迁移回归。之后补 Runtime 行为测试 CI。