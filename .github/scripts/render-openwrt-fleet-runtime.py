#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None


FEATURE_DEFAULTS = {
    "feature_openwrt_base": True,
    "feature_openwrt_wan": False,
    "feature_openwrt_zerotier": True,
    "feature_tailscale": False,
    "feature_openwrt_passwall2": True,
    "feature_openwrt_homeproxy_cleanup": True,
    "feature_openwrt_docker_stacks": True,
    "feature_openwrt_monitoring_agent": True,
    "feature_openwrt_ssh_lockdown": False,
}

PASSWALL2_DEFAULTS = {
    "enabled": True,
    "subscribe_url": "",
    "probe_url": "https://www.gstatic.com/generate_204",
    "socks_port": 1070,
    "profile_overrides": {},
}

ZEROTIER_DEFAULTS = {
    "enabled": True,
    "network_id": "",
    "manage_secret": False,
    "secret": "",
}

MONITORING_DEFAULTS = {
    "openwrt_monitoring_enabled": True,
    "openwrt_node_exporter_port": 9100,
    "openwrt_probe_interval_minutes": 1,
}

DOCKER_DEFAULTS = {
    "manage_runtime": True,
    "compose_command": "docker-compose",
    "stacks": [],
}

WAN_DEFAULTS = {
    "enabled": True,
    "proto": "dhcp",
    "device": "eth0",
    "ipaddr": "",
    "netmask": "",
    "gateway": "",
    "dns": [],
    "pppoe_username": "",
    "pppoe_password": "",
    "pppoe_ipv6": "auto",
}
DEFAULT_SSH_KEY_FILE = "~/.ssh/id_ed25519"


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def parse_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    fail(f"Cannot parse boolean value: {value!r}")


