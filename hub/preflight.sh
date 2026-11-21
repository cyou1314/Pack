#!/usr/bin/env bash
# Pack 动工日预检（VM 半边）：Mac 开工派第一项之前跑一遍，全绿才动刀。
# 检查：静态资产（命令/文件/令牌权限）/ systemd 五件 / DB 完整性 /
# 中枢 MCP 应答与工具数 / 仓库同步与干净 / 备份新鲜度与可验证性。
# Mac 半边（App 基线构建、Codex 额度）不在此列，见 UI-IMPL-BRIEF。
set -u
FAIL=0
ok()   { printf '  ok  %s\n' "$1"; }
bad()  { printf 'FAIL  %s\n' "$1"; FAIL=1; }

HUB=/home/hatch/workspace/pack/hub

echo "[static]"
for cmd in python3 sqlite3; do
  command -v "$cmd" >/dev/null 2>&1 && ok "command: $cmd" || bad "missing command: $cmd"
done
id hatch >/dev/null 2>&1 && ok "service account hatch present" || bad "service account hatch missing"
getent group systemd-journal >/dev/null 2>&1 && ok "systemd-journal group present" || bad "systemd-journal group missing"
SSH_DIR=/home/hatch/.ssh
if [ -d "$SSH_DIR" ]; then
  SSH_OWNER=$(stat -c '%U' "$SSH_DIR")
  SSH_MODE=$(stat -c '%a' "$SSH_DIR")
  [ "$SSH_OWNER" = "hatch" ] && [ "$SSH_MODE" = "700" ] && ok "SSH directory owner/mode" || bad "SSH directory owner=$SSH_OWNER mode=$SSH_MODE (expected hatch/700)"
else
  bad "SSH directory missing"
fi
if [ -f "$SSH_DIR/pack_addr_ed25519" ]; then
  KEY_OWNER=$(stat -c '%U' "$SSH_DIR/pack_addr_ed25519")
  KEY_MODE=$(stat -c '%a' "$SSH_DIR/pack_addr_ed25519")
  [ "$KEY_OWNER" = "hatch" ] && [ "$KEY_MODE" = "600" ] && ok "URL publisher key owner/mode" || bad "URL publisher key owner=$KEY_OWNER mode=$KEY_MODE (expected hatch/600)"
else
  bad "URL publisher SSH key missing"
fi
[ -f "$HUB/hub.py" ] && ok "hub.py present" || bad "hub.py missing"
for module in core/context.py core/policy.py core/schema.py core/storage.py services/rooms.py; do
  [ -f "$HUB/$module" ] && ok "$module present" || bad "$module missing"
done
[ -f "$HUB/room_watch.py" ] && ok "room_watch.py present" || bad "room_watch.py missing"
[ -x "$HUB/.venv/bin/python" ] && ok "hub venv present" || bad "hub/.venv/bin/python missing"
[ -x "$HUB/backup-db.sh" ] && ok "backup-db.sh executable" || bad "backup-db.sh missing/not executable"
[ -x "$HUB/verify-backup.sh" ] && ok "verify-backup.sh executable" || bad "verify-backup.sh missing/not executable"
[ -d "$HUB/attachments" ] && ok "attachments dir present" || bad "attachments dir missing"
[ -d "$HUB/backups" ] && ok "backups dir present" || bad "backups dir missing"
if [ -f "$HUB/.dispatcher_token" ]; then
  PERMS=$(stat -c '%a' "$HUB/.dispatcher_token")
  [ "$PERMS" = "600" ] && ok "dispatcher token permissions 0600" || bad "dispatcher token permissions=$PERMS (expected 600)"
else
  bad "dispatcher token file missing"
fi

echo "[services]"
for u in pack-hub.service pack-tunnel.service pack-room-watch.service \
         pack-url-publisher.timer pack-db-backup.timer; do
  if systemctl is-active --quiet "$u"; then ok "$u active"; else bad "$u NOT active"; fi
