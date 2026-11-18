# Pack systemd units

仓库现在提供并可从干净 clone 恢复的基础服务：

- `pack-hub.service`：FastMCP Hub
- `pack-room-watch.service`：房间消息 → 任务卡转换器
- `pack-db-backup.service` / `pack-db-backup.timer`：SQLite + attachments 备份

`pack-tunnel` 与 `pack-url-publisher` 不放进仓库正本，因为它们依赖你当前 VM 的 Pinggy/凭证/发布脚本，属于环境专属组件。`hub/install-services.sh` 不再假定这些文件存在；如果部署环境提供对应 unit，会明确提示但不会擅自覆盖。

安装：

1. 确保 `hub/.venv/bin/python` 已存在，并且 `PACK_USER` 对应实际运行用户。
2. 运行 `sudo hub/install-services.sh`。
3. 运行 `PACK_PREFLIGHT_SYSTEMD=1 hub/preflight.sh`。
4. 首次部署后手动执行一次 `hub/backup-db.sh`，再执行 `hub/verify-backup.sh`。

systemd 模板使用 `@PACK_ROOT@` / `@PACK_USER@` 占位符，由安装脚本写入 `/etc/systemd/system/` 时替换。
