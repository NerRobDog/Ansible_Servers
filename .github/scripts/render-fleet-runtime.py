#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path
from typing import Optional

try:
    import yaml
except Exception:  # pragma: no cover - optional dependency check
    yaml = None


FEATURE_DEFAULTS = {
    "feature_base": True,
    "feature_firewall": True,
    "feature_docker": True,
    "feature_tailscale": False,
    "feature_remnawave_node": False,
    "feature_caddy_node": False,
    "feature_node_tuning": False,
    "feature_monitoring_agent": False,
    "feature_monitoring_stack": False,
    "feature_user_shell": False,
    "feature_sing_box_proxy": False,
}

REMNAWAVE_DEFAULTS = {
    "node_secret_key": "",
    "node_port": 3001,
    "caddy_domain": "",
    "caddy_monitor_port": 8443,
    "ipv6_state": "enabled",
    "caddy_tls_mode": "public",
    "caddy_tls_cert_file": "",
    "caddy_tls_key_file": "",
    "caddy_local_only": True,
    "caddy_acme_ca": "",
    "panel_node_uuid": "",
    "target_profile_name": "",
    "target_inbound_tags": [],
}

YUSIC_WORKER_DEFAULTS = {
    "relay_host_alias": "",
    "image_repo": "",
    "enabled": True,
    "arch": "amd64",
    "ssh": {
        "host": "",
        "port": 22,
        "user": "root",
        "password": "",
        "private_key": "",
    },
    "tags": [],
    "max_concurrent_jobs": 1,
    "network_mode": "host",
    "dns": [],
    "proxy": {
        "http_proxy": "",
        "https_proxy": "",
        "no_proxy": "",
    },
    "redis_url": "",
    "cache_bot_token": "",
    "inline_cache_chat_id": "",
    "workdir": "/opt/yusic-worker",
    "container_name": "yusic_download_worker",
    "selfcheck_command": "python services/download-worker/selfcheck.py",
}

WORKER_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
WORKER_CONTAINER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
WORKER_WORKDIR_RE = re.compile(r"^/[A-Za-z0-9._/\-]+$")

MONITORING_DEFAULTS = {
    "agent_bind_address": "0.0.0.0",
    "agent_node_exporter_port": 9100,
    "agent_cadvisor_port": 8080,
    "stack_retention_days": 7,
    "stack_grafana_admin_user": "admin",
    "stack_grafana_admin_password": "change_me",
}

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


def parse_bool_soft(value, default: bool) -> bool:
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
    return default


def ensure_mapping(value, context: str):
    if value is None:
        return {}
    if not isinstance(value, dict):
        fail(f"{context} must be an object.")
    return value


def parse_int(value, context: str, min_value: Optional[int] = None, max_value: Optional[int] = None) -> int:
    try:
        parsed = int(value)
    except Exception:
        fail(f"{context} must be an integer: {value!r}")
    if min_value is not None and parsed < min_value:
        fail(f"{context} must be >= {min_value}.")
    if max_value is not None and parsed > max_value:
        fail(f"{context} must be <= {max_value}.")
    return parsed


