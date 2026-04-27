#!/usr/bin/env bash
set -euo pipefail

inventory=""
runtime_vars=""
limit="all"

usage() {
  cat <<'USAGE'
Usage: smoke-openwrt.sh --inventory <hosts.ini> --runtime-vars <runtime_vars.json> [--limit <all|host1,host2>]

Runs post-deploy smoke checks for OpenWrt fleet hosts (fail-fast).
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --inventory)
      inventory="${2:-}"
      shift 2
      ;;
    --runtime-vars)
      runtime_vars="${2:-}"
      shift 2
      ;;
    --limit)
      limit="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "$inventory" || -z "$runtime_vars" ]]; then
  echo "Both --inventory and --runtime-vars are required." >&2
  usage >&2
  exit 1
fi

if [[ ! -f "$inventory" ]]; then
  echo "Inventory file not found: $inventory" >&2
  exit 1
fi

if [[ ! -f "$runtime_vars" ]]; then
  echo "Runtime vars file not found: $runtime_vars" >&2
  exit 1
fi

for cmd in ansible jq; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Required command not found: $cmd" >&2
    exit 1
  fi
done

targets=()
if [[ "$limit" == "all" || -z "$limit" ]]; then
  while IFS= read -r alias; do
    [[ -n "$alias" ]] && targets+=("$alias")
  done < <(jq -r '.fleet_hosts | keys[]' "$runtime_vars")
else
  while IFS= read -r alias; do
    [[ -n "$alias" ]] && targets+=("$alias")
  done < <(echo "$limit" | tr ',' '\n' | sed 's/^ *//;s/ *$//' | sed '/^$/d')
fi

if [[ ${#targets[@]} -eq 0 ]]; then
  echo "No target hosts resolved for smoke checks." >&2
  exit 1
fi

run_ansible() {
  local alias="$1"
  shift
  ansible -i "$inventory" "$alias" -o "$@"
}

passwall_ban_ru_domains=(
  "youtube.com"
  "discord.com"
  "instagram.com"
  "rutracker.org"
)

passwall_proxy_domains=(
  "soundcloud.com"
  "chat.openai.com"
  "x.ai"
  "netflix.com"
  "copilot.microsoft.com"
  "tiktok.com"
  "telegram.org"
  "t.me"
  "web.telegram.org"
)

passwall_direct_domains=(
  "gosuslugi.ru"
  "nalog.gov.ru"
  "microsoft.com"
  "apple.com"
  "twitch.tv"
  "store.steampowered.com"
  "playstation.com"
)

expected_geosite_tags() {
  local domain="$1"
  case "$domain" in
    youtube.com) echo "YOUTUBE" ;;
    discord.com) echo "DISCORD" ;;
    instagram.com) echo "INSTAGRAM|META" ;;
    rutracker.org) echo "RUTRACKER" ;;
    web.telegram.org) echo "TELEGRAM" ;;
    telegram.org) echo "TELEGRAM" ;;
    t.me) echo "TELEGRAM" ;;
    soundcloud.com) echo "SOUNDCLOUD" ;;
    chat.openai.com) echo "OPENAI|CATEGORY-AI-CHAT" ;;
    x.ai) echo "XAI|CATEGORY-AI" ;;
    netflix.com) echo "NETFLIX" ;;
    copilot.microsoft.com) echo "MICROSOFT|BING|CATEGORY-AI" ;;
    tiktok.com) echo "TIKTOK|BYTEDANCE" ;;
    gosuslugi.ru) echo "CATEGORY-RU|CATEGORY-GOV-RU" ;;
    nalog.gov.ru) echo "CATEGORY-RU|CATEGORY-GOV-RU" ;;
    microsoft.com) echo "MICROSOFT" ;;
    apple.com) echo "APPLE" ;;
    twitch.tv) echo "TWITCH" ;;
    store.steampowered.com) echo "STEAM|CATEGORY-GAMES" ;;
    playstation.com) echo "PLAYSTATION|SONY|CATEGORY-GAMES" ;;
    *) echo "" ;;
  esac
}

