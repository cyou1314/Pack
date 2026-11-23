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
2. 区分**全新安装**与**已有安装升级**。DMG 新增 `upgrade.command` / `manager.py upgrade`：先验证现有安装身份并暂存/验证新程序，再停止所有 Pack 写服务、确认端口释放，随后在线备份 SQLite/附件，最后替换代码与 App；新服务启动失败时回滚旧程序与 App，但不自动覆盖 live DB/attachments，避免丢弃启动窗口内已接受的写入；经验证的预升级快照保留供人工恢复；Hub 通过 dispatcher token 认证的只读 MCP `room_list` 调用，Engine 通过本地端口检查后才视为启动成功。该路径目前仅有仓库级实现，仍须在隔离 Mac 验证；`local/install-local.sh` 仍只用于全新开发安装。
3. 验证数据库一致性、可用备份、附件与 Keychain/登录态可恢复；对部署包进行 checksum 与工具签名/打包 smoke test。备份和回滚条件未满足时不部署。
4. 在隔离 Mac 上做一次完整 DMG 验证：已有安装识别、保留用户配置、只升级程序文件、LaunchAgent 启停与失败回滚、真实账号登录；通过后才评估生产 Mac。
5. 优先把所有仓库端整改合入一个可核验的发布候选包；避免为了每个 Hub 服务拆分步骤多次修改真实 Mac。

## 本次整改与未来工作

- 本轮修改仓库中的 Hub Python 服务/模块、SwiftUI App 内部结构、DMG 安装器的非破坏式 upgrade 命令、自动化测试、CI 与文档；不执行真实 Mac 安装/升级，不改动用户 Mac 上的 LaunchAgent、Keychain service/account、配置、数据库、附件及登录态数据。
- 部署前先运行 `bash installer/mac-inventory.sh` 生成脱敏只读盘点；报告不包含 Token/Keychain 值，也不启停服务或写入 Pack 数据。随后在隔离 Mac 验证升级前预检、停机后在线备份、程序文件替换、认证 Hub/Engine 健康检查、失败回滚及 Keychain/登录态保留；仓库测试不能替代真实 Mac 验收。备份在所有 Pack 写服务停止后生成，以包含 staging 期间已接受的写入。若新服务启动失败，只回滚程序文件/App；live DB/attachments 保持当前内容，避免覆盖启动窗口内可能已接受的新写入，已验证快照保留供人工恢复。当前升级不执行 SQLite schema migration；未来若需改表，必须先设计独立、可逆的版本化迁移流程。


## 隔离 Mac 验收操作

仅在**可丢弃的测试 Mac 或专用 macOS 用户账户**执行。`PACK_INSTALL_TEST_ROOT` 会把 Pack 数据目录、Engine 数据和 App Bundle 放入测试根目录，并使用独立端口 18795/47822、独立 LaunchAgent 标签 `com.pack.installer-test-*`；但 LaunchAgent plist 仍写入当前用户的 `~/Library/LaunchAgents`，因此不要在生产会话中执行。

```bash
DMG_OLD="/Volumes/Pack-old"
DMG_NEW="/Volumes/Pack-candidate"
export PACK_INSTALL_TEST_ROOT="$HOME/commander-work/pack-upgrade-acceptance"

# 在测试根目录安装旧包，创建一套可升级的测试数据
"$DMG_OLD/python/bin/python3" "$DMG_OLD/manager.py" install

# 模拟首次安装时把 Pack.app 拖入 Applications（manager.py install 只安装后台服务）
mkdir -p "$PACK_INSTALL_TEST_ROOT/Applications"
ditto "$DMG_OLD/Pack.app" "$PACK_INSTALL_TEST_ROOT/Applications/Pack.app"

# 使用候选 DMG 执行真实 macOS 文件替换、LaunchAgent 启停与健康检查
"$DMG_NEW/python/bin/python3" "$DMG_NEW/manager.py" upgrade

# 完成后停止并移除测试服务/程序；测试数据库、附件、Token、配置和备份仍保留
"$DMG_NEW/python/bin/python3" "$DMG_NEW/manager.py" uninstall
```

验收时检查测试根目录下的 `local-hub/backups/upgrade-*`、SQLite 完整性、附件数量、Engine data、四个测试 LaunchAgent 和 18795/47822 端口。测试安装不会验证真实 App Keychain 迁移或生产登录态；这两项仍需单独做人工验收。测试数据目录不会自动删除，只有确认不再需要后才手动清理。