done
for u in pack-hub.service pack-room-watch.service pack-tunnel.service \
         pack-url-publisher.service pack-db-backup.service; do
  SERVICE_USER=$(systemctl show -p User --value "$u" 2>/dev/null || true)
  SERVICE_GROUP=$(systemctl show -p Group --value "$u" 2>/dev/null || true)
  SERVICE_UMASK=$(systemctl show -p UMask --value "$u" 2>/dev/null || true)
  if [ "$SERVICE_USER" = "hatch" ] && [ "$SERVICE_GROUP" = "hatch" ] && [ "$SERVICE_UMASK" = "0077" ]; then
    ok "$u runs as hatch:hatch with UMask=0077"
  else
    bad "$u identity/umask mismatch (User=$SERVICE_USER Group=$SERVICE_GROUP UMask=$SERVICE_UMASK)"
  fi
done
PUBLISHER_GROUPS=$(systemctl show -p SupplementaryGroups --value pack-url-publisher.service 2>/dev/null || true)
case " $PUBLISHER_GROUPS " in
  *" systemd-journal "*) ok "URL publisher has journal read group" ;;
  *) bad "URL publisher missing systemd-journal supplementary group" ;;
esac

for path in "$HUB" "$HUB"/pack.db* "$HUB"/.dispatcher_token "$HUB"/.room_watch_state.json \
            "$HUB"/attachments "$HUB"/backups "$HUB"/backup.log "$HUB"/.published_url_addr \
            "$HUB"/../addr-mirror /home/hatch/.ssh; do
  [ -e "$path" ] || continue
  OWNER=$(stat -c '%U' "$path")
  [ "$OWNER" = "hatch" ] && ok "owner hatch: $path" || bad "owner=$OWNER (expected hatch): $path"
done

echo "[db]"
DB=/home/hatch/workspace/pack/hub/pack.db
IC=$(sqlite3 "$DB" "PRAGMA integrity_check;")
[ "$IC" = "ok" ] && ok "pack.db integrity ok" || bad "pack.db integrity: $IC"

echo "[hub]"
TOK=$(cat /home/hatch/workspace/pack/hub/.dispatcher_token)
SID=$(curl -s -m 5 -D - -o /dev/null -X POST http://127.0.0.1:8794/mcp \
  -H "Authorization: Bearer $TOK" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"preflight","version":"1"}}}' \
  | grep -i '^mcp-session-id:' | tr -d '\r' | awk '{print $2}')
if [ -n "$SID" ]; then
  TOOLS=$(curl -s -m 5 -X POST http://127.0.0.1:8794/mcp \
    -H "Authorization: Bearer $TOK" -H "mcp-session-id: $SID" \
    -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
    -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' | python3 -c '
import sys, json
n = 0
for line in sys.stdin:
    line = line.strip()
    if line.startswith("data:"):
        try:
            n = len(json.loads(line[5:])["result"]["tools"])
        except Exception:
            pass
print(n)')
  [ "$TOOLS" -gt 0 ] && ok "hub MCP responding, tools=$TOOLS" || bad "hub MCP tools/list empty"
else
  bad "hub MCP initialize failed"
fi

echo "[repo]"
cd /home/hatch/workspace/pack/repo-mirror || exit 1
export GIT_SSH_COMMAND="ssh -i ~/.ssh/pack_deploy_ed25519 -o BatchMode=yes"
git fetch origin -q 2>/dev/null
if [ -n "$(git status --porcelain)" ]; then bad "repo-mirror dirty"; else ok "repo-mirror clean"; fi
if [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ]; then ok "HEAD == origin/main"; else bad "HEAD != origin/main"; fi

echo "[backup]"
LATEST=$(ls -t /home/hatch/workspace/pack/hub/backups/*.db 2>/dev/null | head -1)
if [ -n "$LATEST" ]; then
  AGE=$(( ($(date +%s) - $(stat -c %Y "$LATEST")) / 3600 ))
  [ "$AGE" -lt 36 ] && ok "latest backup ${AGE}h old" || bad "latest backup ${AGE}h old (>36h)"
  if "$HUB/verify-backup.sh" >/dev/null 2>&1; then ok "latest backup verifies"; else bad "latest backup verification failed"; fi
else
  bad "no backups found"
fi

echo "----"
[ "$FAIL" = "0" ] && echo "PREFLIGHT ALL GREEN" || echo "PREFLIGHT HAS FAILURES"
exit "$FAIL"
