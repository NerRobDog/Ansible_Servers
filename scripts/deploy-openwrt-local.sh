#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"

inventory=""
runtime_vars=""
bootstrap_map=""
limit="all"
mode="deploy"
profile="prod_update"
check_mode="false"
tags=""
vault_id=""

usage() {
  cat <<'USAGE'
Usage: deploy-openwrt-local.sh --inventory <hosts.ini> --runtime-vars <runtime_vars.json> --bootstrap-map <bootstrap_map.json> [options]

Options:
  --limit <all|host1,host2>   Limit hosts (default: all)
  --bootstrap-map <path>      Runtime bootstrap map from renderer
  --mode <bootstrap|deploy|lockdown>
                              Fleet mode (default: deploy)
  --profile <fresh|prod_update>
                              OpenWrt profile (default: prod_update)
  --check-mode <true|false>   Run ansible in check mode (default: false)
  --tags <tag1,tag2>          Optional ansible tags
  --vault-id <value>          Optional --vault-id argument for ansible-playbook
  -h, --help                  Show this help

For non-bootstrap and non-check runs this wrapper enforces:
  deploy -> smoke-openwrt -> confirm rollback guard

For bootstrap runs this wrapper enforces:
  access preflight -> ssh-copy-id (password) -> key login check -> ansible bootstrap
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
    --bootstrap-map)
      bootstrap_map="${2:-}"
      shift 2
      ;;
    --limit)
      limit="${2:-}"
      shift 2
      ;;
    --mode)
      mode="${2:-}"
      shift 2
      ;;
    --profile)
      profile="${2:-}"
      shift 2
      ;;
    --check-mode)
      check_mode="${2:-}"
      shift 2
      ;;
    --tags)
      tags="${2:-}"
      shift 2
      ;;
    --vault-id)
      vault_id="${2:-}"
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

if [[ -z "${inventory}" || -z "${runtime_vars}" || -z "${bootstrap_map}" ]]; then
  echo "--inventory, --runtime-vars and --bootstrap-map are required." >&2
  usage >&2
  exit 1
fi

if [[ ! -f "${inventory}" ]]; then
  echo "Inventory file not found: ${inventory}" >&2
  exit 1
fi

if [[ ! -f "${runtime_vars}" ]]; then
  echo "Runtime vars file not found: ${runtime_vars}" >&2
  exit 1
fi

if [[ ! -f "${bootstrap_map}" ]]; then
  echo "Bootstrap map file not found: ${bootstrap_map}" >&2
  exit 1
fi

if [[ "${mode}" != "bootstrap" && "${mode}" != "deploy" && "${mode}" != "lockdown" ]]; then
  echo "--mode must be bootstrap|deploy|lockdown" >&2
  exit 1
fi

if [[ "${profile}" != "fresh" && "${profile}" != "prod_update" ]]; then
  echo "--profile must be fresh|prod_update" >&2
  exit 1
fi

if [[ "${check_mode}" != "true" && "${check_mode}" != "false" ]]; then
  echo "--check-mode must be true|false" >&2
  exit 1
fi

mkdir -p "${repo_root}/.ansible/runtime"

if [[ "${mode}" != "bootstrap" && -n "${ZEROTIER_API_TOKEN:-}" ]]; then
  "${repo_root}/.github/scripts/zerotier-central-sync.py" \
    --runtime-vars "${runtime_vars}" \
    --limit "${limit}" \
    --authorize \
    --network-id "${ZEROTIER_NETWORK_ID:-}" \
    --report-out "${repo_root}/.ansible/runtime/openwrt_zerotier_report.local.json"
fi

"${repo_root}/.github/scripts/openwrt-access-preflight.py" \
  --inventory "${inventory}" \
  --bootstrap-map "${bootstrap_map}" \
  --output-inventory "${inventory}" \
  --report-out "${repo_root}/.ansible/runtime/openwrt_access_preflight.local.json" \
  --mode "${mode}" \
  --limit "${limit}"

