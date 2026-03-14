#!/usr/bin/env bash
set -euo pipefail

inventory=""
runtime_vars=""
limit="all"

usage() {
  cat <<'EOF'
Usage: smoke-remnawave.sh --inventory <hosts.ini> --runtime-vars <runtime_vars.json> [--limit <all|host1,host2>]

Runs post-deploy smoke checks for fleet hosts (fail-fast).
EOF
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

run_ansible_retry() {
  local alias="$1"
  local attempts="$2"
  local delay_seconds="$3"
  shift 3

  local attempt=1
  local last_output=""
  while true; do
    if last_output="$(run_ansible "$alias" "$@" 2>&1)"; then
      return 0
    fi

    if [[ "$attempt" -ge "$attempts" ]]; then
      echo "[smoke][$alias] Check failed after ${attempts} attempts: $*" >&2
      if [[ -n "$last_output" ]]; then
        echo "$last_output" >&2
      fi
      return 1
    fi

    attempt=$((attempt + 1))
    sleep "$delay_seconds"
  done
}

for alias in "${targets[@]}"; do
  if ! jq -e --arg alias "$alias" '.fleet_hosts[$alias]' "$runtime_vars" >/dev/null; then
    echo "Host alias '$alias' not found in runtime vars." >&2
    exit 1
  fi

  echo "[smoke][$alias] Verify SSH connectivity"
  run_ansible "$alias" -m ansible.builtin.ping >/dev/null

  feature_docker="$(jq -r --arg alias "$alias" '.fleet_hosts[$alias].features.feature_docker // false' "$runtime_vars")"
  feature_remnawave_node="$(jq -r --arg alias "$alias" '.fleet_hosts[$alias].features.feature_remnawave_node // false' "$runtime_vars")"
  feature_caddy_node="$(jq -r --arg alias "$alias" '.fleet_hosts[$alias].features.feature_caddy_node // false' "$runtime_vars")"
  feature_node_tuning="$(jq -r --arg alias "$alias" '.fleet_hosts[$alias].features.feature_node_tuning // false' "$runtime_vars")"
  feature_monitoring_agent="$(jq -r --arg alias "$alias" '.fleet_hosts[$alias].features.feature_monitoring_agent // false' "$runtime_vars")"
  feature_monitoring_stack="$(jq -r --arg alias "$alias" '.fleet_hosts[$alias].features.feature_monitoring_stack // false' "$runtime_vars")"

  if [[ "$feature_docker" == "true" ]]; then
    echo "[smoke][$alias] Check Docker service"
    run_ansible "$alias" -b -m ansible.builtin.command -a "systemctl is-active docker" >/dev/null
  fi

  if [[ "$feature_remnawave_node" == "true" ]]; then
    echo "[smoke][$alias] Check remnanode container runtime"
    run_ansible "$alias" -b -m ansible.builtin.shell -a "docker ps --filter name=^/remnanode$ | grep -q remnanode" >/dev/null
    run_ansible "$alias" -b -m ansible.builtin.shell -a "docker inspect remnanode | grep -q '\"NetworkMode\": \"host\"'" >/dev/null
    run_ansible "$alias" -b -m ansible.builtin.shell -a "docker inspect remnanode | grep -q NET_ADMIN" >/dev/null
  fi

  if [[ "$feature_caddy_node" == "true" ]]; then
    caddy_domain="$(jq -r --arg alias "$alias" '.remnawave_runtime_host_vars[$alias].remnawave_caddy_domain // empty' "$runtime_vars")"
    caddy_monitor_port="$(jq -r --arg alias "$alias" '.remnawave_runtime_host_vars[$alias].remnawave_caddy_monitor_port // empty' "$runtime_vars")"

    if [[ -z "$caddy_domain" || -z "$caddy_monitor_port" ]]; then
      echo "Missing caddy_domain/caddy_monitor_port for host '$alias'." >&2
      exit 1
    fi

    echo "[smoke][$alias] Check Caddy service and config"
    run_ansible "$alias" -b -m ansible.builtin.command -a "systemctl is-active caddy" >/dev/null
    run_ansible "$alias" -b -m ansible.builtin.command -a "caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile" >/dev/null

    echo "[smoke][$alias] Check Caddy local health endpoint"
    run_ansible "$alias" -b -m ansible.builtin.shell -a "curl --silent --show-error --fail --insecure --resolve '${caddy_domain}:${caddy_monitor_port}:127.0.0.1' 'https://${caddy_domain}:${caddy_monitor_port}/healthz' | grep -qx ok" >/dev/null
  fi

  if [[ "$feature_node_tuning" == "true" ]]; then
    ipv6_state="$(jq -r --arg alias "$alias" '.remnawave_runtime_host_vars[$alias].remnawave_ipv6_state // "enabled"' "$runtime_vars")"
    if [[ "$ipv6_state" == "disabled" ]]; then
      expected_ipv6="1"
    else
      expected_ipv6="0"
    fi

    echo "[smoke][$alias] Check node tuning sysctl"
    run_ansible "$alias" -b -m ansible.builtin.shell -a "test \"\$(sysctl -n net.core.default_qdisc)\" = fq && test \"\$(sysctl -n net.ipv4.tcp_congestion_control)\" = bbr" >/dev/null
    run_ansible "$alias" -b -m ansible.builtin.shell -a "test \"\$(sysctl -n net.ipv6.conf.all.disable_ipv6)\" = '${expected_ipv6}' && test \"\$(sysctl -n net.ipv6.conf.default.disable_ipv6)\" = '${expected_ipv6}' && test \"\$(sysctl -n net.ipv6.conf.lo.disable_ipv6)\" = '${expected_ipv6}'" >/dev/null
  fi

  if [[ "$feature_monitoring_agent" == "true" ]]; then
    node_exporter_port="$(jq -r --arg alias "$alias" '.remnawave_runtime_host_vars[$alias].monitoring_agent_node_exporter_port // 9100' "$runtime_vars")"
    cadvisor_port="$(jq -r --arg alias "$alias" '.remnawave_runtime_host_vars[$alias].monitoring_agent_cadvisor_port // 8080' "$runtime_vars")"
    promtail_enabled="$(jq -r --arg alias "$alias" '.remnawave_runtime_host_vars[$alias].monitoring_agent_promtail_enabled // true' "$runtime_vars")"
    node_exporter_enabled="$(jq -r --arg alias "$alias" '.remnawave_runtime_host_vars[$alias].monitoring_agent_enable_node_exporter // true' "$runtime_vars")"
    cadvisor_enabled="$(jq -r --arg alias "$alias" '.remnawave_runtime_host_vars[$alias].monitoring_agent_enable_cadvisor // true' "$runtime_vars")"
    agent_bind_address="$(jq -r --arg alias "$alias" '
      .remnawave_runtime_host_vars[$alias].monitoring_agent_bind_address
      // .fleet_hosts[$alias].monitoring.agent_bind_address
      // "127.0.0.1"
    ' "$runtime_vars")"
    agent_probe_host="$agent_bind_address"
    if [[ "$agent_probe_host" == "0.0.0.0" ]]; then
      agent_probe_host="127.0.0.1"
    fi

    echo "[smoke][$alias] Check monitoring_agent containers and ports"
    if [[ "$node_exporter_enabled" == "true" ]]; then
      run_ansible "$alias" -b -m ansible.builtin.shell -a "docker ps --filter name=^/monitoring-node-exporter$ | grep -q monitoring-node-exporter" >/dev/null
    fi
    if [[ "$cadvisor_enabled" == "true" ]]; then
      run_ansible "$alias" -b -m ansible.builtin.shell -a "docker ps --filter name=^/monitoring-cadvisor$ | grep -q monitoring-cadvisor" >/dev/null
    fi
    if [[ "$promtail_enabled" == "true" ]]; then
      run_ansible "$alias" -b -m ansible.builtin.shell -a "docker ps --filter name=^/monitoring-promtail-agent$ | grep -q monitoring-promtail-agent" >/dev/null
    fi
    if [[ "$node_exporter_enabled" == "true" ]]; then
      run_ansible "$alias" -b -m ansible.builtin.shell -a "curl --silent --show-error --fail 'http://${agent_probe_host}:${node_exporter_port}/metrics' >/dev/null" >/dev/null
    fi
    if [[ "$cadvisor_enabled" == "true" ]]; then
      run_ansible "$alias" -b -m ansible.builtin.shell -a "curl --silent --show-error --fail 'http://${agent_probe_host}:${cadvisor_port}/metrics' >/dev/null" >/dev/null
    fi
  fi

	  if [[ "$feature_monitoring_stack" == "true" ]]; then
	    echo "[smoke][$alias] Check monitoring_stack services"
	    run_ansible "$alias" -b -m ansible.builtin.shell -a "docker ps --filter name=^/monitoring-prometheus$ | grep -q monitoring-prometheus" >/dev/null
	    run_ansible "$alias" -b -m ansible.builtin.shell -a "docker ps --filter name=^/monitoring-alertmanager$ | grep -q monitoring-alertmanager" >/dev/null
	    run_ansible "$alias" -b -m ansible.builtin.shell -a "docker ps --filter name=^/monitoring-grafana$ | grep -q monitoring-grafana" >/dev/null
	    run_ansible "$alias" -b -m ansible.builtin.shell -a "docker ps --filter name=^/monitoring-loki$ | grep -q monitoring-loki" >/dev/null
	    run_ansible_retry "$alias" 12 5 -b -m ansible.builtin.shell -a "curl --silent --show-error --fail --connect-timeout 3 'http://127.0.0.1:9090/-/ready' >/dev/null"
	    run_ansible_retry "$alias" 12 5 -b -m ansible.builtin.shell -a "curl --silent --show-error --fail --connect-timeout 3 'http://127.0.0.1:9093/-/ready' >/dev/null"
	    run_ansible_retry "$alias" 12 5 -b -m ansible.builtin.shell -a "curl --silent --show-error --fail --connect-timeout 3 'http://127.0.0.1:3100/ready' >/dev/null"
	    run_ansible_retry "$alias" 12 5 -b -m ansible.builtin.shell -a "curl --silent --show-error --fail --connect-timeout 3 'http://127.0.0.1:3000/api/health' >/dev/null"

	    echo "[smoke][$alias] Check monitoring target coverage, health and duplicates"
	    expected_node_instances_json="$(
	      jq -c '
	        [
	          .fleet_hosts
	          | to_entries[]
	          | select(((.value.features.feature_monitoring_agent // false) | tostring | ascii_downcase) == "true")
	          | .key
	        ] | unique | sort
	      ' "$runtime_vars"
	    )"
	    expected_cadvisor_instances_json="$(
	      jq -c '
	        [
	          .fleet_hosts
	          | to_entries[]
	          | select(((.value.features.feature_monitoring_agent // false) | tostring | ascii_downcase) == "true")
	          | .key
	        ] | unique | sort
	      ' "$runtime_vars"
	    )"
	    prom_targets_check_script="$(
	      cat <<'PY'
import base64
import collections
import json
import sys
import urllib.request

expected_node = __EXPECTED_NODE_JSON__
expected_cadvisor = __EXPECTED_CADVISOR_JSON__
expected = {
    "monitoring-node-exporter": expected_node,
    "monitoring-cadvisor": expected_cadvisor,
}

with urllib.request.urlopen("http://127.0.0.1:9090/api/v1/targets?state=active", timeout=10) as resp:
    payload = json.load(resp)

active_targets = payload.get("data", {}).get("activeTargets", [])
errors = []

for job_name, expected_instances in expected.items():
    job_targets = []
    for target in active_targets:
        labels = target.get("labels", {})
        if labels.get("job") != job_name:
            continue
        job_targets.append(
            {
                "instance": labels.get("instance", ""),
                "health": str(target.get("health", "")).lower(),
            }
        )

    observed_instances = [item["instance"] for item in job_targets]
    counts = collections.Counter(observed_instances)
    missing_instances = sorted(set(expected_instances) - set(observed_instances))
    duplicate_instances = sorted(instance for instance, count in counts.items() if count > 1)
    unexpected_instances = sorted(set(observed_instances) - set(expected_instances))
    unhealthy_expected = sorted(
        instance
        for instance in expected_instances
        if not any(t["instance"] == instance and t["health"] == "up" for t in job_targets)
    )

    if missing_instances:
        errors.append(f"{job_name}: missing {missing_instances}")
    if duplicate_instances:
        errors.append(f"{job_name}: duplicates {duplicate_instances}")
    if unexpected_instances:
        errors.append(f"{job_name}: unexpected {unexpected_instances}")
    if unhealthy_expected:
        errors.append(f"{job_name}: not-up {unhealthy_expected}")

if errors:
    print("; ".join(errors))
    sys.exit(1)

print("monitoring-targets-ok")
PY
	    )"
	    prom_targets_check_script="${prom_targets_check_script/__EXPECTED_NODE_JSON__/${expected_node_instances_json}}"
	    prom_targets_check_script="${prom_targets_check_script/__EXPECTED_CADVISOR_JSON__/${expected_cadvisor_instances_json}}"
	    prom_targets_check_script_b64="$(printf '%s' "${prom_targets_check_script}" | base64 | tr -d '\n')"
	    run_ansible_retry "$alias" 8 5 -b -m ansible.builtin.shell -a "python3 -c \"import base64; exec(base64.b64decode('${prom_targets_check_script_b64}'))\"" >/dev/null

	    run_ansible "$alias" -b -m ansible.builtin.shell -a "test -f /opt/monitoring-stack/prometheus/alerts.yml" >/dev/null
	    run_ansible "$alias" -b -m ansible.builtin.shell -a "test -f /opt/monitoring-stack/grafana/provisioning/datasources/datasources.yml" >/dev/null
	    run_ansible "$alias" -b -m ansible.builtin.shell -a "test -f /opt/monitoring-stack/grafana/dashboards/remna-fleet-overview.json" >/dev/null
	    run_ansible "$alias" -b -m ansible.builtin.shell -a "test -f /opt/monitoring-stack/grafana/dashboards/remna-node-drilldown.json" >/dev/null
  fi

done

echo "Smoke checks passed for: ${targets[*]}"