def parse_string_list(value, context: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        fail(f"{context} must be a list.")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            fail(f"{context} contains invalid item: {item!r}")
        result.append(item.strip())
    return result


def deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_fleet_config(path: Path, target: str):
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        fail("Fleet config file is empty.")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        if yaml is None:
            fail("Fleet config is not valid JSON and PyYAML is unavailable for YAML parsing.")
        try:
            data = yaml.safe_load(raw)
        except Exception as exc:  # pragma: no cover
            fail(f"Unable to parse fleet config as YAML: {exc}")

    if not isinstance(data, dict):
        fail("Fleet config root must be an object.")
    if "hosts" not in data or not isinstance(data["hosts"], dict) or not data["hosts"]:
        fail("Fleet config must contain non-empty object field 'hosts'.")

    defaults = ensure_mapping(data.get("defaults", {}), "Field 'defaults'")
    workers_raw = data.get("workers", {})
    if workers_raw is None:
        workers_raw = {}
    if not isinstance(workers_raw, dict):
        if target == "yusic_worker":
            fail("Field 'workers' must be an object.")
        workers_raw = {}

    workers = workers_raw
    return data["hosts"], defaults, workers


def normalize_host(alias: str, host_cfg: dict, defaults: dict):
    if not isinstance(host_cfg, dict):
        fail(f"Host '{alias}' config must be an object.")

    ansible_host = host_cfg.get("ansible_host")
    if not isinstance(ansible_host, str) or not ansible_host.strip():
        fail(f"Host '{alias}' requires non-empty string 'ansible_host'.")

    ansible_port = parse_int(host_cfg.get("ansible_port", defaults.get("ansible_port", 22)), f"Host '{alias}' ansible_port", 1, 65535)

    deploy_user = host_cfg.get("deploy_user", defaults.get("deploy_user", "deploy"))
    if not isinstance(deploy_user, str) or not deploy_user.strip():
        fail(f"Host '{alias}' requires string deploy_user.")

    default_bootstrap = ensure_mapping(defaults.get("bootstrap", {}), "defaults.bootstrap")
    bootstrap = ensure_mapping(host_cfg.get("bootstrap", {}), f"Host '{alias}' bootstrap")
    bootstrap_username = bootstrap.get("username", default_bootstrap.get("username", "root"))
    bootstrap_password = bootstrap.get("password", default_bootstrap.get("password", ""))
    if bootstrap_password is None:
        bootstrap_password = ""

    if not isinstance(bootstrap_username, str) or not bootstrap_username.strip():
        fail(f"Host '{alias}' bootstrap.username must be a non-empty string.")
    if not isinstance(bootstrap_password, str):
        fail(f"Host '{alias}' bootstrap.password must be a string when provided.")

    default_features = ensure_mapping(defaults.get("features", {}), "defaults.features")
    features = FEATURE_DEFAULTS.copy()
    features.update(default_features)
    features.update(ensure_mapping(host_cfg.get("features", {}), f"Host '{alias}' features"))
    normalized_features = {}
    for key in FEATURE_DEFAULTS:
        normalized_features[key] = parse_bool(features.get(key, FEATURE_DEFAULTS[key]))

    default_remnawave = ensure_mapping(defaults.get("remnawave", {}), "defaults.remnawave")
    remnawave_cfg = REMNAWAVE_DEFAULTS.copy()
    remnawave_cfg.update(default_remnawave)
    remnawave_cfg.update(ensure_mapping(host_cfg.get("remnawave", {}), f"Host '{alias}' remnawave"))
    remnawave_cfg["node_port"] = parse_int(remnawave_cfg["node_port"], f"Host '{alias}' remnawave.node_port", 1, 65535)
    remnawave_cfg["caddy_monitor_port"] = parse_int(
        remnawave_cfg["caddy_monitor_port"], f"Host '{alias}' remnawave.caddy_monitor_port", 1, 65535
    )
    if remnawave_cfg["ipv6_state"] not in {"enabled", "disabled"}:
        fail(f"Host '{alias}' remnawave.ipv6_state must be enabled|disabled.")
    remnawave_cfg["caddy_tls_mode"] = str(remnawave_cfg.get("caddy_tls_mode", "public")).strip().lower()
    if remnawave_cfg["caddy_tls_mode"] not in {"public", "internal", "files"}:
        fail(f"Host '{alias}' remnawave.caddy_tls_mode must be public|internal|files.")
    remnawave_cfg["caddy_local_only"] = parse_bool(remnawave_cfg.get("caddy_local_only", True))
    remnawave_cfg["caddy_tls_cert_file"] = str(remnawave_cfg.get("caddy_tls_cert_file", "") or "")
    remnawave_cfg["caddy_tls_key_file"] = str(remnawave_cfg.get("caddy_tls_key_file", "") or "")
    remnawave_cfg["caddy_acme_ca"] = str(remnawave_cfg.get("caddy_acme_ca", "") or "")
    remnawave_cfg["panel_node_uuid"] = str(remnawave_cfg.get("panel_node_uuid", "") or "").strip()
    remnawave_cfg["target_profile_name"] = str(remnawave_cfg.get("target_profile_name", "") or "").strip()

    remnawave_cfg["target_inbound_tags"] = parse_string_list(remnawave_cfg.get("target_inbound_tags", []), f"Host '{alias}' remnawave.target_inbound_tags")
    if remnawave_cfg["caddy_tls_mode"] == "files":
        if not remnawave_cfg["caddy_tls_cert_file"].strip() or not remnawave_cfg["caddy_tls_key_file"].strip():
            fail(f"Host '{alias}' remnawave.caddy_tls_mode=files requires caddy_tls_cert_file and caddy_tls_key_file.")

    default_monitoring = defaults.get("monitoring", {})
    if default_monitoring is None:
        default_monitoring = {}
    if not isinstance(default_monitoring, dict):
        fail("defaults.monitoring must be an object when provided.")
    monitoring_cfg = MONITORING_DEFAULTS.copy()
    monitoring_cfg.update(default_monitoring)
    monitoring_cfg.update(host_cfg.get("monitoring", {}) or {})
    try:
        monitoring_cfg["agent_node_exporter_port"] = int(monitoring_cfg["agent_node_exporter_port"])
        monitoring_cfg["agent_cadvisor_port"] = int(monitoring_cfg["agent_cadvisor_port"])
        monitoring_cfg["stack_retention_days"] = int(monitoring_cfg["stack_retention_days"])
    except Exception:
        fail(f"Host '{alias}' monitoring ports/retention must be numbers.")
    for key in ("agent_node_exporter_port", "agent_cadvisor_port"):
        if not (1 <= monitoring_cfg[key] <= 65535):
            fail(f"Host '{alias}' monitoring.{key} must be in range 1..65535.")
    if monitoring_cfg["stack_retention_days"] < 1:
        fail(f"Host '{alias}' monitoring.stack_retention_days must be >= 1.")
    monitoring_cfg["agent_bind_address"] = str(monitoring_cfg.get("agent_bind_address", "0.0.0.0") or "0.0.0.0")
    monitoring_cfg["stack_grafana_admin_user"] = str(
        monitoring_cfg.get("stack_grafana_admin_user", "admin") or "admin"
    )
    monitoring_cfg["stack_grafana_admin_password"] = str(
        monitoring_cfg.get("stack_grafana_admin_password", "change_me") or "change_me"
    )

    custom_roles = host_cfg.get("custom_roles", defaults.get("custom_roles", []))
    if custom_roles is None:
        custom_roles = []
    if not isinstance(custom_roles, list):
        fail(f"Host '{alias}' custom_roles must be a list.")
    for role_name in custom_roles:
        if not isinstance(role_name, str) or not role_name.strip():
            fail(f"Host '{alias}' custom_roles contains invalid item: {role_name!r}")

    sing_box_cfg = normalize_sing_box_proxy(
        alias, host_cfg, defaults, normalized_features.get("feature_sing_box_proxy", False)
    )

    return {
        "ansible_host": ansible_host.strip(),
        "ansible_port": ansible_port,
        "deploy_user": deploy_user.strip(),
        "bootstrap": {
            "username": bootstrap_username.strip(),
            "password": bootstrap_password,
        },
        "features": normalized_features,
        "remnawave": remnawave_cfg,
        "monitoring": monitoring_cfg,
        "sing_box_proxy": sing_box_cfg,
        "custom_roles": custom_roles,
    }


def normalize_sing_box_proxy(alias: str, host_cfg: dict, defaults: dict, feature_enabled: bool) -> dict:
    default_cfg = ensure_mapping(defaults.get("sing_box_proxy", {}), "defaults.sing_box_proxy")
    host_specific = ensure_mapping(host_cfg.get("sing_box_proxy", {}), f"Host '{alias}' sing_box_proxy")
    merged = deep_merge(default_cfg, host_specific)

    outbounds_raw = merged.get("outbounds", [])
    if not isinstance(outbounds_raw, list):
        fail(f"Host '{alias}' sing_box_proxy.outbounds must be a list.")
    outbounds = []
    for index, ob in enumerate(outbounds_raw):
        if not isinstance(ob, dict):
            fail(f"Host '{alias}' sing_box_proxy.outbounds[{index}] must be a mapping.")
        ob_tag = str(ob.get("tag", "") or "").strip()
        if not ob_tag:
            fail(f"Host '{alias}' sing_box_proxy.outbounds[{index}].tag must be non-empty.")
        ob_type = str(ob.get("type", "") or "").strip()
        if not ob_type:
            fail(f"Host '{alias}' sing_box_proxy.outbounds[{index}] (tag={ob_tag}) must define type.")
        # Persist trimmed tag/type back into the outbound so downstream
        # consumers (template, route_final lookup, jinja map(attribute='tag'))
        # see whitespace-free identifiers.
        normalized_ob = copy.deepcopy(ob)
        normalized_ob["tag"] = ob_tag
        normalized_ob["type"] = ob_type
        outbounds.append(normalized_ob)

    route_final = str(merged.get("route_final", "") or "").strip()
    log_level = str(merged.get("log_level", "info") or "").strip().lower()
    if log_level not in {"trace", "debug", "info", "warn", "error", "fatal", "panic"}:
        fail(f"Host '{alias}' sing_box_proxy.log_level invalid: {log_level!r}")

    if feature_enabled:
        if len(outbounds) == 0:
            fail(
                f"Host '{alias}' has feature_sing_box_proxy=true but sing_box_proxy.outbounds is empty. "
                "Define at least one outbound (typically a VLESS REALITY entry to a non-RU node)."
            )
        outbound_tags = {ob["tag"] for ob in outbounds}
        if route_final and route_final not in outbound_tags and route_final != "auto" and route_final != "direct":
            fail(
                f"Host '{alias}' sing_box_proxy.route_final='{route_final}' references no outbound. "
                f"Available outbound tags: {sorted(outbound_tags)}"
            )

    return {
        "outbounds": outbounds,
        "route_final": route_final,
        "log_level": log_level,
    }


def normalize_yusic_defaults(defaults: dict) -> dict:
    worker_defaults = ensure_mapping(defaults.get("yusic_worker", {}), "defaults.yusic_worker")
    merged = deep_merge(YUSIC_WORKER_DEFAULTS, worker_defaults)
    merged["relay_host_alias"] = str(merged.get("relay_host_alias", "") or "").strip()
    merged["image_repo"] = str(merged.get("image_repo", "") or "").strip()
    merged["enabled"] = parse_bool(merged.get("enabled", True))
    merged["arch"] = str(merged.get("arch", "amd64") or "").strip().lower()
    if merged["arch"] not in {"amd64", "arm64"}:
        fail("defaults.yusic_worker.arch must be one of: amd64, arm64.")
    merged["ssh"] = ensure_mapping(merged.get("ssh", {}), "defaults.yusic_worker.ssh")
    merged["ssh"]["host"] = str(merged["ssh"].get("host", "") or "").strip()
    merged["ssh"]["port"] = parse_int(merged["ssh"].get("port", 22), "defaults.yusic_worker.ssh.port", 1, 65535)
    merged["ssh"]["user"] = str(merged["ssh"].get("user", "root") or "").strip()
    merged["ssh"]["password"] = str(merged["ssh"].get("password", "") or "")
    merged["ssh"]["private_key"] = str(merged["ssh"].get("private_key", "") or "")
    merged["tags"] = parse_string_list(merged.get("tags", []), "defaults.yusic_worker.tags")
    merged["max_concurrent_jobs"] = parse_int(
        merged.get("max_concurrent_jobs", 1), "defaults.yusic_worker.max_concurrent_jobs", 1, 128
    )
    merged["network_mode"] = str(merged.get("network_mode", "host") or "").strip()
    merged["dns"] = parse_string_list(merged.get("dns", []), "defaults.yusic_worker.dns")
    merged["proxy"] = ensure_mapping(merged.get("proxy", {}), "defaults.yusic_worker.proxy")
    merged["proxy"]["http_proxy"] = str(merged["proxy"].get("http_proxy", "") or "")
    merged["proxy"]["https_proxy"] = str(merged["proxy"].get("https_proxy", "") or "")
    merged["proxy"]["no_proxy"] = str(merged["proxy"].get("no_proxy", "") or "")
    merged["redis_url"] = str(merged.get("redis_url", "") or "").strip()
    merged["cache_bot_token"] = str(merged.get("cache_bot_token", "") or "").strip()
    merged["inline_cache_chat_id"] = str(merged.get("inline_cache_chat_id", "") or "").strip()
    merged["workdir"] = str(merged.get("workdir", "/opt/yusic-worker") or "").strip()
    merged["container_name"] = str(merged.get("container_name", "yusic_download_worker") or "").strip()
    merged["selfcheck_command"] = str(merged.get("selfcheck_command", "python services/download-worker/selfcheck.py") or "").strip()
    return merged


def normalize_worker(alias: str, worker_cfg: dict, worker_defaults: dict, host_aliases: set[str], strict_required: bool) -> dict:
    if not isinstance(worker_cfg, dict):
        fail(f"Worker '{alias}' config must be an object.")
    if not WORKER_ALIAS_RE.match(alias) or ".." in alias:
        fail(
            f"Worker alias '{alias}' is invalid. Allowed: [A-Za-z0-9_.-], "
            "must start with alnum, max 64 chars, no '..'."
        )
    merged = deep_merge(worker_defaults, worker_cfg)
    merged["enabled"] = parse_bool(merged.get("enabled", True))
    merged["relay_host_alias"] = str(merged.get("relay_host_alias", "") or "").strip()
    merged["image_repo"] = str(merged.get("image_repo", "") or "").strip()
    merged["arch"] = str(merged.get("arch", "amd64") or "").strip().lower()
    if merged["arch"] not in {"amd64", "arm64"}:
        fail(f"Worker '{alias}' arch must be one of: amd64, arm64.")

    ssh_cfg = ensure_mapping(merged.get("ssh", {}), f"Worker '{alias}' ssh")
    merged["ssh"] = {
        "host": str(ssh_cfg.get("host", "") or "").strip(),
        "port": parse_int(ssh_cfg.get("port", 22), f"Worker '{alias}' ssh.port", 1, 65535),
        "user": str(ssh_cfg.get("user", "root") or "").strip(),
        "password": str(ssh_cfg.get("password", "") or ""),
        "private_key": str(ssh_cfg.get("private_key", "") or ""),
    }
    merged["tags"] = parse_string_list(merged.get("tags", []), f"Worker '{alias}' tags")
    merged["max_concurrent_jobs"] = parse_int(merged.get("max_concurrent_jobs", 1), f"Worker '{alias}' max_concurrent_jobs", 1, 128)
    merged["network_mode"] = str(merged.get("network_mode", "host") or "").strip()
    merged["dns"] = parse_string_list(merged.get("dns", []), f"Worker '{alias}' dns")
    proxy = ensure_mapping(merged.get("proxy", {}), f"Worker '{alias}' proxy")
    merged["proxy"] = {
        "http_proxy": str(proxy.get("http_proxy", "") or ""),
        "https_proxy": str(proxy.get("https_proxy", "") or ""),
        "no_proxy": str(proxy.get("no_proxy", "") or ""),
    }
    merged["redis_url"] = str(merged.get("redis_url", "") or "").strip()
    merged["cache_bot_token"] = str(merged.get("cache_bot_token", "") or "").strip()
    merged["inline_cache_chat_id"] = str(merged.get("inline_cache_chat_id", "") or "").strip()
    merged["workdir"] = str(merged.get("workdir", "/opt/yusic-worker") or "").strip()
    merged["container_name"] = str(merged.get("container_name", "yusic_download_worker") or "").strip()
    merged["selfcheck_command"] = str(merged.get("selfcheck_command", "python services/download-worker/selfcheck.py") or "").strip()
    merged["alias"] = alias
    if not WORKER_WORKDIR_RE.match(merged["workdir"]) or ".." in merged["workdir"]:
        fail(
            f"Worker '{alias}' workdir is invalid. "
            "Use absolute POSIX path with [A-Za-z0-9._/-] and without '..'."
        )
    if not WORKER_CONTAINER_NAME_RE.match(merged["container_name"]):
        fail(
            f"Worker '{alias}' container_name is invalid. "
            "Allowed: [A-Za-z0-9_.-], must start with alnum, max 128 chars."
        )

    if merged["enabled"] and strict_required:
        if not merged["relay_host_alias"]:
            fail(f"Worker '{alias}' must define relay_host_alias (or defaults.yusic_worker.relay_host_alias).")
        if merged["relay_host_alias"] not in host_aliases:
            fail(f"Worker '{alias}' relay_host_alias '{merged['relay_host_alias']}' is not present in fleet hosts.")
        if not merged["image_repo"]:
            fail(f"Worker '{alias}' must define image_repo.")
        if not merged["ssh"]["host"]:
            fail(f"Worker '{alias}' must define ssh.host.")
        if not merged["ssh"]["user"]:
            fail(f"Worker '{alias}' must define ssh.user.")
        if not merged["redis_url"]:
            fail(f"Worker '{alias}' must define redis_url.")
        if not merged["cache_bot_token"]:
            fail(f"Worker '{alias}' must define cache_bot_token.")
        if not merged["inline_cache_chat_id"]:
            fail(f"Worker '{alias}' must define inline_cache_chat_id.")
    return merged


def normalize_workers(workers_raw: dict, defaults: dict, host_aliases: set[str], target: str):
    if target != "yusic_worker":
        defaults_raw = defaults.get("yusic_worker", {})
        worker_defaults = deep_merge(YUSIC_WORKER_DEFAULTS, defaults_raw if isinstance(defaults_raw, dict) else {})
        relay_host_alias = str(worker_defaults.get("relay_host_alias", "") or "").strip()
        normalized = {}
        for alias, cfg in workers_raw.items():
            if not isinstance(cfg, dict):
                continue
            merged = deep_merge(worker_defaults, cfg)
            merged["alias"] = alias
            merged["enabled"] = parse_bool_soft(merged.get("enabled", True), True)
            normalized[alias] = merged

        enabled_aliases = sorted(alias for alias, cfg in normalized.items() if cfg.get("enabled", False))
        if not relay_host_alias and enabled_aliases:
            relay_host_alias = str(normalized[enabled_aliases[0]].get("relay_host_alias", "") or "").strip()

        return {
            "defaults": worker_defaults,
            "workers": normalized,
            "enabled_workers": enabled_aliases,
            "relay_host_alias": relay_host_alias,
        }

    strict_required = target == "yusic_worker"
    worker_defaults = normalize_yusic_defaults(defaults)
    normalized = {
        alias: normalize_worker(alias, cfg, worker_defaults, host_aliases, strict_required)
        for alias, cfg in workers_raw.items()
    }
    enabled_aliases = sorted(alias for alias, cfg in normalized.items() if cfg.get("enabled", False))
    if strict_required and not enabled_aliases:
        fail("target=yusic_worker requires at least one enabled worker in 'workers'.")

    relay_host_alias = worker_defaults.get("relay_host_alias", "")
    if strict_required and enabled_aliases:
        if not relay_host_alias:
            relay_host_alias = normalized[enabled_aliases[0]]["relay_host_alias"]
        if relay_host_alias not in host_aliases:
            fail(f"Global relay_host_alias '{relay_host_alias}' is not present in fleet hosts.")
        for alias in enabled_aliases:
            if normalized[alias]["relay_host_alias"] != relay_host_alias:
                fail(
                    f"Worker '{alias}' relay_host_alias='{normalized[alias]['relay_host_alias']}' differs from global relay_host_alias='{relay_host_alias}'. "
                    "Current contract supports one global relay host alias."
                )

    return {
        "defaults": worker_defaults,
        "workers": normalized,
        "enabled_workers": enabled_aliases,
        "relay_host_alias": relay_host_alias,
    }


def build_inventory(hosts: dict, mode: str) -> str:
    lines = ["[all]"]
    for alias, cfg in hosts.items():
        ansible_user = cfg["bootstrap"]["username"] if mode == "bootstrap" else cfg["deploy_user"]
        lines.append(
            f"{alias} "
            f"ansible_host={cfg['ansible_host']} "
            f"ansible_port={cfg['ansible_port']} "
            f"ansible_user={ansible_user} "
            "ansible_ssh_private_key_file=~/.ssh/id_ed25519"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render runtime inventory and vars from fleet config.")
    parser.add_argument("--fleet-config", required=True, help="Path to decoded fleet config (JSON or YAML).")
    parser.add_argument("--mode", required=True, choices=["bootstrap", "deploy", "lockdown"])
    parser.add_argument("--target", required=False, default="remnawave", choices=["remnawave", "yusic_worker"])
    parser.add_argument("--inventory-out", required=True)
    parser.add_argument("--vars-out", required=True)
    parser.add_argument("--bootstrap-out", required=True)
    args = parser.parse_args()

    hosts_raw, defaults, workers_raw = load_fleet_config(Path(args.fleet_config), args.target)
    normalized_hosts = {alias: normalize_host(alias, cfg, defaults) for alias, cfg in hosts_raw.items()}
    yusic_runtime = normalize_workers(workers_raw, defaults, set(normalized_hosts.keys()), args.target)

    runtime_vars = {
        "fleet_mode": args.mode,
        "fleet_target": args.target,
        "fleet_hosts": normalized_hosts,
        "remnawave_runtime_host_vars": {
            alias: {
                "remnawave_node_secret_key": cfg["remnawave"]["node_secret_key"],
                "remnawave_node_port": cfg["remnawave"]["node_port"],
                "remnawave_caddy_domain": cfg["remnawave"]["caddy_domain"],
                "remnawave_caddy_monitor_port": cfg["remnawave"]["caddy_monitor_port"],
                "remnawave_ipv6_state": cfg["remnawave"]["ipv6_state"],
                "remnawave_caddy_tls_mode": cfg["remnawave"]["caddy_tls_mode"],
                "remnawave_caddy_tls_cert_file": cfg["remnawave"]["caddy_tls_cert_file"],
                "remnawave_caddy_tls_key_file": cfg["remnawave"]["caddy_tls_key_file"],
                "remnawave_caddy_local_only": cfg["remnawave"]["caddy_local_only"],
                "remnawave_caddy_acme_ca": cfg["remnawave"]["caddy_acme_ca"],
                "remnawave_panel_node_uuid": cfg["remnawave"]["panel_node_uuid"],
                "remnawave_target_profile_name": cfg["remnawave"]["target_profile_name"],
                "remnawave_target_inbound_tags": cfg["remnawave"]["target_inbound_tags"],
                "monitoring_agent_bind_address": cfg["monitoring"]["agent_bind_address"],
                "monitoring_agent_node_exporter_port": cfg["monitoring"]["agent_node_exporter_port"],
                "monitoring_agent_cadvisor_port": cfg["monitoring"]["agent_cadvisor_port"],
                "monitoring_stack_retention_days": cfg["monitoring"]["stack_retention_days"],
                "monitoring_stack_grafana_admin_user": cfg["monitoring"]["stack_grafana_admin_user"],
                "monitoring_stack_grafana_admin_password": cfg["monitoring"]["stack_grafana_admin_password"],
            }
            for alias, cfg in normalized_hosts.items()
        },
        "yusic_worker_runtime": yusic_runtime,
    }

    bootstrap_map = {
        alias: {
            "ansible_host": cfg["ansible_host"],
            "ansible_port": cfg["ansible_port"],
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
