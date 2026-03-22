#!/usr/bin/env python3
"""Collect OpenWrt settings over SSH and emit fleet host YAML block."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None


PASSWALL2_DEFAULT_PROBE_URL = "https://www.gstatic.com/generate_204"
PASSWALL2_DEFAULT_SOCKS_PORT = 1070
MONITORING_DEFAULT_PORT = 9100
MONITORING_DEFAULT_INTERVAL = 1
WAN_FAILOVER_DEFAULT_WAIT = 35
WAN_FAILOVER_DEFAULT_PROBE_HOST = "1.1.1.1"
WAN_FAILOVER_DEFAULT_PROBE_COUNT = 2
WAN_FAILOVER_DEFAULT_START_PRIORITY = 97
DEFAULT_DOCKER_PACKAGES = ["dockerd", "docker", "docker-compose"]


REMOTE_PROBE_SCRIPT = r"""
set +e

get_uci() {
  uci -q get "$1" 2>/dev/null || true
}

service_present() {
  if [ -x "/etc/init.d/$1" ]; then
    echo "1"
  else
    echo "0"
  fi
}

service_enabled() {
  if [ -x "/etc/init.d/$1" ] && "/etc/init.d/$1" enabled >/dev/null 2>&1; then
    echo "1"
  else
    echo "0"
  fi
}

pkg_names="$(opkg list-installed 2>/dev/null | awk '{print $1}' | tr '\n' ' ')"

has_pkg() {
  case " ${pkg_names} " in
    *" $1 "*) echo "1" ;;
    *) echo "0" ;;
  esac
}

network_cfg_present=0
firewall_cfg_present=0
homeproxy_cfg_present=0
if [ -f /etc/config/network ]; then
  network_cfg_present=1
fi
if [ -f /etc/config/firewall ]; then
  firewall_cfg_present=1
fi
if [ -f /etc/config/homeproxy ]; then
  homeproxy_cfg_present=1
fi

wan_failover_enabled=0
wan_failover_wait_sec=""
wan_failover_probe_host=""
wan_failover_probe_count=""
wan_failover_start_priority=""
if [ -f /etc/config/wan_failover ]; then
  enabled_raw="$(get_uci wan_failover.main.enabled)"
  case "${enabled_raw}" in
    1|true|TRUE|yes|YES|on|ON) wan_failover_enabled=1 ;;
    *) wan_failover_enabled=0 ;;
  esac
  wan_failover_wait_sec="$(get_uci wan_failover.main.dhcp_wait_sec)"
  wan_failover_probe_host="$(get_uci wan_failover.main.probe_host)"
  wan_failover_probe_count="$(get_uci wan_failover.main.probe_count)"
fi
if [ -f /etc/init.d/wan_failover ]; then
  wan_failover_start_priority="$(sed -n 's/^START=//p' /etc/init.d/wan_failover 2>/dev/null | head -n1)"
fi

zt_ip="$(ip -4 -o addr show 2>/dev/null | awk '$2 ~ /^zt/ {split($4, a, "/"); print a[1]; exit}')"
zt_network_id="$(get_uci zerotier.mynet.id)"
zt_secret="$(get_uci zerotier.global.secret)"
zt_src_cidr="$(get_uci firewall.rule_ssh_zerotier.src_ip)"

passwall2_cfg_present=0
if [ -f /etc/config/passwall2 ]; then
  passwall2_cfg_present=1
fi
pw_global_sid="$(uci -q show passwall2 2>/dev/null | sed -n 's/^passwall2\.\([^.]*\)=global$/\1/p' | head -n1)"
pw_enabled=""
pw_socks_port=""
if [ -n "${pw_global_sid}" ]; then
  pw_enabled="$(get_uci "passwall2.${pw_global_sid}.enabled")"
  pw_socks_port="$(get_uci "passwall2.${pw_global_sid}.node_socks_port")"
fi
pw_sub_sid="$(uci -q show passwall2 2>/dev/null | sed -n 's/^passwall2\.\([^.]*\)=subscribe_list$/\1/p' | head -n1)"
pw_subscribe_url=""
if [ -n "${pw_sub_sid}" ]; then
  pw_subscribe_url="$(get_uci "passwall2.${pw_sub_sid}.url")"