for alias in "${targets[@]}"; do
  if ! jq -e --arg alias "$alias" '.fleet_hosts[$alias]' "$runtime_vars" >/dev/null; then
    echo "Host alias '$alias' not found in runtime vars." >&2
    exit 1
  fi

  echo "[smoke-openwrt][$alias] Verify SSH connectivity"
  run_ansible "$alias" -m ansible.builtin.raw -a "echo openwrt-smoke-ok" >/dev/null

  echo "[smoke-openwrt][$alias] Check rollback guard state"
  run_ansible "$alias" -m ansible.builtin.raw -a "if [ -x /usr/local/sbin/openwrt-rollback-guard ]; then /usr/local/sbin/openwrt-rollback-guard status | grep -Eq '^(ARMED|CONFIRMED) '; else echo 'DISABLED no_guard_script'; fi" >/dev/null

  feature_zerotier="$(jq -r --arg alias "$alias" '.fleet_hosts[$alias].features.feature_openwrt_zerotier // false' "$runtime_vars")"
  feature_tailscale="$(jq -r --arg alias "$alias" '.fleet_hosts[$alias].features.feature_tailscale // false' "$runtime_vars")"
  feature_passwall2="$(jq -r --arg alias "$alias" '.fleet_hosts[$alias].features.feature_openwrt_passwall2 // false' "$runtime_vars")"
  feature_docker_runtime="$(jq -r --arg alias "$alias" '.fleet_hosts[$alias].features.feature_openwrt_docker_runtime // false' "$runtime_vars")"
  feature_docker_stacks="$(jq -r --arg alias "$alias" '.fleet_hosts[$alias].features.feature_openwrt_docker_stacks // false' "$runtime_vars")"
  feature_monitoring="$(jq -r --arg alias "$alias" '.fleet_hosts[$alias].features.feature_openwrt_monitoring_agent // false' "$runtime_vars")"
  feature_lockdown="$(jq -r --arg alias "$alias" '.fleet_hosts[$alias].features.feature_openwrt_ssh_lockdown // false' "$runtime_vars")"

  if [[ "$feature_zerotier" == "true" ]]; then
    echo "[smoke-openwrt][$alias] Check ZeroTier service"
    run_ansible "$alias" -m ansible.builtin.raw -a "/etc/init.d/zerotier enabled >/dev/null 2>&1 && /etc/init.d/zerotier status | grep -q running" >/dev/null
  fi

  if [[ "$feature_tailscale" == "true" ]]; then
    echo "[smoke-openwrt][$alias] Check tailscale backend state"
    run_ansible "$alias" -m ansible.builtin.raw -a "tailscale status --json 2>/dev/null | grep -Eq '\"BackendState\"[[:space:]]*:[[:space:]]*\"(Running|Starting)\"'" >/dev/null
  fi

  if [[ "$feature_passwall2" == "true" ]]; then
    subscribe_url="$(jq -r --arg alias "$alias" '.fleet_hosts[$alias].passwall2.subscribe_url // empty' "$runtime_vars")"
    socks_port="$(jq -r --arg alias "$alias" '.fleet_hosts[$alias].passwall2.socks_port // 1070' "$runtime_vars")"
    probe_url="$(jq -r --arg alias "$alias" '.fleet_hosts[$alias].passwall2.probe_url // "https://www.gstatic.com/generate_204"' "$runtime_vars")"

    echo "[smoke-openwrt][$alias] Check Passwall2 config"
    run_ansible "$alias" -m ansible.builtin.raw -a "grep -F \"${subscribe_url}\" /etc/config/passwall2 >/dev/null && /etc/init.d/passwall2 enabled >/dev/null 2>&1" >/dev/null

    passwall_enabled_raw="$(run_ansible "$alias" -m ansible.builtin.raw -a "echo PW_ENABLED=\$(uci -q get 'passwall2.@global[0].enabled' || echo 0)")"
    passwall_enabled="$(echo "${passwall_enabled_raw}" | tr -d '\r' | grep -Eo 'PW_ENABLED=[0-9]+' | tail -n1 | cut -d= -f2 || true)"

    node_count_raw="$(run_ansible "$alias" -m ansible.builtin.raw -a "count=0; for id in \$(uci -q show passwall2 | sed -n 's/^passwall2\\.\\([^.]*\\)=nodes$/\\1/p'); do p=\$(uci -q get passwall2.\$id.protocol || true); case \"\$p\" in \"\"|_*) ;; *) count=\$((count+1));; esac; done; echo PW_NODE_COUNT=\$count")"
    node_count="$(echo "${node_count_raw}" | tr -d '\r' | grep -Eo 'PW_NODE_COUNT=[0-9]+' | tail -n1 | cut -d= -f2 || true)"

    if [[ -z "${node_count}" ]]; then
      node_count="0"
    fi

    if [[ "${passwall_enabled}" == "1" ]]; then
      echo "[smoke-openwrt][$alias] Passwall2 enabled, run SOCKS probe"
      run_ansible "$alias" -m ansible.builtin.raw -a "pgrep -af '/tmp/etc/passwall2/bin/xray|/tmp/etc/passwall2/bin/sing-box' >/dev/null" >/dev/null
      run_ansible "$alias" -m ansible.builtin.raw -a "curl --silent --show-error --fail --max-time 12 --socks5-hostname 127.0.0.1:${socks_port} --output /dev/null '${probe_url}'" >/dev/null

      echo "[smoke-openwrt][$alias] Check geosite lookup tooling"
      run_ansible "$alias" -m ansible.builtin.raw -a "command -v geoview >/dev/null 2>&1 && [ -f /usr/share/v2ray/geosite.dat ]" >/dev/null

      echo "[smoke-openwrt][$alias] Check direct/proxy egress differs"
      run_ansible "$alias" -m ansible.builtin.raw -a "direct_ip=\$(curl --silent --show-error --max-time 12 'https://api.ipify.org' || true); proxy_ip=\$(curl --silent --show-error --max-time 12 --socks5-hostname 127.0.0.1:${socks_port} 'https://api.ipify.org' || true); [ -n \"\$direct_ip\" ] && [ -n \"\$proxy_ip\" ] && [ \"\$direct_ip\" != \"\$proxy_ip\" ]" >/dev/null

      for domain in "${passwall_ban_ru_domains[@]}"; do
        expected_tags="$(expected_geosite_tags "$domain")"
        echo "[smoke-openwrt][$alias] ban_ru via SOCKS: ${domain}"
        run_ansible "$alias" -m ansible.builtin.raw -a "curl --silent --show-error --max-time 20 --socks5-hostname 127.0.0.1:${socks_port} --output /dev/null 'https://${domain}'" >/dev/null
        if [[ -n "$expected_tags" ]]; then
          run_ansible "$alias" -m ansible.builtin.raw -a "geoview -action lookup -type geosite -input /usr/share/v2ray/geosite.dat -value '${domain}' | grep -Eiq '${expected_tags}'" >/dev/null
        fi
      done

      for domain in "${passwall_proxy_domains[@]}"; do
        expected_tags="$(expected_geosite_tags "$domain")"
        echo "[smoke-openwrt][$alias] proxy via SOCKS: ${domain}"
        run_ansible "$alias" -m ansible.builtin.raw -a "curl --silent --show-error --max-time 20 --socks5-hostname 127.0.0.1:${socks_port} --output /dev/null 'https://${domain}'" >/dev/null
        if [[ -n "$expected_tags" ]]; then
          run_ansible "$alias" -m ansible.builtin.raw -a "geoview -action lookup -type geosite -input /usr/share/v2ray/geosite.dat -value '${domain}' | grep -Eiq '${expected_tags}'" >/dev/null
        fi
      done

      for domain in "${passwall_direct_domains[@]}"; do
        expected_tags="$(expected_geosite_tags "$domain")"
        echo "[smoke-openwrt][$alias] direct path: ${domain}"
        run_ansible "$alias" -m ansible.builtin.raw -a "curl --silent --show-error --max-time 20 --output /dev/null 'https://${domain}'" >/dev/null
        if [[ -n "$expected_tags" ]]; then
          run_ansible "$alias" -m ansible.builtin.raw -a "geoview -action lookup -type geosite -input /usr/share/v2ray/geosite.dat -value '${domain}' | grep -Eiq '${expected_tags}'" >/dev/null
        fi
      done
    else
      echo "[smoke-openwrt][$alias] Passwall2 is disabled (safe mode), nodes_detected=${node_count}"
    fi
  fi

  if [[ "$feature_docker_runtime" == "true" || "$feature_docker_stacks" == "true" ]]; then
    echo "[smoke-openwrt][$alias] Check dockerd service"
    run_ansible "$alias" -m ansible.builtin.raw -a "/etc/init.d/dockerd enabled >/dev/null 2>&1 && /etc/init.d/dockerd status | grep -q running" >/dev/null
  fi

  if [[ "$feature_monitoring" == "true" ]]; then
    exporter_port="$(jq -r --arg alias "$alias" '.fleet_hosts[$alias].monitoring.openwrt_node_exporter_port // 9100' "$runtime_vars")"

    echo "[smoke-openwrt][$alias] Check OpenWrt monitoring endpoint and probe metrics"
    run_ansible "$alias" -m ansible.builtin.raw -a "curl --silent --show-error --fail 'http://127.0.0.1:${exporter_port}/metrics' | grep -q 'openwrt_probe_direct_up'" >/dev/null
    run_ansible "$alias" -m ansible.builtin.raw -a "curl --silent --show-error --fail 'http://127.0.0.1:${exporter_port}/metrics' | grep -q 'openwrt_probe_passwall2_up'" >/dev/null
  fi

  if [[ "$feature_lockdown" == "true" ]]; then
    echo "[smoke-openwrt][$alias] Check Dropbear lockdown settings"
    run_ansible "$alias" -m ansible.builtin.raw -a "uci show dropbear | grep -q \"PasswordAuth='off'\" && uci show dropbear | grep -q \"RootPasswordAuth='off'\"" >/dev/null
  fi

done

echo "OpenWrt smoke checks passed for: ${targets[*]}"
