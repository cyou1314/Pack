# Pack v2：Mac 最小改动部署契约（草案）

> 目标：仓库内先完成整改与 CI 验收，随后集中进行一次 Mac 部署验证；不在用户 Mac 上逐批手工打补丁。

## 固定部署边界

| 项目 | 本轮政策 |
| --- | --- |
| 主仓库 | `main` 暂不合并；PR #7 为持续审计整改分支 |
| App 与服务入口 | 保持 `Pack.app`、`hub.py serve`、`room_watch.py`、`backup-db.sh` 入口 |
| 服务路径 | 保持 `~/.pack/local-hub`、`~/.pack/local-engine` |
| 运行时模块 | `hub/core/`、`hub/services/` 由现有打包器复制，无新增外部路径 |
| LaunchAgent | 保留既有 `com.pack.local-*` 标识与 plist 安装约束 |
| 网络 | 保留 Hub 127.0.0.1:8794、Engine 127.0.0.1:47821 |
| 持久数据 | `pack.db`、attachments、tokens、Keychain、配置与 Engine 登录数据不可随代码覆盖 |
| 数据格式 | 本批不改变 24 张 SQLite 表及 75 个 MCP 工具快照 |

## 实际部署前检查

1. 读取现有 Mac 上 Pack 服务、安装来源、launchd 标签、App 版本和数据目录，生成只读 inventory；不要先运行卸载器。
2. 区分**全新安装**与**已有安装升级**。现有 `local/install-local.sh` 和 `installer/manager.py install` 都拒绝覆盖已存在实例，不能直接当作升级器。需先实现并验证受控升级路径，不得手工覆盖原始数据。
3. 验证数据库一致性、可用备份、附件与 Keychain/登录态可恢复；对部署包进行 checksum 与工具签名/打包 smoke test。备份和回滚条件未满足时不部署。
4. 在隔离 Mac 上做一次完整 DMG 验证：已有安装识别、保留用户配置、只升级程序文件、LaunchAgent 启停与失败回滚、真实账号登录；通过后才评估生产 Mac。
5. 优先把所有仓库端整改合入一个可核验的发布候选包；避免为了每个 Hub 服务拆分步骤多次修改真实 Mac。

## 本次整改与未来工作

- 本轮只修改仓库中的 Hub Python 服务/模块、SwiftUI App 内部结构、自动化测试、CI 与文档；不执行本机安装/升级，不修改安装器、LaunchAgent、Keychain service/account、用户配置格式和现有数据库、附件及登录态数据。
- 需要后续单独完成的事项：已有安装差异升级与回滚机制、升级前只读预检、Mac 沙盒/隔离机器验收；不能把这些事项当作已经验证通过。
