# Pack v2：Mac 最小改动部署契约

> 目标：先在仓库端完成整改和 CI 验收，再对真实 Mac 做一次有备份、有影子预检、可回滚的集中部署。不得在用户 Mac 上逐批手工打补丁。

## 1. 固定部署边界

| 项目 | 本轮政策 |
| --- | --- |
| 集成分支 | `codex/pack-v2-foundation-20261121`；main 暂不直接合并 |
| Umbrella PR | PR #6 仍是大范围 Draft/dirty，不作为可直接发布的集成包 |
| App / 服务入口 | 保持 `Pack.app`、`hub.py serve`、`room_watch.py`、`backup-db.sh` 入口 |
| 服务路径 | 保持 `~/.pack/local-hub`、`~/.pack/local-engine` |
| 模块路径 | `hub/core/`、`hub/services/` 由 DMG 打包器复制到 Hub payload |
| LaunchAgent | 保留 `com.pack.local-*` 生产标签；测试模式使用独立 `com.pack.installer-test-*` |
| 网络 | Hub `127.0.0.1:8794`、Engine `127.0.0.1:47821` |
| 持久数据 | `pack.db`、attachments、tokens、Keychain、配置、Engine data / 登录态不可随代码覆盖 |
| SQLite 合约 | 当前 Foundation：29 张表；MCP 工具快照 75 项。以最新 contract snapshots 为准 |

## 2. 当前已完成的仓库端安全修复

- PR #25：WAL 仅在 `hub.init()` 配置；普通请求连接仍逐连接启用 foreign keys 与 busy timeout。
- PR #26：`PACK_INSTALL_TEST_ROOT` 下的 LaunchAgent plist 写入 `<test-root>/LaunchAgents`，不再触碰真实 `~/Library/LaunchAgents`。
- PR #28：App Bundle 在临时目录构建和签名，目标目录原子预留；拒绝覆盖已有 App / symlink，并验证 Info.plist 与 codesign。
- PR #29：恢复 staging56 migration / access-mode 独立 CI。
- PR #34：新增 migration v3 的 7 个 task hot-path indexes；v1/v2 checksum 保持不变，并加入旧库升级、query-plan 和 contract snapshot 测试。

以上是仓库和 CI 证据，不代表真实用户 Mac 已完成部署或数据迁移。

## 3. 真实 Mac 部署前的强制门槛

### 3.1 只读 inventory

先运行：

```bash
bash installer/mac-inventory.sh
```

报告写入私有文件，只记录路径元数据、LaunchAgent 标签/入口、端口监听者、SQLite quick_check 与数量；不输出 Token / Keychain 值，不启停服务，不写入 Pack 数据。检查报告路径、权限和内容脱敏后再继续。

### 3.2 备份与影子迁移

1. 区分全新安装与已有安装升级。已有安装必须先确认安装身份、真实数据路径、配置和凭证目录权限；不得先运行卸载器。
2. 在停止所有 Pack 写服务并确认端口释放后，使用 Hub 自带的 SQLite online backup 和附件归档脚本生成完整备份集；验证 manifest、SQLite 完整性和附件归档，并完成一次恢复演练。Engine data、配置、Keychain / 登录态另行保留恢复方案。
   ```bash
   HUB="$HOME/.pack/local-hub"
   PACK_HUB_DIR="$HUB" PACK_BACKUP_DIR="$HUB/backups" /bin/bash "$HUB/backup-db.sh"
   BACKUP_DB="$(ls -1t "$HUB"/backups/pack-*.db | head -1)"
   STAMP="${BACKUP_DB##*/pack-}"
   STAMP="${STAMP%.db}"
   PACK_HUB_DIR="$HUB" PACK_BACKUP_DIR="$HUB/backups" /bin/bash "$HUB/verify-backup.sh" "$STAMP"
   [[ -s "$BACKUP_DB" ]]
   ```
   只有 `verify-backup.sh` 成功且恢复演练通过后，才将该 `BACKUP_DB` 用作下面的 shadow 输入。
3. 不在 live DB 上做 M1a/M1b/M1c 影子检查。将已验证备份数据库复制到独立 staging 目录。
4. 将候选 DMG 的 `hub/` 代码复制到 staging，令备份副本位于 staging 的 `hub/pack.db`；只对这个副本运行候选版本 `hub.py init`，让 migration v1/v2/v3 在副本上应用。
5. 在同一副本上运行只读 Shadow CLI：

