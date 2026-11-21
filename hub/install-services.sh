#!/usr/bin/env bash
# Install Pack systemd units, migrate their writable files to the service user,
# and restart long-running services so identity changes take effect.
set -Eeuo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "ERROR: run this installer as root (for example: sudo $0)" >&2
  exit 1
fi
id hatch >/dev/null 2>&1 || { echo "ERROR: required service account 'hatch' does not exist" >&2; exit 1; }
getent group systemd-journal >/dev/null 2>&1 || { echo "ERROR: required group 'systemd-journal' does not exist for URL publisher" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="$SCRIPT_DIR/systemd"
UNIT_DEST="/etc/systemd/system"
ADDR_MIRROR="$(dirname "$SCRIPT_DIR")/addr-mirror"
SSH_DIR="/home/hatch/.ssh"
UNITS=(
  pack-hub.service
  pack-room-watch.service
  pack-tunnel.service
  pack-url-publisher.service
  pack-url-publisher.timer
  pack-db-backup.service
  pack-db-backup.timer
)

# Validate every prerequisite before changing ownership or installing units.
[[ -d "$SSH_DIR" ]] || { echo "ERROR: SSH directory missing: $SSH_DIR" >&2; exit 1; }
[[ -f "$SSH_DIR/pack_addr_ed25519" ]] || { echo "ERROR: URL publisher SSH key missing" >&2; exit 1; }
for unit in "${UNITS[@]}"; do
  [[ -f "$UNIT_DIR/$unit" ]] || { echo "ERROR: missing unit: $UNIT_DIR/$unit" >&2; exit 1; }
done

# Earlier system units ran as root. Transfer only Pack-owned runtime/code
# directories and SSH metadata to the dedicated account before switching users.
chown -R hatch:hatch "$SCRIPT_DIR"
if [[ -e "$ADDR_MIRROR" ]]; then chown -R hatch:hatch "$ADDR_MIRROR"; fi
chown hatch:hatch "$SSH_DIR"
chmod 0700 "$SSH_DIR"
for private_key in "$SSH_DIR/id_ed25519" "$SSH_DIR/id_rsa" "$SSH_DIR/id_ecdsa" \
                   "$SSH_DIR/pack_addr_ed25519" "$SSH_DIR/pack_deploy_ed25519"; do
  if [[ -f "$private_key" ]]; then
    chown hatch:hatch "$private_key"
    chmod 0600 "$private_key"
  fi
done
for known_hosts in "$SSH_DIR/pack_tunnel_known_hosts" "$SSH_DIR/pack_addr_known_hosts"; do
  if [[ -e "$known_hosts" ]]; then
    chown hatch:hatch "$known_hosts"
    chmod 0600 "$known_hosts"
  fi
done

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