def parse_int(value, field: str, min_value: int, max_value: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        fail(f"Field '{field}' must be an integer, got {value!r}")
    if parsed < min_value or parsed > max_value:
        fail(f"Field '{field}' must be in range {min_value}..{max_value}")
    return parsed


def load_fleet_config(path: Path):
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        fail("OpenWrt fleet config file is empty.")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        if yaml is None:
            fail("OpenWrt fleet config is not valid JSON and PyYAML is unavailable for YAML parsing.")
        try:
            data = yaml.safe_load(raw)
        except Exception as exc:
            fail(f"Unable to parse OpenWrt fleet config as YAML: {exc}")

    if not isinstance(data, dict):
        fail("OpenWrt fleet config root must be an object.")
    if "hosts" not in data or not isinstance(data["hosts"], dict) or not data["hosts"]:
        fail("OpenWrt fleet config must contain non-empty object field 'hosts'.")

    defaults = data.get("defaults", {})
    if defaults is None:
        defaults = {}
    if not isinstance(defaults, dict):
        fail("Field 'defaults' must be an object when provided.")

    return data["hosts"], defaults


def endpoint_reachable(host: str, port: int, timeout_sec: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_sec):
            return True
    except Exception:
        return False


def choose_endpoint(alias: str, access: dict, mode: str, check_connectivity: bool, timeout_sec: float):
    if mode == "bootstrap":
        preferred = ["lan", "zt"]
    else:
        preferred = ["zt", "lan"]

    candidates = []
    for kind in preferred:
        host_key = f"{kind}_host"
        port_key = f"{kind}_port"
        host = str(access.get(host_key, "") or "").strip()
        if not host:
            continue
        port = parse_int(access.get(port_key, access.get("ansible_port", 22)), f"access.{port_key}", 1, 65535)
        candidates.append((kind, host, port))

    if not candidates:
        fail(f"Host '{alias}' must define at least one endpoint: access.lan_host or access.zt_host.")

    if not check_connectivity:
        return candidates[0]

    for kind, host, port in candidates:
        if endpoint_reachable(host, port, timeout_sec):
            return kind, host, port

    # Fallback to preferred candidate if checks failed (can still work with proxy/jump timing)
    return candidates[0]


def normalize_proxy_jumps(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        candidate = value.strip()
        return [candidate] if candidate else []
    if isinstance(value, list):
        jumps: list[str] = []
        for item in value:
            if not isinstance(item, str):
                fail(f"proxy_jumps entries must be strings, got {item!r}")
            candidate = item.strip()
            if candidate:
                jumps.append(candidate)
        return jumps
    fail(f"proxy_jumps must be string or list of strings, got {type(value).__name__}")


def parse_jump_endpoint(jump: str) -> tuple[str, int]:
    value = jump.strip()
    if "@" in value:
        value = value.split("@", 1)[1]
    host = value
    port = 22
    if value.startswith("[") and "]" in value:
        right = value.find("]")
        host = value[1:right]
        remain = value[right + 1 :]
        if remain.startswith(":"):
            port = parse_int(remain[1:], "proxy_jump_port", 1, 65535)
        return host, port
    if ":" in value and value.count(":") == 1:
        host, port_str = value.rsplit(":", 1)
        port = parse_int(port_str, "proxy_jump_port", 1, 65535)
    return host, port


def choose_proxy_jump(proxy_jumps: list[str], check_connectivity: bool, timeout_sec: float) -> str:
    if not proxy_jumps:
        return ""
    if not check_connectivity:
        return proxy_jumps[0]
    for jump in proxy_jumps:
        host, port = parse_jump_endpoint(jump)
        if endpoint_reachable(host, port, timeout_sec):
            return jump
    return proxy_jumps[0]


def normalize_host(alias: str, host_cfg: dict, defaults: dict, mode: str, check_connectivity: bool, timeout_sec: float):
    if not isinstance(host_cfg, dict):
        fail(f"Host '{alias}' config must be an object.")

    ansible_port_default = parse_int(
        host_cfg.get("ansible_port", defaults.get("ansible_port", 22)),
        f"hosts.{alias}.ansible_port",
        1,
        65535,
    )

    deploy_user = str(host_cfg.get("deploy_user", defaults.get("deploy_user", "root")) or "").strip()
    if not deploy_user:
        fail(f"Host '{alias}' requires non-empty deploy_user.")

    defaults_bootstrap = defaults.get("bootstrap", {}) or {}
    if not isinstance(defaults_bootstrap, dict):
        fail("defaults.bootstrap must be an object when provided.")

    bootstrap = host_cfg.get("bootstrap", {}) or {}
    if not isinstance(bootstrap, dict):
        fail(f"Host '{alias}' bootstrap must be an object.")

    bootstrap_username = str(bootstrap.get("username", defaults_bootstrap.get("username", "root")) or "").strip()
    bootstrap_password = bootstrap.get("password", defaults_bootstrap.get("password", ""))
    if not bootstrap_username:
        fail(f"Host '{alias}' bootstrap.username must be a non-empty string.")
    if bootstrap_password is None:
        bootstrap_password = ""
    if not isinstance(bootstrap_password, str):
        fail(f"Host '{alias}' bootstrap.password must be a string when provided.")

    defaults_access = defaults.get("access", {}) or {}
    if not isinstance(defaults_access, dict):
        fail("defaults.access must be an object when provided.")

    access_cfg = defaults_access.copy()
    access_cfg.update(host_cfg.get("access", {}) or {})
    ssh_private_key_file = str(
        access_cfg.get(
            "ssh_private_key_file",
            host_cfg.get("ansible_ssh_private_key_file", defaults.get("ansible_ssh_private_key_file", DEFAULT_SSH_KEY_FILE)),
        )
        or ""
    ).strip()
    if not ssh_private_key_file:
        fail(f"Host '{alias}' requires non-empty SSH private key path.")

    # Backward-compatible fallback if ansible_host is provided directly.
    direct_ansible_host = str(host_cfg.get("ansible_host", defaults.get("ansible_host", "")) or "").strip()
    if direct_ansible_host:
        access_cfg.setdefault("lan_host", direct_ansible_host)
        access_cfg.setdefault("zt_host", direct_ansible_host)

    access_cfg.setdefault("ansible_port", ansible_port_default)

    proxy_jumps = normalize_proxy_jumps(access_cfg.get("proxy_jumps", None))
    if not proxy_jumps:
        proxy_jumps = normalize_proxy_jumps(access_cfg.get("proxy_jump", ""))
    selected_proxy_jump = choose_proxy_jump(proxy_jumps, check_connectivity=check_connectivity, timeout_sec=timeout_sec)

    access = {
        "lan_host": str(access_cfg.get("lan_host", "") or "").strip(),
        "zt_host": str(access_cfg.get("zt_host", "") or "").strip(),
        "proxy_jumps": proxy_jumps,
        "proxy_jump": selected_proxy_jump,
        "lan_port": parse_int(access_cfg.get("lan_port", ansible_port_default), f"hosts.{alias}.access.lan_port", 1, 65535),
        "zt_port": parse_int(access_cfg.get("zt_port", ansible_port_default), f"hosts.{alias}.access.zt_port", 1, 65535),
        "ansible_port": ansible_port_default,
    }

    selected_kind, selected_host, selected_port = choose_endpoint(
        alias, access, mode, check_connectivity, timeout_sec
    )

    default_features = defaults.get("features", {}) or {}
    if not isinstance(default_features, dict):
        fail("defaults.features must be an object when provided.")

    features = FEATURE_DEFAULTS.copy()
    features.update(default_features)
    features.update(host_cfg.get("features", {}) or {})
    normalized_features = {k: parse_bool(features.get(k, FEATURE_DEFAULTS[k])) for k in FEATURE_DEFAULTS}

    default_passwall2 = defaults.get("passwall2", {}) or {}
    if not isinstance(default_passwall2, dict):
        fail("defaults.passwall2 must be an object when provided.")

    passwall2 = PASSWALL2_DEFAULTS.copy()
    passwall2.update(default_passwall2)
    passwall2.update(host_cfg.get("passwall2", {}) or {})

    passwall2["enabled"] = parse_bool(passwall2.get("enabled", True))
    passwall2["probe_url"] = str(passwall2.get("probe_url", PASSWALL2_DEFAULTS["probe_url"]) or "").strip()
    passwall2["subscribe_url"] = str(passwall2.get("subscribe_url", "") or "").strip()
    passwall2["socks_port"] = parse_int(passwall2.get("socks_port", 1070), f"hosts.{alias}.passwall2.socks_port", 1, 65535)

    profile_overrides = passwall2.get("profile_overrides", {}) or {}
    if not isinstance(profile_overrides, dict):
        fail(f"Host '{alias}' passwall2.profile_overrides must be an object.")
    passwall2["profile_overrides"] = profile_overrides

    if normalized_features["feature_openwrt_passwall2"] and passwall2["enabled"] and not passwall2["subscribe_url"]:
        fail(f"Host '{alias}' requires passwall2.subscribe_url when feature_openwrt_passwall2 is enabled.")

    default_zerotier = defaults.get("zerotier", {}) or {}
    if not isinstance(default_zerotier, dict):
        fail("defaults.zerotier must be an object when provided.")

    zerotier = ZEROTIER_DEFAULTS.copy()
    zerotier.update(default_zerotier)
    zerotier.update(host_cfg.get("zerotier", {}) or {})
    zerotier["enabled"] = parse_bool(zerotier.get("enabled", True))
    zerotier["manage_secret"] = parse_bool(zerotier.get("manage_secret", False))
    zerotier["network_id"] = str(zerotier.get("network_id", "") or "").strip()
    zerotier["secret"] = str(zerotier.get("secret", "") or "").strip()

    if normalized_features["feature_openwrt_zerotier"] and zerotier["enabled"] and not zerotier["network_id"]:
        fail(f"Host '{alias}' requires zerotier.network_id when zerotier role is enabled.")
    if zerotier["manage_secret"] and not zerotier["secret"]:
        fail(f"Host '{alias}' has zerotier.manage_secret=true but zerotier.secret is empty.")

    default_wan = defaults.get("wan", {}) or {}
    if not isinstance(default_wan, dict):
        fail("defaults.wan must be an object when provided.")
    host_wan = host_cfg.get("wan", {}) or {}
    if not isinstance(host_wan, dict):
        fail(f"Host '{alias}' wan must be an object.")

    wan = WAN_DEFAULTS.copy()
    wan.update(default_wan)
    wan.update(host_wan)

    wan["enabled"] = parse_bool(wan.get("enabled", True))
    wan["proto"] = str(wan.get("proto", "dhcp") or "dhcp").strip().lower()
    wan["device"] = str(wan.get("device", "eth0") or "eth0").strip()
    wan["ipaddr"] = str(wan.get("ipaddr", "") or "").strip()
    wan["netmask"] = str(wan.get("netmask", "") or "").strip()
    wan["gateway"] = str(wan.get("gateway", "") or "").strip()
    wan["pppoe_username"] = str(wan.get("pppoe_username", "") or "").strip()
    wan["pppoe_password"] = str(wan.get("pppoe_password", "") or "")
    wan["pppoe_ipv6"] = str(wan.get("pppoe_ipv6", "auto") or "auto").strip().lower()

    if not wan["device"]:
        fail(f"Host '{alias}' wan.device must be non-empty string.")

    if wan["proto"] not in {"dhcp", "static", "pppoe"}:
        fail(f"Host '{alias}' wan.proto must be one of: dhcp, static, pppoe.")

    dns = wan.get("dns", [])
    if dns is None:
        dns = []
    if not isinstance(dns, list):
        fail(f"Host '{alias}' wan.dns must be a list.")
    normalized_dns = []
    for dns_value in dns:
        dns_entry = str(dns_value or "").strip()
        if dns_entry:
            normalized_dns.append(dns_entry)
    wan["dns"] = normalized_dns

    if wan["proto"] == "static":
        if not wan["ipaddr"]:
            fail(f"Host '{alias}' wan.ipaddr is required when wan.proto=static.")
        if not wan["netmask"]:
            fail(f"Host '{alias}' wan.netmask is required when wan.proto=static.")

    if wan["proto"] == "pppoe":
        if not wan["pppoe_username"]:
            fail(f"Host '{alias}' wan.pppoe_username is required when wan.proto=pppoe.")
        if not wan["pppoe_password"]:
            fail(f"Host '{alias}' wan.pppoe_password is required when wan.proto=pppoe.")
        if wan["pppoe_ipv6"] not in {"auto", "0", "1"}:
            fail(f"Host '{alias}' wan.pppoe_ipv6 must be one of: auto, 0, 1.")

    default_monitoring = defaults.get("monitoring", {}) or {}
    if not isinstance(default_monitoring, dict):
        fail("defaults.monitoring must be an object when provided.")

    monitoring = MONITORING_DEFAULTS.copy()
    monitoring.update(default_monitoring)
    monitoring.update(host_cfg.get("monitoring", {}) or {})
    monitoring["openwrt_monitoring_enabled"] = parse_bool(
        monitoring.get("openwrt_monitoring_enabled", True)
    )
    monitoring["openwrt_node_exporter_port"] = parse_int(
        monitoring.get("openwrt_node_exporter_port", 9100),
        f"hosts.{alias}.monitoring.openwrt_node_exporter_port",
        1,
        65535,
    )
    monitoring["openwrt_probe_interval_minutes"] = parse_int(
        monitoring.get("openwrt_probe_interval_minutes", 1),
        f"hosts.{alias}.monitoring.openwrt_probe_interval_minutes",
        1,
        60,
    )

    default_docker = defaults.get("docker", {}) or {}
    if not isinstance(default_docker, dict):
        fail("defaults.docker must be an object when provided.")

    docker = DOCKER_DEFAULTS.copy()
    docker.update(default_docker)
    docker.update(host_cfg.get("docker", {}) or {})

    docker["manage_runtime"] = parse_bool(docker.get("manage_runtime", True))
    docker["compose_command"] = str(docker.get("compose_command", "docker-compose") or "docker-compose").strip()
    if not docker["compose_command"]:
        fail(f"Host '{alias}' docker.compose_command must be non-empty string.")

    stacks = docker.get("stacks", [])
    if stacks is None:
        stacks = []
    if not isinstance(stacks, list):
        fail(f"Host '{alias}' docker.stacks must be a list.")
    for stack in stacks:
        if not isinstance(stack, dict):
            fail(f"Host '{alias}' docker.stacks entries must be objects.")
        name = str(stack.get("name", "") or "").strip()
        compose = str(stack.get("compose_content", "") or "").strip()
        if not name:
            fail(f"Host '{alias}' docker stack entry has empty name.")
        if not compose:
            fail(f"Host '{alias}' docker stack '{name}' requires compose_content.")
    docker["stacks"] = stacks

    custom_roles = host_cfg.get("custom_roles", defaults.get("custom_roles", []))
    if custom_roles is None:
        custom_roles = []
    if not isinstance(custom_roles, list):
        fail(f"Host '{alias}' custom_roles must be a list.")
    for role_name in custom_roles:
        if not isinstance(role_name, str) or not role_name.strip():
            fail(f"Host '{alias}' custom_roles contains invalid item: {role_name!r}")

    return {
        "ansible_host": selected_host,
        "ansible_port": selected_port,
        "ansible_ssh_private_key_file": ssh_private_key_file,
        "selected_access": {
            "kind": selected_kind,
            "host": selected_host,
            "port": selected_port,
            "proxy_jump": selected_proxy_jump,
            "proxy_jumps": proxy_jumps,
        },
        "access": access,
        "deploy_user": deploy_user,
        "bootstrap": {
            "username": bootstrap_username,
            "password": bootstrap_password,
        },
        "features": normalized_features,
        "wan": wan,
        "zerotier": zerotier,
        "passwall2": passwall2,
        "monitoring": monitoring,
        "docker": docker,
        "custom_roles": custom_roles,
    }


def shell_escape_single_quotes(value: str) -> str:
    return value.replace("'", "'\"'\"'")


def build_inventory(hosts: dict, mode: str) -> str:
    lines = ["[all]"]
    for alias, cfg in hosts.items():
        ansible_user = cfg["bootstrap"]["username"] if mode == "bootstrap" else cfg["deploy_user"]
        line = (
            f"{alias} "
            f"ansible_host={cfg['ansible_host']} "
            f"ansible_port={cfg['ansible_port']} "
            f"ansible_user={ansible_user} "
            f"ansible_ssh_private_key_file={cfg['ansible_ssh_private_key_file']}"
        )
        proxy_jump = cfg["selected_access"]["proxy_jump"]
        if proxy_jump:
            escaped = shell_escape_single_quotes(f"-o ProxyJump={proxy_jump}")
            line += f" ansible_ssh_common_args='{escaped}'"
        lines.append(line)
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render OpenWrt runtime inventory and vars from fleet config.")
    parser.add_argument("--fleet-config", required=True, help="Path to decoded OpenWrt fleet config (JSON or YAML).")
    parser.add_argument("--mode", required=True, choices=["bootstrap", "deploy", "lockdown"])
    parser.add_argument("--inventory-out", required=True)
    parser.add_argument("--vars-out", required=True)
    parser.add_argument("--bootstrap-out", required=True)
    parser.add_argument("--skip-connectivity-check", action="store_true")
    parser.add_argument("--probe-timeout", type=float, default=1.5)
    args = parser.parse_args()

    hosts_raw, defaults = load_fleet_config(Path(args.fleet_config))

    normalized_hosts = {
        alias: normalize_host(
            alias,
            cfg,
            defaults,
            args.mode,
            check_connectivity=not args.skip_connectivity_check,
            timeout_sec=args.probe_timeout,
        )
        for alias, cfg in hosts_raw.items()
    }

    runtime_vars = {
        "fleet_mode": args.mode,
        "fleet_hosts": normalized_hosts,
        "openwrt_fleet_hosts": normalized_hosts,
        "openwrt_runtime_host_vars": {
            alias: {
                "passwall2_subscribe_url": cfg["passwall2"]["subscribe_url"],
                "passwall2_probe_url": cfg["passwall2"]["probe_url"],
                "passwall2_socks_port": cfg["passwall2"]["socks_port"],
                "openwrt_node_exporter_port": cfg["monitoring"]["openwrt_node_exporter_port"],
                "openwrt_probe_interval_minutes": cfg["monitoring"]["openwrt_probe_interval_minutes"],
                "openwrt_wan_enabled": cfg["wan"]["enabled"],
                "openwrt_wan_proto": cfg["wan"]["proto"],
                "openwrt_wan_device": cfg["wan"]["device"],
                "openwrt_wan_ipaddr": cfg["wan"]["ipaddr"],
                "openwrt_wan_netmask": cfg["wan"]["netmask"],
                "openwrt_wan_gateway": cfg["wan"]["gateway"],
                "openwrt_wan_dns": cfg["wan"]["dns"],
                "openwrt_wan_pppoe_username": cfg["wan"]["pppoe_username"],
                "openwrt_wan_pppoe_password": cfg["wan"]["pppoe_password"],
                "openwrt_wan_pppoe_ipv6": cfg["wan"]["pppoe_ipv6"],
                "zerotier_network_id": cfg["zerotier"]["network_id"],
            }
            for alias, cfg in normalized_hosts.items()
        },
    }

    bootstrap_map = {
        alias: {
            "ansible_host": cfg["ansible_host"],
            "ansible_port": cfg["ansible_port"],
            "proxy_jump": cfg["selected_access"]["proxy_jump"],
            "proxy_jumps": cfg["selected_access"]["proxy_jumps"],
            "selected_access": cfg["selected_access"]["kind"],
            "bootstrap_username": cfg["bootstrap"]["username"],
            "bootstrap_password": cfg["bootstrap"]["password"],
            "deploy_user": cfg["deploy_user"],
        }
        for alias, cfg in normalized_hosts.items()
    }

    Path(args.inventory_out).write_text(build_inventory(normalized_hosts, args.mode), encoding="utf-8")
    Path(args.vars_out).write_text(json.dumps(runtime_vars, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    Path(args.bootstrap_out).write_text(json.dumps(bootstrap_map, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
