#!/bin/bash
# 从仓库内的 systemd 模板安装 Pack 的可复现基础服务。
# Tunnel / URL publisher 属于环境专属组件，不再假定仓库里一定存在它们。
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "请以 root 运行：sudo hub/install-services.sh" >&2; exit 1; }
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PACK_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PACK_USER="${PACK_USER:-}"
if [ -z "$PACK_USER" ]; then
  PACK_USER="$(stat -c '%U' "$PACK_ROOT" 2>/dev/null || stat -f '%Su' "$PACK_ROOT")"
fi
[ -n "$PACK_USER" ] && [ "$PACK_USER" != "root" ] || {
  echo "无法安全确定运行用户；请显式设置 PACK_USER=<服务用户>" >&2
  exit 1
}

UNIT_DIR="$PACK_ROOT/hub/systemd"
TARGET_DIR="/etc/systemd/system"
UNITS="pack-hub.service pack-room-watch.service pack-db-backup.service pack-db-backup.timer"

for unit in $UNITS; do
  src="$UNIT_DIR/$unit"
  [ -f "$src" ] || { echo "缺少仓库 unit：$src" >&2; exit 1; }
  sed     -e "s|@PACK_ROOT@|$PACK_ROOT|g"     -e "s|@PACK_USER@|$PACK_USER|g"     "$src" > "$TARGET_DIR/$unit"
  chmod 0644 "$TARGET_DIR/$unit"
done

systemctl daemon-reload
systemctl enable --now pack-hub.service
systemctl enable --now pack-room-watch.service
systemctl enable --now pack-db-backup.timer

echo "Pack core systemd services installed."
for optional in pack-tunnel.service pack-url-publisher.service pack-url-publisher.timer; do
  if [ -f "$UNIT_DIR/$optional" ]; then
    echo "NOTE: optional environment unit present but not auto-installed: $optional"
  else
    echo "NOTE: environment-specific unit not shipped: $optional"
  fi
done
