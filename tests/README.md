# Tests

本目录区分 **CI 活跃回归套件** 与 **历史隔离快照**，不要混用。

## CI 活跃套件

由仓库根目录的 `run-all-suites.sh` 驱动。每个套件复制到临时目录运行，不覆盖仓库中的快照。

- 默认：15、16、17、18、19、23、24、38、39、40、41、42、43、44、45、46
- `--full`：默认套件 + 21（对抗与压力测试）

运行方式：

```bash
PY=python ./run-all-suites.sh
PY=python ./run-all-suites.sh --full
PY=python ./run-all-suites.sh --list
```

`hub-stagingNN/` 仅保留当前回归运行器实际使用的套件。套件中的 Hub 快照、harness 和专项测试是隔离测试输入，不是生产 Hub 的替代品。

## 历史快照

`archive/hub-stagingNN/` 保存未纳入当前默认/完整 CI 链的历史阶段快照、专项实验和复核证据。

- 不由当前 `run-all-suites.sh` 自动执行。
- 不得把其中的 `hub.py` 复制到生产环境。
- 原始 REVIEW 文档保留当时的测试路径和结论，作为历史证据。
- 如需恢复某个历史套件，应先检查依赖、端口、环境变量和测试数据，再单独运行；不要直接把它加入 CI。

## 文件纪律

测试目录只保存源码、夹具和必要的复核文档。数据库、token、附件、日志、测试输出和临时目录均由运行器在临时副本中生成，并由 `.gitignore` 排除。


## Hub 数据层回归

Hub 的模块化服务测试由 `.github/workflows/ci.yml` 单独驱动，不属于历史 staging 快照目录。`hub/test_db_query_indexes.py` 验证 audit/approval 的结构化 task_id 回填、WAL 初始化时机，以及任务队列、审计、审批和转交过期查询的索引计划；测试只使用临时 SQLite 数据库。
