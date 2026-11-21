#!/usr/bin/env bash
# Install the repository's systemd units and restart long-running services so
# changed identities and sandbox settings take effect immediately.
set -Eeuo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "ERROR: run this installer as root (for example: sudo $0)" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="$SCRIPT_DIR/systemd"
UNIT_DEST="/etc/systemd/system"
UNITS=(
  pack-hub.service
  pack-room-watch.service
  pack-tunnel.service
  pack-url-publisher.service
  pack-url-publisher.timer
  pack-db-backup.service
  pack-db-backup.timer
)

for unit in "${UNITS[@]}"; do
  source="$UNIT_DIR/$unit"
  [[ -f "$source" ]] || { echo "ERROR: missing unit: $source" >&2; exit 1; }
  install -o root -g root -m 0644 "$source" "$UNIT_DEST/$unit"
done

systemctl daemon-reload
systemctl enable \
  pack-hub.service \
  pack-room-watch.service \
  pack-tunnel.service \
  pack-url-publisher.timer \
  pack-db-backup.timer

# restart starts inactive services too; it also applies User/Group/UMask changes
# to services that were already running under the previous unit definition.
systemctl restart pack-hub.service pack-room-watch.service pack-tunnel.service
systemctl restart pack-url-publisher.timer pack-db-backup.timer

echo "Pack systemd units installed and services restarted."