```bash
(
set -euo pipefail
DMG_NEW="/Volumes/Pack-candidate"       # 替换为已挂载的候选 DMG
BACKUP_DB="/path/to/verified/pack.db"  # 替换为停机后生成并验证过的备份快照
HUB="$HOME/.pack/local-hub"

[[ -f "$BACKUP_DB" && ! -L "$BACKUP_DB" ]]
[[ -f "$HUB/.dispatcher_token" && ! -L "$HUB/.dispatcher_token" ]]
mkdir -p "$HOME/commander-work"
chmod 700 "$HOME/commander-work"
SHADOW_ROOT="$(mktemp -d "$HOME/commander-work/pack-shadow.XXXXXX")"
chmod 700 "$SHADOW_ROOT"
REPORT="$(mktemp "$HOME/commander-work/agent-registry-shadow.XXXXXX")"
trap 'rm -rf "$SHADOW_ROOT"' EXIT

ditto "$DMG_NEW/hub" "$SHADOW_ROOT/hub"
cp "$BACKUP_DB" "$SHADOW_ROOT/hub/pack.db"
cp "$HUB/.dispatcher_token" "$SHADOW_ROOT/hub/.dispatcher_token"
chmod 600 "$SHADOW_ROOT/hub/pack.db" "$SHADOW_ROOT/hub/.dispatcher_token"

"$DMG_NEW/python/bin/python3" "$SHADOW_ROOT/hub/hub.py" init

set +e
PYTHONPATH="$SHADOW_ROOT/hub" \
  "$DMG_NEW/python/bin/python3" \
  "$SHADOW_ROOT/hub/services/agent_registry_shadow.py" \
  --db "$SHADOW_ROOT/hub/pack.db" \
  > "$REPORT"
SHADOW_RC=$?
set -e
chmod 600 "$REPORT"
cat "$REPORT"
[[ "$SHADOW_RC" -eq 0 ]]
"$DMG_NEW/python/bin/python3" -c '
import json,sys
r=json.load(open(sys.argv[1],encoding="utf-8"))
assert r.get("ok") is True and r.get("ready") is True
assert r.get("migration_version") == 3
assert r.get("applied_migration_versions") == [1, 2, 3]
assert r.get("migration_checksum_matches") is True
assert r.get("database_integrity_ok") is True
assert r.get("foreign_key_violation_count") == 0
assert r.get("mismatch_count") == 0
assert r.get("unmapped_member_count") == 0
assert r.get("unexpected_legacy_mapping_count") == 0
' "$REPORT"
)
```

Shadow CLI 以 SQLite `mode=ro` + `query_only=ON` 打开指定副本。候选 `hub.py init` 是唯一会写这个 staging 副本的步骤；不要把 `BACKUP_DB` 指向 live DB。

6. 报告必须同时满足：
   - `ok=true`、`ready=true`；
   - migration checksum 全匹配，已应用版本包含当前最新 migration；
   - `database_integrity_ok=true`，即 `quick_check=ok`；
   - `foreign_key_violation_count=0`；
   - `mismatch_count=0`；
   - `unmapped_member_count=0`；
   - `unexpected_legacy_mapping_count=0`。
7. 人工检查报告不得包含 Token、Profile、消息正文、任务正文或私人记忆。报告仅在本机私有目录保存，不上传 CI 或外部服务。

任一条件不满足：停止发布，不开放 M1c API，不在 live DB 上重试迁移。先查明原因并重新从备份副本验证。

### 3.3 隔离 Mac 验收

只有仓库 CI 全绿、备份可恢复、shadow report 全绿后，才在可丢弃的测试 Mac 或专用 macOS 用户账户上验证完整 DMG：

- 已有安装身份识别和路径保护；
- 只替换程序文件，不覆盖数据库、attachments、Token、配置和 Engine data；
- LaunchAgent 停止、替换、启动、健康检查与失败回滚；
- Hub 通过 dispatcher token 的只读 MCP `room_list` 检查；Engine 通过本地端口检查；
- App Bundle 版本、签名和启动；
- Keychain / 真实账号登录态保留；
- 升级失败时程序/App 回滚，live DB / attachments 不自动回滚，避免覆盖升级期间可能接受的新写入；已验证快照保留供人工恢复。

真实账号登录、Keychain ACL 和系统授权不能由 Linux CI 或 mock 测试替代。

## 4. 隔离安装测试

仅在可丢弃的测试 Mac 或专用 macOS 用户账户执行。当前 `PACK_INSTALL_TEST_ROOT` 会隔离：

- Hub / Engine / config / App Bundle / handoff socket；
- 独立端口 18795 / 47822；
- 独立 LaunchAgent 标签 `com.pack.installer-test-*`；
- LaunchAgent plist 目录 `<test-root>/LaunchAgents`。

测试模式不再写入真实用户的 `~/Library/LaunchAgents`。不要手工复制或编辑真实用户 plist 来模拟测试。

```bash
DMG_OLD="/Volumes/Pack-old"
DMG_NEW="/Volumes/Pack-candidate"
export PACK_INSTALL_TEST_ROOT="$HOME/commander-work/pack-upgrade-acceptance"

# 在测试根目录安装旧包并创建一套可升级的测试数据
"$DMG_OLD/python/bin/python3" "$DMG_OLD/manager.py" install

# 模拟首次安装时把 Pack.app 放入测试 Applications
mkdir -p "$PACK_INSTALL_TEST_ROOT/Applications"
ditto "$DMG_OLD/Pack.app" "$PACK_INSTALL_TEST_ROOT/Applications/Pack.app"

# 使用候选 DMG 执行真实 macOS 文件替换、LaunchAgent 启停与健康检查
"$DMG_NEW/python/bin/python3" "$DMG_NEW/manager.py" upgrade

# 停止并移除测试服务/程序；测试数据和备份仍保留
"$DMG_NEW/python/bin/python3" "$DMG_NEW/manager.py" uninstall
```

验收时检查测试根目录下的 `local-hub/backups/upgrade-*`、SQLite 完整性、附件数量、Engine data、四个测试 LaunchAgent、测试 `LaunchAgents` 目录和 18795/47822 端口。测试安装不验证真实 App Keychain 迁移或生产登录态；两项仍需人工验收。测试数据目录不会自动删除，确认不再需要后才手动清理。

## 5. 当前部署状态

- Foundation 仓库整改与 PR #25/#26/#28/#29/#34/#35/#36 已合并，CI 已验证对应代码和 macOS Release App Bundle 构建。
- 用户 Mac 当前远程设备离线；尚未执行真实 inventory、数据库备份、shadow report、Keychain 检查或安装升级。
- 不部署生产 Mac，不在真实数据库上运行 `hub.py init`，直到 3.1–3.3 全部完成并得到明确验收记录。