fi
pw_probe_url=""
for sid in $(uci -q show passwall2 2>/dev/null | sed -n 's/^passwall2\.\([^.]*\)=nodes$/\1/p'); do
  protocol="$(get_uci "passwall2.${sid}.protocol")"
  if [ "${protocol}" = "_balancing" ]; then
    pw_probe_url="$(get_uci "passwall2.${sid}.probeUrl")"
    break
  fi
done
pw_acl_macs=""
for sid in $(uci -q show passwall2 2>/dev/null | sed -n 's/^passwall2\.\([^.]*\)=acl_rule$/\1/p'); do
  source_mac="$(get_uci "passwall2.${sid}.source_mac")"
  [ -n "${source_mac}" ] || continue
  case ",${pw_acl_macs}," in
    *,"${source_mac}",*) ;;
    *)
      if [ -n "${pw_acl_macs}" ]; then
        pw_acl_macs="${pw_acl_macs},${source_mac}"
      else
        pw_acl_macs="${source_mac}"
      fi
      ;;
  esac
done

monitoring_port="$(get_uci 'prometheus-node-exporter-lua.@prometheus-node-exporter-lua[0].listen_port')"
monitoring_probe_interval=""
if [ -f /etc/crontabs/root ]; then
  probe_spec="$(awk '/openwrt-connectivity-probe\.sh/ {print $1; exit}' /etc/crontabs/root 2>/dev/null)"
  case "${probe_spec}" in
    \*/[0-9]*) monitoring_probe_interval="${probe_spec#*/}" ;;
    *) monitoring_probe_interval="" ;;
  esac
fi

docker_daemon_present=0
docker_daemon_json=""
if [ -f /etc/docker/daemon.json ]; then
  docker_daemon_present=1
  docker_daemon_json="$(tr '\n' ' ' < /etc/docker/daemon.json 2>/dev/null | sed 's/[[:space:]]\+/ /g; s/^ //; s/ $//')"
fi

docker_runtime_pkgs=""
for pkg in dockerd docker docker-compose; do
  if [ "$(has_pkg "${pkg}")" = "1" ]; then
    if [ -n "${docker_runtime_pkgs}" ]; then
      docker_runtime_pkgs="${docker_runtime_pkgs},${pkg}"
    else
      docker_runtime_pkgs="${pkg}"
    fi
  fi
done

