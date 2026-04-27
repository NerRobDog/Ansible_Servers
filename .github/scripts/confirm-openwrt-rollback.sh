#!/usr/bin/env bash
set -euo pipefail

inventory=""
limit="all"

usage() {
  cat <<'USAGE'
Usage: confirm-openwrt-rollback.sh --inventory <hosts.ini> [--limit <all|host1,host2>]

Confirms OpenWrt rollback guard for selected hosts.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --inventory)
      inventory="${2:-}"
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

if [[ -z "${inventory}" ]]; then
  echo "--inventory is required." >&2
  usage >&2
  exit 1
fi

if [[ ! -f "${inventory}" ]]; then
  echo "Inventory file not found: ${inventory}" >&2
  exit 1
fi

if ! command -v ansible >/dev/null 2>&1; then
  echo "Required command not found: ansible" >&2
  exit 1
fi

target_limit="${limit}"
if [[ -z "${target_limit}" ]]; then
  target_limit="all"
fi

ansible -i "${inventory}" "${target_limit}" -o \
  -m ansible.builtin.raw \
  -a "if [ -x /usr/local/sbin/openwrt-rollback-guard ]; then /usr/local/sbin/openwrt-rollback-guard confirm; else echo 'DISABLED no_guard_script'; fi" >/dev/null

ansible -i "${inventory}" "${target_limit}" -o \
  -m ansible.builtin.raw \
  -a "if [ -x /usr/local/sbin/openwrt-rollback-guard ]; then /usr/local/sbin/openwrt-rollback-guard status | grep -Eq '^(CONFIRMED|DISABLED) '; else echo 'DISABLED no_guard_script'; fi" >/dev/null

echo "Rollback guard confirmed for: ${target_limit}"
