#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

try:
    import yaml
except Exception:  # pragma: no cover - optional dependency check
    yaml = None


FEATURE_DEFAULTS = {
    "feature_base": True,
    "feature_firewall": True,
    "feature_docker": True,
    "feature_remnawave_node": False,
    "feature_caddy_node": False,
    "feature_node_tuning": False,
    "feature_monitoring_agent": False,
    "feature_monitoring_stack": False,
    "feature_user_shell": False,
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

MONITORING_DEFAULTS = {
    "agent_bind_address": "0.0.0.0",
    "agent_node_exporter_port": 9100,
    "agent_cadvisor_port": 8080,
    "agent_promtail_enabled": True,
    "agent_acl_enabled": False,
    "agent_acl_allowed_sources": [],
    "loki_push_url": "",
    "labels": {
        "country": "",
        "role": "node",
    },
    "stack_retention_days": 7,
    "stack_bind_address": "127.0.0.1",
    "stack_loki_port": 3100,
    "stack_loki_ingest_bind_address": "0.0.0.0",
    "stack_loki_ingest_allowed_sources": [],
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


def load_fleet_config(path: Path):
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

    defaults = data.get("defaults", {})
    if defaults is None:
        defaults = {}
    if not isinstance(defaults, dict):
        fail("Field 'defaults' must be an object when provided.")

    return data["hosts"], defaults


def normalize_host(alias: str, host_cfg: dict, defaults: dict):
    if not isinstance(host_cfg, dict):
        fail(f"Host '{alias}' config must be an object.")

    ansible_host = host_cfg.get("ansible_host")
    if not isinstance(ansible_host, str) or not ansible_host.strip():
        fail(f"Host '{alias}' requires non-empty string 'ansible_host'.")

    ansible_port = host_cfg.get("ansible_port", defaults.get("ansible_port", 22))
    try:
        ansible_port = int(ansible_port)
    except Exception:
        fail(f"Host '{alias}' has invalid ansible_port: {ansible_port!r}")
    if not (1 <= ansible_port <= 65535):
        fail(f"Host '{alias}' ansible_port must be in range 1..65535.")

    deploy_user = host_cfg.get("deploy_user", defaults.get("deploy_user", "deploy"))
    if not isinstance(deploy_user, str) or not deploy_user.strip():
        fail(f"Host '{alias}' requires string deploy_user.")

    default_bootstrap = defaults.get("bootstrap", {})
    if default_bootstrap is None:
        default_bootstrap = {}
    if not isinstance(default_bootstrap, dict):
        fail("defaults.bootstrap must be an object when provided.")

    bootstrap = host_cfg.get("bootstrap", {})
    if bootstrap is None:
        bootstrap = {}
    if not isinstance(bootstrap, dict):
        fail(f"Host '{alias}' bootstrap must be an object.")
    bootstrap_username = bootstrap.get("username", default_bootstrap.get("username", "root"))
    bootstrap_password = bootstrap.get("password", default_bootstrap.get("password", ""))
    if bootstrap_password is None:
        bootstrap_password = ""

    if not isinstance(bootstrap_username, str) or not bootstrap_username.strip():
        fail(f"Host '{alias}' bootstrap.username must be a non-empty string.")
    if not isinstance(bootstrap_password, str):
        fail(f"Host '{alias}' bootstrap.password must be a string when provided.")

    default_features = defaults.get("features", {})
    if default_features is None:
        default_features = {}
    if not isinstance(default_features, dict):
        fail("defaults.features must be an object when provided.")
    features = FEATURE_DEFAULTS.copy()
    features.update(default_features)
    features.update(host_cfg.get("features", {}) or {})
    normalized_features = {}
    for key in FEATURE_DEFAULTS:
        normalized_features[key] = parse_bool(features.get(key, FEATURE_DEFAULTS[key]))

    default_remnawave = defaults.get("remnawave", {})
    if default_remnawave is None:
        default_remnawave = {}
    if not isinstance(default_remnawave, dict):
        fail("defaults.remnawave must be an object when provided.")
    remnawave_cfg = REMNAWAVE_DEFAULTS.copy()
    remnawave_cfg.update(default_remnawave)
    remnawave_cfg.update(host_cfg.get("remnawave", {}) or {})
    try:
        remnawave_cfg["node_port"] = int(remnawave_cfg["node_port"])
        remnawave_cfg["caddy_monitor_port"] = int(remnawave_cfg["caddy_monitor_port"])
    except Exception:
        fail(f"Host '{alias}' remnawave ports must be numbers.")
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

    inbound_tags = remnawave_cfg.get("target_inbound_tags", [])
    if inbound_tags is None:
        inbound_tags = []
    if not isinstance(inbound_tags, list):
        fail(f"Host '{alias}' remnawave.target_inbound_tags must be a list.")
    for tag in inbound_tags:
        if not isinstance(tag, str) or not tag.strip():
            fail(f"Host '{alias}' remnawave.target_inbound_tags contains invalid tag: {tag!r}")
    remnawave_cfg["target_inbound_tags"] = [tag.strip() for tag in inbound_tags]
    if remnawave_cfg["caddy_tls_mode"] == "files":
        if not remnawave_cfg["caddy_tls_cert_file"].strip() or not remnawave_cfg["caddy_tls_key_file"].strip():
            fail(f"Host '{alias}' remnawave.caddy_tls_mode=files requires caddy_tls_cert_file and caddy_tls_key_file.")

    default_monitoring = defaults.get("monitoring", {})
    if default_monitoring is None:
        default_monitoring = {}
    if not isinstance(default_monitoring, dict):
        fail("defaults.monitoring must be an object when provided.")
    host_monitoring = host_cfg.get("monitoring", {}) or {}
    if not isinstance(host_monitoring, dict):
        fail(f"Host '{alias}' monitoring must be an object when provided.")
    monitoring_cfg = MONITORING_DEFAULTS.copy()
    monitoring_cfg.update(default_monitoring)
    monitoring_cfg.update(host_monitoring)
    try:
        monitoring_cfg["agent_node_exporter_port"] = int(monitoring_cfg["agent_node_exporter_port"])
        monitoring_cfg["agent_cadvisor_port"] = int(monitoring_cfg["agent_cadvisor_port"])
        monitoring_cfg["stack_retention_days"] = int(monitoring_cfg["stack_retention_days"])
        monitoring_cfg["stack_loki_port"] = int(monitoring_cfg["stack_loki_port"])
    except Exception:
        fail(f"Host '{alias}' monitoring ports/retention must be numbers.")
    for key in ("agent_node_exporter_port", "agent_cadvisor_port", "stack_loki_port"):
        if not (1 <= monitoring_cfg[key] <= 65535):
            fail(f"Host '{alias}' monitoring.{key} must be in range 1..65535.")
    if monitoring_cfg["stack_retention_days"] < 1:
        fail(f"Host '{alias}' monitoring.stack_retention_days must be >= 1.")
    monitoring_cfg["agent_bind_address"] = str(monitoring_cfg.get("agent_bind_address", "0.0.0.0") or "0.0.0.0")
    monitoring_cfg["agent_promtail_enabled"] = parse_bool(monitoring_cfg.get("agent_promtail_enabled", True))
    monitoring_cfg["agent_acl_enabled"] = parse_bool(monitoring_cfg.get("agent_acl_enabled", False))
    acl_sources = monitoring_cfg.get("agent_acl_allowed_sources", [])
    if acl_sources is None:
        acl_sources = []
    if not isinstance(acl_sources, list):
        fail(f"Host '{alias}' monitoring.agent_acl_allowed_sources must be a list.")
    normalized_acl_sources = []
    for source in acl_sources:
        source_text = str(source).strip()
        if not source_text:
            fail(f"Host '{alias}' monitoring.agent_acl_allowed_sources contains empty source value.")
        normalized_acl_sources.append(source_text)
    monitoring_cfg["agent_acl_allowed_sources"] = normalized_acl_sources
    monitoring_cfg["loki_push_url"] = str(monitoring_cfg.get("loki_push_url", "") or "").strip()
    labels_cfg = MONITORING_DEFAULTS["labels"].copy()
    defaults_labels = default_monitoring.get("labels", {})
    host_labels = host_monitoring.get("labels", {})
    if defaults_labels is None:
        defaults_labels = {}
    if host_labels is None:
        host_labels = {}
    if not isinstance(defaults_labels, dict):
        fail("defaults.monitoring.labels must be an object when provided.")
    if not isinstance(host_labels, dict):
        fail(f"Host '{alias}' monitoring.labels must be an object when provided.")
    labels_cfg.update(defaults_labels)
    labels_cfg.update(host_labels)
    monitoring_cfg["labels"] = {
        "country": str(labels_cfg.get("country", "") or "").strip(),
        "role": str(labels_cfg.get("role", "node") or "node").strip(),
    }
    monitoring_cfg["stack_bind_address"] = str(monitoring_cfg.get("stack_bind_address", "127.0.0.1") or "127.0.0.1")
    monitoring_cfg["stack_loki_ingest_bind_address"] = str(
        monitoring_cfg.get("stack_loki_ingest_bind_address", "0.0.0.0") or "0.0.0.0"
    )
    loki_sources = monitoring_cfg.get("stack_loki_ingest_allowed_sources", [])
    if loki_sources is None:
        loki_sources = []
    if not isinstance(loki_sources, list):
        fail(f"Host '{alias}' monitoring.stack_loki_ingest_allowed_sources must be a list.")
    normalized_loki_sources = []
    for source in loki_sources:
        source_text = str(source).strip()
        if not source_text:
            fail(f"Host '{alias}' monitoring.stack_loki_ingest_allowed_sources contains empty source value.")
        normalized_loki_sources.append(source_text)
    monitoring_cfg["stack_loki_ingest_allowed_sources"] = normalized_loki_sources
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

    normalized = {
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
        "custom_roles": custom_roles,
    }
    return normalized


def build_inventory(hosts: dict, mode: str) -> str:
    lines = ["[all]"]
    for alias, cfg in hosts.items():
        if mode == "bootstrap":
            ansible_user = cfg["bootstrap"]["username"]
        else:
            ansible_user = cfg["deploy_user"]
        lines.append(
            f"{alias} "
            f"ansible_host={cfg['ansible_host']} "
            f"ansible_port={cfg['ansible_port']} "
            f"ansible_user={ansible_user} "
            f"ansible_ssh_private_key_file=~/.ssh/id_ed25519"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render runtime inventory and vars from fleet config.")
    parser.add_argument("--fleet-config", required=True, help="Path to decoded fleet config (JSON or YAML).")
    parser.add_argument("--mode", required=True, choices=["bootstrap", "deploy", "lockdown"])
    parser.add_argument("--inventory-out", required=True)
    parser.add_argument("--vars-out", required=True)
    parser.add_argument("--bootstrap-out", required=True)
    args = parser.parse_args()

    hosts_raw, defaults = load_fleet_config(Path(args.fleet_config))
    normalized_hosts = {
        alias: normalize_host(alias, cfg, defaults)
        for alias, cfg in hosts_raw.items()
    }
    monitoring_hosts = [
        alias
        for alias, cfg in normalized_hosts.items()
        if cfg["features"]["feature_monitoring_agent"] or cfg["features"]["feature_monitoring_stack"]
    ]
    stack_hosts = [
        alias
        for alias, cfg in normalized_hosts.items()
        if cfg["features"]["feature_monitoring_stack"]
    ]
    if monitoring_hosts and len(stack_hosts) != 1:
        fail(
            "Exactly one host with features.feature_monitoring_stack=true is required when monitoring features are enabled."
        )

    stack_host_alias = stack_hosts[0] if stack_hosts else ""
    stack_host_address = normalized_hosts[stack_host_alias]["ansible_host"] if stack_host_alias else ""
    stack_loki_port = normalized_hosts[stack_host_alias]["monitoring"]["stack_loki_port"] if stack_host_alias else 3100
    for alias, cfg in normalized_hosts.items():
        if cfg["monitoring"]["loki_push_url"]:
            continue
        if not stack_host_alias:
            cfg["monitoring"]["loki_push_url"] = ""
            continue
        if (
            alias == stack_host_alias
            and cfg["monitoring"]["stack_loki_ingest_bind_address"] in {"127.0.0.1", "localhost"}
        ):
            cfg["monitoring"]["loki_push_url"] = f"http://127.0.0.1:{stack_loki_port}/loki/api/v1/push"
        else:
            cfg["monitoring"]["loki_push_url"] = f"http://{stack_host_address}:{stack_loki_port}/loki/api/v1/push"

    runtime_vars = {
        "fleet_mode": args.mode,
        "fleet_hosts": normalized_hosts,
        "monitoring_stack_host_alias": stack_host_alias,
        "monitoring_stack_host_address": stack_host_address,
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
                "monitoring_agent_promtail_enabled": cfg["monitoring"]["agent_promtail_enabled"],
                "monitoring_agent_acl_enabled": cfg["monitoring"]["agent_acl_enabled"],
                "monitoring_agent_acl_allowed_sources": cfg["monitoring"]["agent_acl_allowed_sources"],
                "monitoring_loki_push_url": cfg["monitoring"]["loki_push_url"],
                "monitoring_labels_country": cfg["monitoring"]["labels"]["country"],
                "monitoring_labels_role": cfg["monitoring"]["labels"]["role"],
                "monitoring_stack_retention_days": cfg["monitoring"]["stack_retention_days"],
                "monitoring_stack_bind_address": cfg["monitoring"]["stack_bind_address"],
                "monitoring_stack_loki_port": cfg["monitoring"]["stack_loki_port"],
                "monitoring_stack_loki_ingest_bind_address": cfg["monitoring"]["stack_loki_ingest_bind_address"],
                "monitoring_stack_loki_ingest_allowed_sources": cfg["monitoring"]["stack_loki_ingest_allowed_sources"],
                "monitoring_stack_grafana_admin_user": cfg["monitoring"]["stack_grafana_admin_user"],
                "monitoring_stack_grafana_admin_password": cfg["monitoring"]["stack_grafana_admin_password"],
            }
            for alias, cfg in normalized_hosts.items()
        },
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
