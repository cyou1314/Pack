#!/bin/bash
# 把当前 pinggy 隧道网址发布到私有仓 pack-addr 的 hub-url.txt。
# pack-url-publisher.timer 每 2 分钟跑一次，网址变了才推。
# 2026-11-19 hub-url 挪仓（方案 A）完成：公开仓 Pack 的 runtime/hub-url.txt
# 已停发并删除；三个读取端（App / Mac 运行时 / 桥）均以只读令牌拉私有仓，
# 令牌缺失时回退逻辑仍指向旧公开文件（已删，常态下不会走到）。
set -u
HUB_DIR=/home/hatch/workspace/pack/hub
ADDR_MIRROR=/home/hatch/workspace/pack/addr-mirror
URL=$(journalctl -u pack-tunnel.service --no-pager -n 200 2>/dev/null | grep -oE "https://[a-z0-9.-]+run\.pinggy-free\.link" | tail -1)
[ -z "$URL" ] && exit 0
FULL="$URL/mcp"
STATE=$HUB_DIR/.published_url_addr
[ "$FULL" = "$(cat "$STATE" 2>/dev/null || true)" ] && exit 0
export GIT_SSH_COMMAND="ssh -i /home/hatch/.ssh/pack_addr_ed25519 -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/home/hatch/.ssh/pack_addr_known_hosts -o BatchMode=yes"
[ -d "$ADDR_MIRROR/.git" ] || git clone -q git@github.com:cyou1314/pack-addr.git "$ADDR_MIRROR" || exit 1
git -C "$ADDR_MIRROR" fetch -q origin || exit 1
git -C "$ADDR_MIRROR" checkout -q main || exit 1
git -C "$ADDR_MIRROR" reset -q --hard origin/main || exit 1
printf '%s' "$FULL" > "$ADDR_MIRROR/hub-url.txt"
git -C "$ADDR_MIRROR" add hub-url.txt || exit 1
if git -C "$ADDR_MIRROR" diff --cached --quiet; then
  printf '%s' "$FULL" > "$STATE"; exit 0
fi
git -C "$ADDR_MIRROR" -c user.email='pack@local' -c user.name='pack publisher' commit -q -m "chore: hub url -> $FULL" || exit 1
git -C "$ADDR_MIRROR" push -q origin main || exit 1
printf '%s' "$FULL" > "$STATE"
echo "published (addr): $FULL"
