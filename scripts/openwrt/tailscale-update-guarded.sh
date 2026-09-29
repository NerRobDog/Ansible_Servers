#!/bin/sh
# Guarded Tailscale update for OpenWrt routers (opkg package 1.80.3, tailscale -> tailscaled symlink).
#
# Run detached on the router: restarting tailscaled drops any SSH session that goes over the tailnet.
#   ssh <router> 'cat > /root/tailscale-update-guarded.sh && { nohup sh /root/tailscale-update-guarded.sh </dev/null >/dev/null 2>&1 & }' < scripts/openwrt/tailscale-update-guarded.sh
# Keep the braces: backgrounding the whole "cat && nohup" list makes cat read /dev/null.
#
# Log: /root/ts-update.log. Keeps the old binaries in /root/ts-rollback-<old version> and restores them
# if tailscaled is not Running within 120 s. On success pins the opkg package so opkg cannot downgrade it.
# Requires pkgs.tailscale.com to resolve to a real address (OpenClash en_mode, see openclash-en-mode-fix.sh).
LOG=/root/ts-update.log
log() { echo "$(date -u +%FT%TZ) $*" >> "$LOG"; }
state() { tailscale status --json 2>/dev/null | grep -m1 -o '"BackendState": *"[A-Za-z]*"' | cut -d'"' -f4; }

OLD=$(/usr/sbin/tailscaled --version 2>/dev/null | head -1)
UP=$(tailscale version --upstream 2>/dev/null | awk '/upstream:/{print $2}')
if [ -n "$UP" ] && [ "$UP" = "$OLD" ]; then log "already at upstream $OLD, nothing to do"; exit 0; fi
B=/root/ts-rollback-$OLD
mkdir -p "$B"
cp -a /usr/sbin/tailscaled "$B/tailscaled"
if [ -L /usr/sbin/tailscale ]; then echo symlink > "$B/tailscale.kind"; else cp -a /usr/sbin/tailscale "$B/tailscale"; fi
cp -a /etc/init.d/tailscale "$B/tailscale.init"
log "start old=$OLD state=$(state) backup=$B"

[ "$(state)" = Running ] || { log "ABORT: tailscaled is not Running before update"; exit 1; }

tailscale update --yes >> "$LOG" 2>&1
NEW=$(/usr/sbin/tailscaled --version 2>/dev/null | head -1)
log "binary after update: $NEW"
[ -n "$NEW" ] && [ "$NEW" != "$OLD" ] || { log "ABORT: binary unchanged, nothing to restart"; exit 1; }

# 1.102.x keeps re-dialling control on port 80 when a middlebox stalls that connection
# (wrt-nab 2026-09-29, see tailscale-force-443.sh); force HTTPS/443 for control.
grep -q 'TS_FORCE_NOISE_443' /etc/init.d/tailscale ||
  sed -i '/procd_set_param env TS_DEBUG_FIREWALL_MODE/s/$/ TS_FORCE_NOISE_443=true/' /etc/init.d/tailscale
log "init env: $(grep 'procd_set_param env' /etc/init.d/tailscale | sed 's/^ *//')"

/etc/init.d/tailscale restart
i=0; st=""
while [ $i -lt 24 ]; do
  sleep 5; i=$((i + 1)); st=$(state)
  [ "$st" = Running ] && break
done

# "Running" is reported before the control long-poll proves itself; a stalled poll only shows up
# as a health warning once it times out (2 min). Wait past that and require a clean map.
if [ "$st" = Running ]; then
  sleep 150
  if tailscale status --json 2>/dev/null | grep -q "received a network map"; then
    log "control long-poll failing after restart (mapresponse-timeout)"
    st="Running-no-netmap"
  fi
fi

if [ "$st" = Running ]; then
  opkg flag hold tailscale >> "$LOG" 2>&1
  log "OK Running $(tailscale version | head -1); opkg hold set; rollback copy kept in $B"
else
  log "FAIL state=$st after 120 s -> rollback"
  cp -a "$B/tailscaled" /usr/sbin/tailscaled
  cp -a "$B/tailscale.init" /etc/init.d/tailscale
  rm -f /usr/sbin/tailscale
  if [ -f "$B/tailscale.kind" ]; then ln -s tailscaled /usr/sbin/tailscale; else cp -a "$B/tailscale" /usr/sbin/tailscale; fi
  /etc/init.d/tailscale restart
  sleep 20
  log "rolled back: $(/usr/sbin/tailscaled --version | head -1) state=$(state)"
fi