if [[ "${mode}" == "bootstrap" ]]; then
  if ! command -v sshpass >/dev/null 2>&1; then
    echo "sshpass is required for bootstrap mode. Install it first." >&2
    exit 1
  fi

  if [[ "${limit}" == "all" || -z "${limit}" ]]; then
    targets="$(jq -r 'keys[]' "${bootstrap_map}")"
  else
    targets="$(echo "${limit}" | tr ',' '\n' | sed 's/^ *//;s/ *$//' | sed '/^$/d')"
  fi

  shared_key_file=""

  for alias in ${targets}; do
    if ! jq -e --arg alias "${alias}" '.[$alias]' "${bootstrap_map}" >/dev/null; then
      echo "Host alias '${alias}' not found in bootstrap map." >&2
      exit 1
    fi

    host="$(jq -r --arg alias "${alias}" '.[$alias].ansible_host' "${bootstrap_map}")"
    port="$(jq -r --arg alias "${alias}" '.[$alias].ansible_port' "${bootstrap_map}")"
    username="$(jq -r --arg alias "${alias}" '.[$alias].bootstrap_username' "${bootstrap_map}")"
    password="$(jq -r --arg alias "${alias}" '.[$alias].bootstrap_password // ""' "${bootstrap_map}")"
    proxy_jump="$(jq -r --arg alias "${alias}" '.[$alias].proxy_jump // ""' "${bootstrap_map}")"

    key_file="$(awk -v alias="${alias}" '
      $1 == alias {
        for (i = 2; i <= NF; i++) {
          if ($i ~ /^ansible_ssh_private_key_file=/) {
            sub(/^ansible_ssh_private_key_file=/, "", $i)
            gsub(/^'\''|'\''$/, "", $i)
            print $i
            exit
          }
        }
      }
    ' "${inventory}")"

    if [[ -z "${key_file}" ]]; then
      echo "Unable to resolve ansible_ssh_private_key_file for host '${alias}' from inventory." >&2
      exit 1
    fi

    key_file="${key_file/#\~/${HOME}}"
    if [[ ! -f "${key_file}" ]]; then
      echo "SSH private key for '${alias}' not found: ${key_file}" >&2
      exit 1
    fi

    if [[ ! -f "${key_file}.pub" ]]; then
      ssh-keygen -y -f "${key_file}" > "${key_file}.pub"
      chmod 644 "${key_file}.pub"
    fi

    if [[ -z "${shared_key_file}" ]]; then
      shared_key_file="${key_file}"
    elif [[ "${shared_key_file}" != "${key_file}" ]]; then
      echo "Bootstrap mode currently requires one shared SSH key across hosts in limit." >&2
      echo "Found '${shared_key_file}' and '${key_file}'." >&2
      exit 1
    fi

    if [[ -z "${password}" ]]; then
      echo "Bootstrap password is missing for host '${alias}'." >&2
      exit 1
    fi

    ssh_opts=(-o StrictHostKeyChecking=yes -p "${port}")
    if [[ -n "${proxy_jump}" ]]; then
      ssh_opts+=(-o "ProxyJump=${proxy_jump}")
    fi

    SSHPASS="${password}" sshpass -e ssh-copy-id \
      -f \
      -i "${key_file}.pub" \
      "${ssh_opts[@]}" \
      "${username}@${host}"

    ssh -i "${key_file}" \
      -o BatchMode=yes \
      -o StrictHostKeyChecking=yes \
      "${ssh_opts[@]}" \
      "${username}@${host}" "echo bootstrap-key-ok"
  done

  if [[ -n "${shared_key_file}" ]]; then
    export CI_DEPLOY_PUBLIC_KEY
    CI_DEPLOY_PUBLIC_KEY="$(tr -d '\n' < "${shared_key_file}.pub")"
  fi
fi

cmd=(
  ansible-playbook
  -i "${inventory}"
  "${repo_root}/playbook.openwrt.yml"
  --limit "${limit}"
  --extra-vars "@${runtime_vars}"
  --extra-vars "fleet_mode=${mode}"
  --extra-vars "openwrt_profile=${profile}"
)

if [[ -n "${tags}" ]]; then
  cmd+=(--tags "${tags}")
fi

if [[ -n "${vault_id}" ]]; then
  cmd+=(--vault-id "${vault_id}")
fi

if [[ "${check_mode}" == "true" ]]; then
  cmd+=(--check --diff)
fi

"${cmd[@]}"

if [[ "${mode}" != "bootstrap" && "${check_mode}" == "false" ]]; then
  "${repo_root}/.github/scripts/smoke-openwrt.sh" \
    --inventory "${inventory}" \
    --runtime-vars "${runtime_vars}" \
    --limit "${limit}"

  "${repo_root}/.github/scripts/confirm-openwrt-rollback.sh" \
    --inventory "${inventory}" \
    --limit "${limit}"
fi
