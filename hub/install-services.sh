#!/bin/bash
# 把持久目录里的 unit 正本装到 /etc 并启用（VM 替换后重跑即可恢复）
set -e
cp /home/hatch/workspace/pack/hub/systemd/pack-hub.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now pack-hub.service
cp /home/hatch/workspace/pack/hub/systemd/pack-tunnel.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now pack-tunnel.service
cp /home/hatch/workspace/pack/hub/systemd/pack-url-publisher.service /etc/systemd/system/
cp /home/hatch/workspace/pack/hub/systemd/pack-url-publisher.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now pack-url-publisher.timer
cp /home/hatch/workspace/pack/hub/systemd/pack-db-backup.service /etc/systemd/system/
cp /home/hatch/workspace/pack/hub/systemd/pack-db-backup.timer /etc/systemd/system/
systemctl enable --now pack-db-backup.timer
cp /home/hatch/workspace/pack/hub/systemd/pack-room-watch.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now pack-room-watch.service