docker_stack_names=""
if [ -d /opt/docker-stacks ]; then
  for compose_file in /opt/docker-stacks/*/docker-compose.yml; do
    [ -f "${compose_file}" ] || continue
    stack_name="$(basename "$(dirname "${compose_file}")")"
    if [ -n "${docker_stack_names}" ]; then
      docker_stack_names="${docker_stack_names},${stack_name}"
    else
      docker_stack_names="${stack_name}"
    fi
  done
fi

dropbear_sections="$(uci -q show dropbear 2>/dev/null | sed -n 's/^dropbear\.\([^.]*\)=dropbear$/\1/p')"
ssh_pw_auth_off=1
ssh_root_pw_auth_off=1
if [ -z "${dropbear_sections}" ]; then
  ssh_pw_auth_off=0
  ssh_root_pw_auth_off=0
fi
for sid in ${dropbear_sections}; do
  pw_auth="$(get_uci "dropbear.${sid}.PasswordAuth")"
  root_pw_auth="$(get_uci "dropbear.${sid}.RootPasswordAuth")"
  [ "${pw_auth}" = "off" ] || ssh_pw_auth_off=0
  [ "${root_pw_auth}" = "off" ] || ssh_root_pw_auth_off=0
done

printf '%s=%s\n' "NETWORK_CONFIG_PRESENT" "${network_cfg_present}"
printf '%s=%s\n' "FIREWALL_CONFIG_PRESENT" "${firewall_cfg_present}"
printf '%s=%s\n' "LAN_DEVICE" "$(get_uci network.lan.device)"
printf '%s=%s\n' "LAN_IPADDR" "$(get_uci network.lan.ipaddr)"
printf '%s=%s\n' "LAN_NETMASK" "$(get_uci network.lan.netmask)"
printf '%s=%s\n' "LAN_IP6ASSIGN" "$(get_uci network.lan.ip6assign)"
printf '%s=%s\n' "ULA_PREFIX" "$(get_uci network.globals.ula_prefix)"

printf '%s=%s\n' "WAN_PROTO" "$(get_uci network.wan.proto)"
printf '%s=%s\n' "WAN_DEVICE" "$(get_uci network.wan.device)"
printf '%s=%s\n' "WAN_IPADDR" "$(get_uci network.wan.ipaddr)"
printf '%s=%s\n' "WAN_NETMASK" "$(get_uci network.wan.netmask)"
printf '%s=%s\n' "WAN_GATEWAY" "$(get_uci network.wan.gateway)"
printf '%s=%s\n' "WAN_DNS" "$(get_uci network.wan.dns)"
printf '%s=%s\n' "WAN_PPPOE_USERNAME" "$(get_uci network.wan.username)"
printf '%s=%s\n' "WAN_PPPOE_PASSWORD" "$(get_uci network.wan.password)"
printf '%s=%s\n' "WAN_PPPOE_IPV6" "$(get_uci network.wan.ipv6)"
printf '%s=%s\n' "WAN_FAILOVER_ENABLED" "${wan_failover_enabled}"
printf '%s=%s\n' "WAN_FAILOVER_WAIT_SEC" "${wan_failover_wait_sec}"
printf '%s=%s\n' "WAN_FAILOVER_PROBE_HOST" "${wan_failover_probe_host}"
printf '%s=%s\n' "WAN_FAILOVER_PROBE_COUNT" "${wan_failover_probe_count}"
printf '%s=%s\n' "WAN_FAILOVER_START_PRIORITY" "${wan_failover_start_priority}"

printf '%s=%s\n' "ZT_IP" "${zt_ip}"
printf '%s=%s\n' "ZT_NETWORK_ID" "${zt_network_id}"
printf '%s=%s\n' "ZT_SECRET" "${zt_secret}"
printf '%s=%s\n' "ZT_SRC_CIDR" "${zt_src_cidr}"
printf '%s=%s\n' "ZT_PKG_INSTALLED" "$(has_pkg zerotier)"
printf '%s=%s\n' "ZT_SERVICE_PRESENT" "$(service_present zerotier)"
printf '%s=%s\n' "ZT_SERVICE_ENABLED" "$(service_enabled zerotier)"

printf '%s=%s\n' "TAILSCALE_PKG_INSTALLED" "$(has_pkg tailscale)"
printf '%s=%s\n' "TAILSCALE_SERVICE_PRESENT" "$(service_present tailscale)"
printf '%s=%s\n' "TAILSCALE_SERVICE_ENABLED" "$(service_enabled tailscale)"
printf '%s=%s\n' "TAILSCALE_BINARY_PRESENT" "$(command -v tailscale >/dev/null 2>&1 && echo 1 || echo 0)"

printf '%s=%s\n' "PASSWALL2_CONFIG_PRESENT" "${passwall2_cfg_present}"
printf '%s=%s\n' "PASSWALL2_PKG_INSTALLED" "$(has_pkg luci-app-passwall2)"
printf '%s=%s\n' "PASSWALL2_ENABLED" "${pw_enabled}"
printf '%s=%s\n' "PASSWALL2_SUBSCRIBE_URL" "${pw_subscribe_url}"
printf '%s=%s\n' "PASSWALL2_PROBE_URL" "${pw_probe_url}"
printf '%s=%s\n' "PASSWALL2_SOCKS_PORT" "${pw_socks_port}"
printf '%s=%s\n' "PASSWALL2_ACL_MACS" "${pw_acl_macs}"

printf '%s=%s\n' "MONITORING_SERVICE_PRESENT" "$(service_present prometheus-node-exporter-lua)"
printf '%s=%s\n' "MONITORING_SERVICE_ENABLED" "$(service_enabled prometheus-node-exporter-lua)"
printf '%s=%s\n' "MONITORING_EXPORTER_PORT" "${monitoring_port}"
printf '%s=%s\n' "MONITORING_PROBE_INTERVAL" "${monitoring_probe_interval}"

printf '%s=%s\n' "DOCKER_SERVICE_PRESENT" "$(service_present dockerd)"
printf '%s=%s\n' "DOCKER_SERVICE_ENABLED" "$(service_enabled dockerd)"
printf '%s=%s\n' "DOCKER_RUNTIME_PACKAGES" "${docker_runtime_pkgs}"
printf '%s=%s\n' "DOCKER_DAEMON_CONFIG_PRESENT" "${docker_daemon_present}"
printf '%s=%s\n' "DOCKER_DAEMON_CONFIG_JSON" "${docker_daemon_json}"
printf '%s=%s\n' "DOCKER_STACK_NAMES" "${docker_stack_names}"

printf '%s=%s\n' "HOMEPROXY_CONFIG_PRESENT" "${homeproxy_cfg_present}"
printf '%s=%s\n' "HOMEPROXY_PKG_INSTALLED" "$(has_pkg homeproxy)"
printf '%s=%s\n' "SSH_LOCKDOWN_PASSWORD_OFF" "${ssh_pw_auth_off}"
printf '%s=%s\n' "SSH_LOCKDOWN_ROOT_OFF" "${ssh_root_pw_auth_off}"
"""


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def warn(message: str, warnings: list[str]) -> None:
    warnings.append(message)


def parse_bool(value: str) -> bool:
    lowered = str(value or "").strip().lower()
    return lowered in {"1", "true", "yes", "on"}


def parse_int(value: str, default: int) -> int:
    try:
        return int(str(value or "").strip())
    except Exception:
        return default


def parse_csv(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def parse_whitespace_list(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split() if item.strip()]


def parse_kv_output(output: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in output.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip()
    return result


class SSHRunner:
    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        timeout: int,
        proxy_jump: str,
        host_key_check: str,
        key_file: str | None,
        password: str | None,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.timeout = timeout
        self.proxy_jump = proxy_jump
        self.host_key_check = host_key_check
        self.key_file = key_file
        self.password = password
        self.ssh_cmd = os.getenv("OPENWRT_COLLECTOR_SSH_CMD", "ssh")
        self.sshpass_cmd = os.getenv("OPENWRT_COLLECTOR_SSHPASS_CMD", "sshpass")

    def _base_ssh_command(self) -> list[str]:
        cmd = [self.ssh_cmd]
        cmd.extend(["-p", str(self.port)])
        cmd.extend(["-o", f"ConnectTimeout={self.timeout}"])
        cmd.extend(["-o", "LogLevel=ERROR"])

        if self.host_key_check == "strict":
            cmd.extend(["-o", "StrictHostKeyChecking=yes"])
        elif self.host_key_check == "accept-new":
            cmd.extend(["-o", "StrictHostKeyChecking=accept-new"])
        elif self.host_key_check == "off":
            cmd.extend(["-o", "StrictHostKeyChecking=no"])
            cmd.extend(["-o", "UserKnownHostsFile=/dev/null"])
        else:  # pragma: no cover
            fail(f"Unsupported host key check mode: {self.host_key_check}")

        if self.proxy_jump:
            cmd.extend(["-o", f"ProxyJump={self.proxy_jump}"])

        if self.key_file:
            cmd.extend(["-i", self.key_file])
            cmd.extend(["-o", "BatchMode=yes"])

        return cmd

    def run_script(self, script: str, context: str) -> str:
        cmd: list[str] = []
        env = os.environ.copy()
        if self.password is not None:
            if not shutil.which(self.sshpass_cmd):
                fail("Password mode requires sshpass in PATH.")
            cmd.extend([self.sshpass_cmd, "-e"])
            env["SSHPASS"] = self.password

        cmd.extend(self._base_ssh_command())
        cmd.extend([f"{self.user}@{self.host}", "sh", "-s"])

        proc = subprocess.run(
            cmd,
            input=script,
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )
        if proc.returncode != 0:
            fail(
                f"{context} failed (rc={proc.returncode}). "
                f"stdout={proc.stdout.strip()!r} stderr={proc.stderr.strip()!r}"
            )
        return proc.stdout

    def read_stack_compose(self, stack_name: str) -> str:
        stack_q = shlex.quote(stack_name)
        script = (
            "set +e\n"
            f"compose_file='/opt/docker-stacks/{stack_q}/docker-compose.yml'\n"
            "if [ -f \"${compose_file}\" ]; then\n"
            "  cat \"${compose_file}\"\n"
            "fi\n"
        )
        return self.run_script(script, context=f"Read docker stack '{stack_name}' compose").strip()


def build_host_block(
    args: argparse.Namespace,
    raw: dict[str, str],
    docker_stacks: list[dict[str, str]],
    warnings: list[str],
) -> dict[str, object]:
    wan_proto = str(raw.get("WAN_PROTO", "dhcp") or "dhcp").strip().lower()
    if wan_proto not in {"dhcp", "static", "pppoe"}:
        warn(f"Unsupported WAN proto '{wan_proto}', fallback to dhcp.", warnings)
        wan_proto = "dhcp"

    wan_ipaddr = str(raw.get("WAN_IPADDR", "") or "").strip()
    wan_netmask = str(raw.get("WAN_NETMASK", "") or "").strip()
    wan_pppoe_username = str(raw.get("WAN_PPPOE_USERNAME", "") or "").strip()
    wan_pppoe_password = str(raw.get("WAN_PPPOE_PASSWORD", "") or "")
    wan_pppoe_ipv6 = str(raw.get("WAN_PPPOE_IPV6", "auto") or "auto").strip().lower()
    if wan_pppoe_ipv6 not in {"auto", "0", "1"}:
        wan_pppoe_ipv6 = "auto"

    if wan_proto == "static" and (not wan_ipaddr or not wan_netmask):
        warn("Static WAN is incomplete (ipaddr/netmask), fallback to dhcp in generated block.", warnings)
        wan_proto = "dhcp"
        wan_ipaddr = ""
        wan_netmask = ""

    if wan_proto == "pppoe" and (not wan_pppoe_username or not wan_pppoe_password):
        warn("PPPoE WAN is incomplete (username/password), fallback to dhcp in generated block.", warnings)
        wan_proto = "dhcp"
        wan_pppoe_username = ""
        wan_pppoe_password = ""
        wan_pppoe_ipv6 = "auto"

    wan_failover_enabled = parse_bool(raw.get("WAN_FAILOVER_ENABLED", "0"))
    if wan_proto == "dhcp":
        wan_failover_enabled = False

    wan = {
        "enabled": True,
        "proto": wan_proto,
        "device": str(raw.get("WAN_DEVICE", "eth0") or "eth0").strip() or "eth0",
        "ipaddr": wan_ipaddr,
        "netmask": wan_netmask,
        "gateway": str(raw.get("WAN_GATEWAY", "") or "").strip(),
        "dns": parse_whitespace_list(raw.get("WAN_DNS", "")),
        "pppoe_username": wan_pppoe_username,
        "pppoe_password": wan_pppoe_password,
        "pppoe_ipv6": wan_pppoe_ipv6,
        "boot_try_dhcp_first": wan_failover_enabled,
        "boot_try_dhcp_wait_sec": parse_int(raw.get("WAN_FAILOVER_WAIT_SEC", ""), WAN_FAILOVER_DEFAULT_WAIT),
        "boot_try_dhcp_probe_host": str(raw.get("WAN_FAILOVER_PROBE_HOST", "") or "").strip()
        or WAN_FAILOVER_DEFAULT_PROBE_HOST,
        "boot_try_dhcp_probe_count": parse_int(raw.get("WAN_FAILOVER_PROBE_COUNT", ""), WAN_FAILOVER_DEFAULT_PROBE_COUNT),
        "boot_try_dhcp_service_name": "wan_failover",
        "boot_try_dhcp_start_priority": parse_int(
            raw.get("WAN_FAILOVER_START_PRIORITY", ""), WAN_FAILOVER_DEFAULT_START_PRIORITY
        ),
    }

    network = {
        "lan_device": str(raw.get("LAN_DEVICE", "br-lan") or "br-lan").strip() or "br-lan",
        "lan_ipaddr": str(raw.get("LAN_IPADDR", "192.168.1.1") or "192.168.1.1").strip() or "192.168.1.1",
        "lan_netmask": str(raw.get("LAN_NETMASK", "255.255.255.0") or "255.255.255.0").strip() or "255.255.255.0",
        "lan_ip6assign": str(raw.get("LAN_IP6ASSIGN", "60") or "60").strip() or "60",
        "ula_prefix": str(raw.get("ULA_PREFIX", "") or "").strip(),
    }

    zt_network_id = str(raw.get("ZT_NETWORK_ID", "") or "").strip()
    zt_secret = str(raw.get("ZT_SECRET", "") or "")
    zerotier_detected = any(
        [
            parse_bool(raw.get("ZT_PKG_INSTALLED", "0")),
            parse_bool(raw.get("ZT_SERVICE_PRESENT", "0")),
            parse_bool(raw.get("ZT_SERVICE_ENABLED", "0")),
            bool(zt_network_id),
        ]
    )
    zerotier_enabled = zerotier_detected
    if zerotier_enabled and not zt_network_id:
        warn("ZeroTier detected but network_id is empty; zerotier.enabled set to false for fleet compatibility.", warnings)
        zerotier_enabled = False

    zerotier = {
        "enabled": zerotier_enabled,
        "network_id": zt_network_id,
        "manage_secret": bool(zt_secret),
        "secret": zt_secret,
        "src_cidr": str(raw.get("ZT_SRC_CIDR", "") or "").strip() or "172.16.0.0/12",
    }

    passwall2_present = parse_bool(raw.get("PASSWALL2_CONFIG_PRESENT", "0")) or parse_bool(
        raw.get("PASSWALL2_PKG_INSTALLED", "0")
    )
    passwall2_enabled = parse_bool(raw.get("PASSWALL2_ENABLED", "0"))
    passwall2_subscribe_url = str(raw.get("PASSWALL2_SUBSCRIBE_URL", "") or "")
    if passwall2_present and passwall2_enabled and not passwall2_subscribe_url.strip():
        warn("Passwall2 enabled but subscribe URL is empty; forcing passwall2.enabled=false in generated block.", warnings)
        passwall2_enabled = False

    passwall2_probe_url = str(raw.get("PASSWALL2_PROBE_URL", "") or "").strip() or PASSWALL2_DEFAULT_PROBE_URL
    passwall2_socks_port = parse_int(raw.get("PASSWALL2_SOCKS_PORT", ""), PASSWALL2_DEFAULT_SOCKS_PORT)
    if passwall2_socks_port <= 0 or passwall2_socks_port > 65535:
        passwall2_socks_port = PASSWALL2_DEFAULT_SOCKS_PORT

    passwall2 = {
        "enabled": passwall2_enabled,
        "subscribe_url": passwall2_subscribe_url,
        "probe_url": passwall2_probe_url,
        "socks_port": passwall2_socks_port,
        "profile_overrides": {},
        "acl_bypass_macs": parse_csv(raw.get("PASSWALL2_ACL_MACS", "")),
    }

    monitoring_enabled = any(
        [
            parse_bool(raw.get("MONITORING_SERVICE_PRESENT", "0")),
            parse_bool(raw.get("MONITORING_SERVICE_ENABLED", "0")),
            bool(str(raw.get("MONITORING_EXPORTER_PORT", "") or "").strip()),
        ]
    )
    node_exporter_port = parse_int(raw.get("MONITORING_EXPORTER_PORT", ""), MONITORING_DEFAULT_PORT)
    if node_exporter_port <= 0 or node_exporter_port > 65535:
        node_exporter_port = MONITORING_DEFAULT_PORT
    probe_interval = parse_int(raw.get("MONITORING_PROBE_INTERVAL", ""), MONITORING_DEFAULT_INTERVAL)
    if probe_interval <= 0:
        probe_interval = MONITORING_DEFAULT_INTERVAL
    if probe_interval > 60:
        probe_interval = 60

    monitoring = {
        "openwrt_monitoring_enabled": monitoring_enabled,
        "openwrt_node_exporter_port": node_exporter_port,
        "openwrt_probe_interval_minutes": probe_interval,
    }

    runtime_packages = parse_csv(raw.get("DOCKER_RUNTIME_PACKAGES", ""))
    docker_service_detected = parse_bool(raw.get("DOCKER_SERVICE_PRESENT", "0")) or parse_bool(
        raw.get("DOCKER_SERVICE_ENABLED", "0")
    )
    docker_manage_runtime = docker_service_detected or bool(runtime_packages)
    if docker_manage_runtime and not runtime_packages:
        runtime_packages = list(DEFAULT_DOCKER_PACKAGES)

    docker_daemon_present = parse_bool(raw.get("DOCKER_DAEMON_CONFIG_PRESENT", "0"))
    daemon_config: dict[str, object] = {}
    daemon_raw = str(raw.get("DOCKER_DAEMON_CONFIG_JSON", "") or "").strip()
    if docker_daemon_present and daemon_raw:
        try:
            parsed = json.loads(daemon_raw)
            if isinstance(parsed, dict):
                daemon_config = parsed
            else:
                warn("Docker daemon config is not a JSON object; exporting empty daemon_config.", warnings)
        except Exception:
            warn("Docker daemon config is not valid JSON; exporting empty daemon_config.", warnings)

    docker = {
        "manage_runtime": docker_manage_runtime,
        "runtime_packages": runtime_packages,
        "manage_daemon_config": docker_daemon_present,
        "daemon_config": daemon_config,
        "compose_command": "docker-compose",
        "stacks": docker_stacks,
    }

    tailscale_detected = any(
        [
            parse_bool(raw.get("TAILSCALE_PKG_INSTALLED", "0")),
            parse_bool(raw.get("TAILSCALE_SERVICE_PRESENT", "0")),
            parse_bool(raw.get("TAILSCALE_SERVICE_ENABLED", "0")),
            parse_bool(raw.get("TAILSCALE_BINARY_PRESENT", "0")),
        ]
    )
    homeproxy_detected = parse_bool(raw.get("HOMEPROXY_CONFIG_PRESENT", "0")) or parse_bool(
        raw.get("HOMEPROXY_PKG_INSTALLED", "0")
    )

    features = {
        "feature_openwrt_base": True,
        "feature_openwrt_network_core": parse_bool(raw.get("NETWORK_CONFIG_PRESENT", "0")),
        "feature_openwrt_firewall_core": parse_bool(raw.get("FIREWALL_CONFIG_PRESENT", "0")),
        "feature_openwrt_wan": True,
        "feature_openwrt_wan_apply_in_prod": False,
        "feature_openwrt_zerotier": zerotier_detected,
        "feature_tailscale": tailscale_detected,
        "feature_openwrt_passwall2": passwall2_present,
        "feature_openwrt_homeproxy_cleanup": not homeproxy_detected,
        "feature_openwrt_docker_runtime": docker_manage_runtime,
        "feature_openwrt_docker_stacks": len(docker_stacks) > 0,
        "feature_openwrt_monitoring_agent": monitoring_enabled,
        "feature_openwrt_ssh_lockdown": parse_bool(raw.get("SSH_LOCKDOWN_PASSWORD_OFF", "0"))
        and parse_bool(raw.get("SSH_LOCKDOWN_ROOT_OFF", "0")),
    }

    access: dict[str, object] = {
        "lan_host": args.host,
        "lan_port": args.port,
    }
    zt_ip = str(raw.get("ZT_IP", "") or "").strip()
    if zt_ip:
        access["zt_host"] = zt_ip
        access["zt_port"] = 22
    if args.proxy_jump:
        access["proxy_jumps"] = [args.proxy_jump]

    bootstrap_password = args.password if args.password is not None else ""
    if not bootstrap_password:
        warn("Bootstrap password cannot be discovered from router state; bootstrap.password left empty.", warnings)

    return {
        "profile": "prod_update",
        "deploy_user": args.user,
        "access": access,
        "bootstrap": {
            "username": args.user,
            "password": bootstrap_password,
        },
        "features": features,
        "zerotier": zerotier,
        "network": network,
        "passwall2": passwall2,
        "monitoring": monitoring,
        "docker": docker,
        "wan": wan,
        "custom_roles": [],
    }


def collect_docker_stacks(runner: SSHRunner, raw: dict[str, str], warnings: list[str]) -> list[dict[str, str]]:
    stacks: list[dict[str, str]] = []
    for stack_name in parse_csv(raw.get("DOCKER_STACK_NAMES", "")):
        compose_content = runner.read_stack_compose(stack_name)
        if not compose_content.strip():
            warn(f"Docker stack '{stack_name}' has no readable docker-compose.yml; skipped.", warnings)
            continue
        stacks.append(
            {
                "name": stack_name,
                "compose_content": compose_content,
            }
        )
    return stacks


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect OpenWrt config over SSH and generate fleet host YAML block."
    )
    parser.add_argument("--alias", required=True, help="Target host alias for fleet.hosts.<alias>.")
    parser.add_argument("--host", required=True, help="SSH endpoint host/IP.")
    parser.add_argument("--user", required=True, help="SSH username.")
    parser.add_argument("--port", type=int, default=22, help="SSH port (default: 22).")
    parser.add_argument("--proxy-jump", default="", help="Optional SSH ProxyJump value.")
    parser.add_argument("--timeout", type=int, default=8, help="SSH connect timeout in seconds (default: 8).")
    parser.add_argument(
        "--host-key-check",
        choices=["accept-new", "strict", "off"],
        default="accept-new",
        help="SSH host key check mode (default: accept-new).",
    )

    auth = parser.add_mutually_exclusive_group(required=True)
    auth.add_argument("--key-file", help="SSH private key file path.")
    auth.add_argument("--password", help="SSH password (uses sshpass).")

    parser.add_argument("--yaml-out", default="", help="Optional file path for generated YAML host block.")
    parser.add_argument("--json-out", default="", help="Optional file path for raw collection report JSON.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if yaml is None:
        fail("PyYAML is required to run this script.")

    if args.port <= 0 or args.port > 65535:
        fail("--port must be in range 1..65535.")
    if args.timeout <= 0:
        fail("--timeout must be > 0.")

    key_file: str | None = None
    if args.key_file:
        key_path = Path(args.key_file).expanduser()
        if not key_path.exists():
            fail(f"Key file does not exist: {key_path}")
        key_file = str(key_path)

    runner = SSHRunner(
        host=args.host,
        port=args.port,
        user=args.user,
        timeout=args.timeout,
        proxy_jump=args.proxy_jump.strip(),
        host_key_check=args.host_key_check,
        key_file=key_file,
        password=args.password,
    )

    warnings: list[str] = []
    raw_output = runner.run_script(REMOTE_PROBE_SCRIPT, context="OpenWrt probe")
    raw = parse_kv_output(raw_output)
    docker_stacks = collect_docker_stacks(runner, raw, warnings)
    host_block = build_host_block(args, raw, docker_stacks, warnings)

    payload = {args.alias: host_block}
    yaml_text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=False)
    if args.yaml_out:
        Path(args.yaml_out).write_text(yaml_text, encoding="utf-8")
    else:
        print(yaml_text.rstrip())

    report = {
        "alias": args.alias,
        "ssh": {
            "host": args.host,
            "port": args.port,
            "user": args.user,
            "proxy_jump": args.proxy_jump.strip(),
            "host_key_check": args.host_key_check,
            "auth_mode": "password" if args.password is not None else "key_file",
        },
        "warnings": warnings,
        "collected_raw": raw,
        "generated_host_block": payload,
    }
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")

    for message in warnings:
        print(f"WARN: {message}", file=sys.stderr)


if __name__ == "__main__":
    main()
