#!/bin/sh
# Fetch the router's OpenClash subscription past fake-ip and check it is a real config, not the
# X-HWID stub the panel serves to unsupported clients (two LAN-wide outages: 2026-08-13, 2026-08-31).
# Prints "PRECHECK OK ..." or "PRECHECK FAIL <reason>". Writes only /tmp/sub_precheck.yaml.
#   ssh <router> 'sh -s' < scripts/openwrt/subscription-precheck.sh
SUB_HOST=no.watchd0g.dev
SUB_IP=89.23.98.20
URL=$(uci -q get openclash.@config_subscribe[0].address)
[ -n "$URL" ] || URL=$(uci -q get openclash.config.config_update_path)
[ -n "$URL" ] || { echo "PRECHECK FAIL no subscription url in uci"; exit 1; }
F=/tmp/sub_precheck.yaml
R=$(curl -sS --max-time 40 --resolve "$SUB_HOST:443:$SUB_IP" -A clash.meta -o "$F" -w '%{http_code} %{size_download}' "$URL" 2>&1)
code=${R%% *}; size=${R##* }
[ "$code" = 200 ] || { echo "PRECHECK FAIL http=$R"; exit 1; }
[ "${size:-0}" -ge 5000 ] 2>/dev/null || { echo "PRECHECK FAIL size=$size"; exit 1; }
stub=$(grep -ciE 'не поддерживается|X-HWID|server: *0\.0\.0\.0' "$F")
[ "$stub" = 0 ] || { echo "PRECHECK FAIL stub markers=$stub"; exit 1; }
servers=$(sed -n '/^proxies:/,/^proxy-groups:/p' "$F" | grep -oE 'server: *[a-z0-9.-]+' | sort -u | wc -l)
[ "$servers" -ge 2 ] || { echo "PRECHECK FAIL servers=$servers"; exit 1; }
echo "PRECHECK OK http=$code size=$size servers=$servers"
