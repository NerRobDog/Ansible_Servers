#!/bin/sh
# Make OpenClash 0.47 merge its custom fake-ip filter and keep Tailscale domains out of fake-ip.
#
# Without uci openclash.config.en_mode, yml_change.sh skips openclash_custom_fake_filter.list, so
# *.tailscale.com resolves to 198.18.x on the router itself and tailscaled cannot reach control
# (PDP c42bec3c). Routers provisioned from the openwrt_openclash template carry operation_mode
# instead, which OpenClash does not read.
#
# Restarts OpenClash once: clients behind the router lose connectivity for 10-30 s.
#   ssh <router> 'sh -s' < scripts/openwrt/openclash-en-mode-fix.sh
set -e
TS=$(date -u +%Y%m%dT%H%M%SZ); B=/root/backup-openclash-$TS; mkdir -p "$B"
L=/etc/openclash/custom/openclash_custom_fake_filter.list
cp -a /etc/config/openclash "$B/uci-openclash"
cp -a "$L" "$B/custom-fake-filter.list"
cp -a /etc/openclash/Watchdog.yaml "$B/running-Watchdog.yaml"
echo "backup=$B: $(ls "$B" | tr '\n' ' ')"

uci set openclash.config.en_mode='fake-ip'
uci commit openclash

for d in '+.tailscale.com' '+.tailscale.io'; do
  if ! grep -qxF "$d" "$L"; then
    [ -n "$(tail -c1 "$L")" ] && printf '\n' >> "$L"
    printf '%s\n' "$d" >> "$L"
  fi
done
# Real address for the subscription host so the nightly update can fetch past fake-ip.
cp -a /etc/hosts "$B/hosts"
if ! grep -qE '[[:space:]]no\.watchd0g\.dev$' /etc/hosts; then
  [ -n "$(tail -c1 /etc/hosts)" ] && printf '\n' >> /etc/hosts
  printf '89.23.98.20 no.watchd0g.dev\n' >> /etc/hosts
  /etc/init.d/dnsmasq reload >/dev/null 2>&1 || killall -HUP dnsmasq
fi
echo "en_mode=$(uci get openclash.config.en_mode) list_tailscale=$(grep -c tailscale "$L") hosts_sub=$(grep -c no.watchd0g.dev /etc/hosts)"

nohup /etc/init.d/openclash restart </dev/null >/tmp/oc-restart.out 2>&1 &
echo "openclash restart started at $(date -u +%T)Z; progress in /tmp/openclash.log"
