# tests/ — 中枢回归套件

`hub-stagingNN/` 是中枢历次增量的隔离测试套件（每个含当时的 hub.py 快照、harness 与专项测试）。
根目录的 `run-all-suites.sh` 从这里取套件，在一次性副本里跑，不动正本：

- 默认套件：15 16 17 18 19 23 24 38 39 40 41 42 43 44 45 46
- `--full` 追加：21（对抗 + 300 卡压力）

套件编号与中枢增量一一对应（staging38 = week_done、staging39 = 附件通道、staging43 = kind 白名单…），细节见各目录的 REVIEW 文档。
运行器预期 `skills/` 与其同级（staging23 读技能正本）；生产机上该目录是指向 repo-mirror/skills 的符号链接。
套件目录只放源码，不放运行时状态（pack.db、token、日志由运行器在副本里生成）。

## 当前生产增量专项回归

- staging56（成员访问模式 + 旧库迁移）使用独立脚本：`PY=python ./tests/hub-staging56/run_tests.sh`。
- GitHub Actions 在默认历史回归之后单独执行 staging56，确保当前生产增量不会被旧套件列表遗漏。
- 脚本优先使用环境变量 `PY`，并将同一解释器传给迁移测试；未指定时回退到仓库虚拟环境，虚拟环境不存在则使用系统 Python。
