#!/bin/sh
# Watch an OpenWrt router that is reachable only in rare windows and apply pending fixes on contact.
#
#   wrt-watch.sh /etc/wrt-watch/<name>.env [--dry-run]
#
# The env file sets:
#   NAME=wrt-mkch                 label for logs and notifications
#   TS_HOST=wrt-mkch              tailnet host name (MagicDNS short name), empty to skip the tailnet path
#   ZT_IP=172.23.234.116          ZeroTier address, reached through JUMP; empty to skip
#   JUMP=amalthea                 ssh Host alias (in root's ~/.ssh/config) that sits in the ZeroTier network
#   KEY=/root/.ssh/wrt-mkch       private key accepted by the router's root account
#   TARGET_TS=1.102.4             Tailscale version to reach; the update step is skipped once reached
#
# Every run is idempotent: steps are decided from the router's current state, local markers only
# stop repeated notifications. Scripts it pushes live next to this file.
# Telegram notifications: /etc/wrt-watch/telegram.env with BOT_TOKEN, CHAT_ID, optional TOPIC_ID.
set -u
CONF=${1:?usage: wrt-watch.sh <env> [--dry-run]}
DRY=${2:-}
. "$CONF"
HERE=$(cd "$(dirname "$0")" && pwd)
STATE=${STATE_DIR:-/var/lib/wrt-watch}/$NAME
LOG=${LOG_FILE:-/var/log/wrt-watch-$NAME.log}
mkdir -p "$STATE"

log() { echo "$(date -u +%FT%TZ) $*" >> "$LOG"; [ -n "$DRY" ] && echo "$*"; }
notify() {
  log "notify: $1"
  [ -n "$DRY" ] && return 0
  T=/etc/wrt-watch/telegram.env
  [ -f "$T" ] || return 0
  ( . "$T"
    curl -s -m 20 -o /dev/null "https://api.telegram.org/bot$BOT_TOKEN/sendMessage" \
      -d chat_id="$CHAT_ID" ${TOPIC_ID:+-d message_thread_id="$TOPIC_ID"} --data-urlencode text="$1" )
}
once() { [ -f "$STATE/$1" ] && return 1; touch "$STATE/$1"; return 0; }

SSH_OPTS="-o BatchMode=yes -o LogLevel=ERROR -o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=3 -o StrictHostKeyChecking=accept-new -i $KEY"
remote() { # shellcheck disable=SC2086
  ssh $SSH_OPTS $ROUTE "$@"
}

ts_online() {
  [ -n "${TS_HOST:-}" ] || return 1
  tailscale status --json 2>/dev/null | python3 -c '
import json, sys
host = sys.argv[1]
d = json.load(sys.stdin)
ok = any(p.get("Online") and p.get("DNSName", "").split(".")[0] == host for p in d.get("Peer", {}).values())
sys.exit(0 if ok else 1)' "$TS_HOST"
}

ROUTE=""
if ts_online; then
  ROUTE="root@$TS_HOST"; remote true 2>/dev/null || ROUTE=""
fi
if [ -z "$ROUTE" ] && [ -n "${ZT_IP:-}" ] && [ -n "${JUMP:-}" ]; then
  ROUTE="-J $JUMP root@$ZT_IP"; remote true 2>/dev/null || ROUTE=""
fi

if [ -z "$ROUTE" ]; then
  if [ -f "$STATE/up" ]; then rm -f "$STATE/up" "$STATE"/notified-*; log "down"; fi
  exit 0
fi
if once up; then notify "$NAME в сети ($ROUTE)"; fi

# Snapshot of the router state once per window, for later diagnosis.
if once diag; then
  remote 'sh -s' > "$STATE/diag-$(date -u +%Y%m%dT%H%M%SZ).txt" 2>&1 <<'EOF'
. /etc/openwrt_release; echo "$DISTRIB_DESCRIPTION uptime: $(uptime)"
echo "openclash enable=$(uci -q get openclash.config.enable) en_mode=$(uci -q get openclash.config.en_mode) operation_mode=$(uci -q get openclash.config.operation_mode)"
echo "tailscale $(tailscale version 2>/dev/null | head -1)"; tailscale status 2>&1 | head -5
nslookup controlplane.tailscale.com 127.0.0.1 2>&1 | tail -3
grep -vE '^#|^$' /etc/hosts; logread 2>/dev/null | grep tailscaled | tail -15
EOF
fi

OC_ENABLE=$(remote 'uci -q get openclash.config.enable' 2>/dev/null)
EN_MODE=$(remote 'uci -q get openclash.config.en_mode' 2>/dev/null)

# Step 1: OpenClash fake-ip filter (en_mode). Only when OpenClash runs and the subscription is not a stub.
if [ "$OC_ENABLE" = 1 ] && [ -z "$EN_MODE" ]; then
  PRE=$(remote 'sh -s' < "$HERE/subscription-precheck.sh" 2>&1)
  log "precheck: $(echo "$PRE" | tr '\n' ' ')"
  if echo "$PRE" | grep -q '^PRECHECK OK'; then
    if [ -n "$DRY" ]; then log "dry-run: would apply openclash-en-mode-fix.sh"
    else
      OUT=$(remote 'sh -s' < "$HERE/openclash-en-mode-fix.sh" 2>&1)
      log "en-mode-fix: $(echo "$OUT" | tr '\n' ' ')"
      notify "$NAME: применён фикс en_mode (fake-ip), OpenClash перезапущен"
    fi
  elif once notified-precheck; then
    notify "$NAME: подписка не прошла проверку, фикс en_mode НЕ применён: $(echo "$PRE" | tail -1)"
  fi
  exit 0   # let OpenClash settle; the Tailscale step runs on a later tick
fi

# Step 2: guarded Tailscale update, once tailscaled is Running.
TS_VER=$(remote '/usr/sbin/tailscaled --version 2>/dev/null | head -1' 2>/dev/null)
TS_STATE=$(remote "tailscale status --json 2>/dev/null | grep -m1 -o '\"BackendState\": *\"[A-Za-z]*\"' | cut -d'\"' -f4" 2>/dev/null)
if [ -n "${TARGET_TS:-}" ] && [ -n "$TS_VER" ] && [ "$TS_VER" != "$TARGET_TS" ]; then
  # The bracket keeps pgrep from matching the remote sh -c that carries this very pattern.
  if remote 'pgrep -f "[t]ailscale-update-guarded" >/dev/null' 2>/dev/null; then
    log "tailscale update in progress"
  elif [ "$TS_STATE" = Running ]; then
    if [ -n "$DRY" ]; then log "dry-run: would run tailscale-update-guarded.sh ($TS_VER -> $TARGET_TS)"
    else
      # Only nohup is backgrounded: a backgrounded cat would read /dev/null and write an empty script.
      remote 'cat > /root/tailscale-update-guarded.sh && { nohup sh /root/tailscale-update-guarded.sh </dev/null >/dev/null 2>&1 & }' \
        < "$HERE/tailscale-update-guarded.sh"
      log "tailscale update started ($TS_VER -> $TARGET_TS)"
    fi
  elif once notified-tsstate; then
    notify "$NAME: tailscaled в состоянии '$TS_STATE', обновление Tailscale отложено"
  fi
elif [ -n "$TS_VER" ] && once notified-tsdone; then
  notify "$NAME: Tailscale $TS_VER, всё применено. Итог: $(remote 'tail -1 /root/ts-update.log 2>/dev/null')"
fi
