#!/bin/sh
# Make tailscaled reach the coordination server over HTTPS/443 only (TS_FORCE_NOISE_443=true).
#
# On wrt-nab (2026-09-29, Tailscale 1.102.4) the port-80 Noise long-poll to controlplane stalls
# (Send-Q stuck) and times out after exactly 2 min, every 2 min. The built-in fallback to 443
# (health.LastNoiseDialWasRecent, 2-minute window) never fires because each re-dial lands just
# past that window, so the node stays "offline" at control while peers can still reach it.
# tailscaled on generic Linux reads no env file (envknob.getPlatformEnvFiles), so the variable
# goes into the procd env of /etc/init.d/tailscale.
#
# Restarts tailscaled: run it over a path that does not depend on the tailnet (ZeroTier, LAN),
# or detached.
set -e
INIT=/etc/init.d/tailscale
if grep -q 'TS_FORCE_NOISE_443' "$INIT"; then
  echo "already set"
else
  cp -a "$INIT" "/root/tailscale.init.bak-$(date -u +%Y%m%dT%H%M%SZ)"
  sed -i '/procd_set_param env TS_DEBUG_FIREWALL_MODE/s/$/ TS_FORCE_NOISE_443=true/' "$INIT"
  grep -q 'TS_FORCE_NOISE_443=true' "$INIT" || { echo "patch failed"; exit 1; }
  echo "patched: $(grep -n 'procd_set_param env' "$INIT")"
fi
/etc/init.d/tailscale restart
sleep 15
echo "env: $(tr '\0' '\n' < /proc/$(pidof tailscaled)/environ | grep '^TS_' | tr '\n' ' ')"
echo "state: $(tailscale status --json 2>/dev/null | grep -m1 -o '"BackendState": *"[A-Za-z]*"' | cut -d'"' -f4)"
