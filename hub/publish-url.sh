#!/bin/bash
# 把当前 pinggy 隧道网址发布出去。pack-url-publisher.timer 每 2 分钟跑一次，
# 网址变了才推。双发过渡期（2026-11-19 起，hub-url 挪仓方案 A）：
#   1) Pack 公开仓 runtime/hub-url.txt —— 老通道，现行读取端在用；
#   2) pack-addr 私有仓 hub-url.txt —— 新通道，读取端带只读令牌拉取。
# 三个读取端（App / Mac 运行时 / 桥）全部切到新通道并验收后，停 1) 并删公开仓文件。
set -u
HUB_DIR=/home/hatch/workspace/pack/hub
MIRROR=/home/hatch/workspace/pack/repo-mirror
ADDR_MIRROR=/home/hatch/workspace/pack/addr-mirror
URL=$(journalctl -u pack-tunnel.service --no-pager -n 200 2>/dev/null | grep -oE "https://[a-z0-9.-]+run\.pinggy-free\.link" | tail -1)
[ -z "$URL" ] && exit 0
FULL="$URL/mcp"

publish_pack() {
  local state=$HUB_DIR/.published_url
  [ "$FULL" = "$(cat "$state" 2>/dev/null || true)" ] && return 0
  printf '%s' "$FULL" > /home/hatch/workspace/pack/runtime/hub-url.txt
  export GIT_SSH_COMMAND="ssh -i /home/hatch/.ssh/pack_deploy_ed25519 -o StrictHostKeyChecking=accept-new -o BatchMode=yes"
  if [ ! -d "$MIRROR/.git" ] || ! git -C "$MIRROR" fetch -q origin; then
    publish_pack_via_connector && { printf '%s' "$FULL" > "$state"; echo "published (pack, connector): $FULL"; return 0; }
    return 1
  fi
  git -C "$MIRROR" checkout -q main || return 1
  git -C "$MIRROR" reset -q --hard origin/main || return 1
  printf '%s' "$FULL" > "$MIRROR/runtime/hub-url.txt"
  git -C "$MIRROR" add runtime/hub-url.txt || return 1
  if git -C "$MIRROR" diff --cached --quiet; then
    printf '%s' "$FULL" > "$state"; return 0
  fi
  git -C "$MIRROR" commit -q -m "chore: hub url -> $FULL" || return 1
  if git -C "$MIRROR" push -q origin main; then
    printf '%s' "$FULL" > "$state"; echo "published (pack): $FULL"; return 0
  fi
  if publish_pack_via_connector; then
    printf '%s' "$FULL" > "$state"; echo "published (pack, connector): $FULL"; return 0
  fi
  return 1
}

publish_pack_via_connector() {
  python3 - "$FULL" <<'PY'
import json, sys
content = sys.argv[1]
args = {"owner": "cyou1314", "repo": "Pack", "branch": "main",
        "message": f"chore: hub url -> {content}",
        "files": [{"path": "runtime/hub-url.txt", "content": content}]}
open("/tmp/push-url.json", "w").write(json.dumps(args, ensure_ascii=False))
PY
  /opt/hatch/bin/github call-tool --name push_files --arguments-json "$(cat /tmp/push-url.json)" > /dev/null 2>&1
}

publish_addr() {
  local state=$HUB_DIR/.published_url_addr
  [ "$FULL" = "$(cat "$state" 2>/dev/null || true)" ] && return 0
  export GIT_SSH_COMMAND="ssh -i /home/hatch/.ssh/pack_addr_ed25519 -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o BatchMode=yes"
  [ -d "$ADDR_MIRROR/.git" ] || git clone -q git@github.com:cyou1314/pack-addr.git "$ADDR_MIRROR" || return 1
  git -C "$ADDR_MIRROR" fetch -q origin || return 1
  git -C "$ADDR_MIRROR" checkout -q main || return 1
  git -C "$ADDR_MIRROR" reset -q --hard origin/main || return 1
  printf '%s' "$FULL" > "$ADDR_MIRROR/hub-url.txt"
  git -C "$ADDR_MIRROR" add hub-url.txt || return 1
  if git -C "$ADDR_MIRROR" diff --cached --quiet; then
    printf '%s' "$FULL" > "$state"; return 0
  fi
  git -C "$ADDR_MIRROR" -c user.email='pack@local' -c user.name='pack publisher' commit -q -m "chore: hub url -> $FULL" || return 1
  git -C "$ADDR_MIRROR" push -q origin main || return 1
  printf '%s' "$FULL" > "$state"
  echo "published (addr): $FULL"
}

publish_pack
publish_addr
exit 0
