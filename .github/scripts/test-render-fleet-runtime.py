#!/usr/bin/env python3
"""Contract tests for render-fleet-runtime.py."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
RENDERER = SCRIPT_DIR / "render-fleet-runtime.py"


def run_renderer(config_text: str, mode: str, suffix: str = ".yaml") -> tuple[subprocess.CompletedProcess[str], Path, Path, Path]:
    temp_dir = tempfile.TemporaryDirectory()
    root = Path(temp_dir.name)
    config_path = root / f"fleet{suffix}"
    inventory_out = root / "hosts.ini"
    vars_out = root / "runtime_vars.json"
    bootstrap_out = root / "bootstrap_map.json"

    config_path.write_text(config_text, encoding="utf-8")

    cmd = [
        str(RENDERER),
        "--fleet-config",
        str(config_path),
        "--mode",
        mode,
        "--inventory-out",
        str(inventory_out),
        "--vars-out",
        str(vars_out),
        "--bootstrap-out",
        str(bootstrap_out),
    ]
    proc = subprocess.run(cmd, text=True, capture_output=True, check=False)
    # keep temp dir alive by attaching to proc; caller cleans via attribute
    setattr(proc, "_tmpdir", temp_dir)
    return proc, inventory_out, vars_out, bootstrap_out


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def parse_inventory_line(inventory_text: str, alias: str) -> str:
    for line in inventory_text.splitlines():
        if line.startswith(f"{alias} "):
            return line
    raise AssertionError(f"Host alias '{alias}' not found in generated inventory")


def test_valid_yaml_modes() -> None:
    yaml_text = (
        "---\n"
        "defaults:\n"
        "  deploy_user: deploy\n"
        "  bootstrap:\n"
        "    username: root\n"
        "    password: pw\n"
        "  features:\n"
        "    feature_remnawave_node: true\n"
        "  remnawave:\n"
        "    node_secret_key: from-defaults\n"
        "  monitoring:\n"
        "    agent_acl_enabled: true\n"
        "    agent_acl_allowed_sources:\n"
        "      - 203.0.113.100\n"
        "hosts:\n"
        "  de_node:\n"
        "    ansible_host: 203.0.113.10\n"
        "    remnawave:\n"
        "      caddy_domain: de.example.com\n"
    )

    expectations = {
        "bootstrap": "ansible_user=root",
        "deploy": "ansible_user=deploy",
        "lockdown": "ansible_user=deploy",
    }

    for mode, expected_user in expectations.items():
        proc, inv_path, vars_path, boot_path = run_renderer(yaml_text, mode)
        assert_true(proc.returncode == 0, f"Mode {mode} failed: {proc.stderr or proc.stdout}")

        inv_text = inv_path.read_text(encoding="utf-8")
        line = parse_inventory_line(inv_text, "de_node")
        assert_true(expected_user in line, f"Mode {mode} expected '{expected_user}' in inventory line: {line}")

        runtime_vars = json.loads(vars_path.read_text(encoding="utf-8"))
        assert_true(runtime_vars["fleet_mode"] == mode, f"fleet_mode mismatch for {mode}")
        assert_true("de_node" in runtime_vars["fleet_hosts"], "de_node missing in fleet_hosts")
        assert_true("de_node" in runtime_vars["remnawave_runtime_host_vars"], "de_node missing in remnawave_runtime_host_vars")
        assert_true(
            runtime_vars["fleet_hosts"]["de_node"]["features"]["feature_firewall"] is False,
            "feature_firewall default mismatch",
        )
        monitoring_cfg = runtime_vars["fleet_hosts"]["de_node"]["monitoring"]
        assert_true(monitoring_cfg["agent_acl_enabled"] is True, "monitoring.agent_acl_enabled default not applied")
        assert_true(
            monitoring_cfg["agent_acl_allowed_sources"] == ["203.0.113.100"],
            "monitoring.agent_acl_allowed_sources default not applied",
        )
        assert_true(monitoring_cfg["agent_promtail_enabled"] is True, "monitoring.agent_promtail_enabled default mismatch")
        assert_true(monitoring_cfg["labels"]["role"] == "node", "monitoring.labels.role default mismatch")
        assert_true(monitoring_cfg["labels"]["country"] == "", "monitoring.labels.country default mismatch")
        assert_true(
            runtime_vars["remnawave_runtime_host_vars"]["de_node"]["monitoring_agent_acl_enabled"] is True,
            "monitoring_agent_acl_enabled missing in runtime host vars",
        )

        bootstrap_map = json.loads(boot_path.read_text(encoding="utf-8"))
        assert_true(bootstrap_map["de_node"]["deploy_user"] == "deploy", "bootstrap_map deploy_user mismatch")


def test_firewall_defaults_and_overrides() -> None:
    yaml_text = (
        "---\n"
        "defaults:\n"
        "  features:\n"
        "    feature_firewall: true\n"
        "  firewall:\n"
        "    ssh_allowed_sources:\n"
        "      - 203.0.113.10/32\n"
        "    extra_allowed_tcp_ports:\n"
        "      - 2222\n"
        "hosts:\n"
        "  de_node:\n"
        "    ansible_host: 203.0.113.90\n"
        "  nl_node:\n"
        "    ansible_host: 203.0.113.91\n"
        "    features:\n"
        "      feature_firewall: false\n"
        "    firewall:\n"
        "      ssh_allowed_sources:\n"
        "        - 198.51.100.25\n"
        "      extra_allowed_udp_ports:\n"
        "        - 5353\n"
    )

    proc, _, vars_path, _ = run_renderer(yaml_text, "deploy")
    assert_true(proc.returncode == 0, f"Renderer failed: {proc.stderr or proc.stdout}")
    runtime_vars = json.loads(vars_path.read_text(encoding="utf-8"))
    de_cfg = runtime_vars["fleet_hosts"]["de_node"]
    nl_cfg = runtime_vars["fleet_hosts"]["nl_node"]
    assert_true(de_cfg["features"]["feature_firewall"] is True, "defaults feature_firewall not applied")
    assert_true(nl_cfg["features"]["feature_firewall"] is False, "host feature_firewall override not applied")
    assert_true(
        de_cfg["firewall"]["ssh_allowed_sources"] == ["203.0.113.10/32"],
        "defaults firewall.ssh_allowed_sources not applied",
    )
    assert_true(
        de_cfg["firewall"]["extra_allowed_tcp_ports"] == [2222],
        "defaults firewall.extra_allowed_tcp_ports not applied",
    )
    assert_true(
        nl_cfg["firewall"]["ssh_allowed_sources"] == ["198.51.100.25"],
        "host firewall.ssh_allowed_sources override not applied",
    )
    assert_true(
        nl_cfg["firewall"]["extra_allowed_udp_ports"] == [5353],
        "host firewall.extra_allowed_udp_ports override not applied",
    )


def test_valid_json_input() -> None:
    config = {
        "hosts": {
            "nl_node": {
                "ansible_host": "198.51.100.20",
                "deploy_user": "ops",
                "bootstrap": {"username": "root", "password": "pw"},
                "features": {"feature_docker": True},
                "remnawave": {"node_secret_key": "secret-from-json"},
            }
        }
    }

    proc, inv_path, _, _ = run_renderer(json.dumps(config), "deploy", suffix=".json")
    assert_true(proc.returncode == 0, f"JSON input failed: {proc.stderr or proc.stdout}")
    inv_text = inv_path.read_text(encoding="utf-8")
    line = parse_inventory_line(inv_text, "nl_node")
    assert_true("ansible_user=ops" in line, f"deploy_user from JSON not applied: {line}")


def test_invalid_missing_ansible_host() -> None:
    config = {
        "hosts": {
            "bad": {
                "bootstrap": {"username": "root", "password": "pw"},
            }
        }
    }

    proc, _, _, _ = run_renderer(json.dumps(config), "deploy", suffix=".json")
    assert_true(proc.returncode != 0, "Renderer should fail when ansible_host is missing")
    assert_true("ansible_host" in (proc.stderr + proc.stdout), "Error should mention ansible_host")


def test_invalid_custom_roles_item() -> None:
    config = {
        "hosts": {
            "bad": {
                "ansible_host": "203.0.113.30",
                "custom_roles": [""],
            }
        }
    }

    proc, _, _, _ = run_renderer(json.dumps(config), "deploy", suffix=".json")
    assert_true(proc.returncode != 0, "Renderer should fail for invalid custom_roles item")
    assert_true("custom_roles" in (proc.stderr + proc.stdout), "Error should mention custom_roles")


def test_invalid_ipv6_state() -> None:
    config = {
        "hosts": {
            "bad": {
                "ansible_host": "203.0.113.40",
                "remnawave": {"ipv6_state": "invalid"},
            }
        }
    }

    proc, _, _, _ = run_renderer(json.dumps(config), "deploy", suffix=".json")
    assert_true(proc.returncode != 0, "Renderer should fail for invalid ipv6_state")
    assert_true("ipv6_state" in (proc.stderr + proc.stdout), "Error should mention ipv6_state")


def test_invalid_monitoring_port() -> None:
    config = {
        "hosts": {
            "bad": {
                "ansible_host": "203.0.113.50",
                "monitoring": {"agent_node_exporter_port": 70000},
            }
        }
    }

    proc, _, _, _ = run_renderer(json.dumps(config), "deploy", suffix=".json")
    assert_true(proc.returncode != 0, "Renderer should fail for invalid monitoring port")
    assert_true("monitoring" in (proc.stderr + proc.stdout), "Error should mention monitoring")


def test_invalid_monitoring_acl_sources_type() -> None:
    config = {
        "hosts": {
            "bad": {
                "ansible_host": "203.0.113.60",
                "monitoring": {"agent_acl_allowed_sources": "203.0.113.10"},
            }
        }
    }

    proc, _, _, _ = run_renderer(json.dumps(config), "deploy", suffix=".json")
    assert_true(proc.returncode != 0, "Renderer should fail for invalid monitoring ACL source type")
    assert_true("agent_acl_allowed_sources" in (proc.stderr + proc.stdout), "Error should mention agent_acl_allowed_sources")


def test_invalid_firewall_port() -> None:
    config = {
        "hosts": {
            "bad": {
                "ansible_host": "203.0.113.65",
                "firewall": {"extra_allowed_tcp_ports": [70000]},
            }
        }
    }

    proc, _, _, _ = run_renderer(json.dumps(config), "deploy", suffix=".json")
    assert_true(proc.returncode != 0, "Renderer should fail for invalid firewall port")
    assert_true("firewall.extra_allowed_tcp_ports" in (proc.stderr + proc.stdout), "Error should mention firewall port")


def test_invalid_firewall_sources_type() -> None:
    config = {
        "hosts": {
            "bad": {
                "ansible_host": "203.0.113.66",
                "firewall": {"ssh_allowed_sources": "203.0.113.10/32"},
            }
        }
    }

    proc, _, _, _ = run_renderer(json.dumps(config), "deploy", suffix=".json")
    assert_true(proc.returncode != 0, "Renderer should fail for invalid firewall source type")
    assert_true("firewall.ssh_allowed_sources" in (proc.stderr + proc.stdout), "Error should mention firewall sources")


def test_monitoring_requires_single_stack_host() -> None:
    config = {
        "hosts": {
            "de_node": {
                "ansible_host": "203.0.113.70",
                "features": {"feature_monitoring_agent": True},
            },
            "nl_node": {
                "ansible_host": "203.0.113.71",
                "features": {"feature_monitoring_agent": True},
            },
        }
    }

    proc, _, _, _ = run_renderer(json.dumps(config), "deploy", suffix=".json")
    assert_true(proc.returncode != 0, "Renderer should fail when monitoring stack host is missing")
    assert_true("feature_monitoring_stack" in (proc.stderr + proc.stdout), "Error should mention stack host requirement")


def test_monitoring_fails_with_multiple_stack_hosts() -> None:
    config = {
        "hosts": {
            "de_node": {
                "ansible_host": "203.0.113.80",
                "features": {
                    "feature_monitoring_agent": True,
                    "feature_monitoring_stack": True,
                },
            },
            "nl_node": {
                "ansible_host": "203.0.113.81",
                "features": {
                    "feature_monitoring_agent": True,
                    "feature_monitoring_stack": True,
                },
            },
        }
    }

    proc, _, _, _ = run_renderer(json.dumps(config), "deploy", suffix=".json")
    assert_true(proc.returncode != 0, "Renderer should fail when multiple monitoring stack hosts are enabled")
    assert_true("feature_monitoring_stack" in (proc.stderr + proc.stdout), "Error should mention stack host requirement")


def test_monitoring_resolves_loki_push_url_and_labels() -> None:
    yaml_text = (
        "---\n"
        "defaults:\n"
        "  features:\n"
        "    feature_monitoring_agent: true\n"
        "  monitoring:\n"
        "    labels:\n"
        "      country: global-country\n"
        "      role: edge\n"
        "hosts:\n"
        "  de_node:\n"
        "    ansible_host: 203.0.113.90\n"
        "    features:\n"
        "      feature_monitoring_stack: true\n"
        "    monitoring:\n"
        "      stack_loki_ingest_bind_address: 127.0.0.1\n"
        "  nl_node:\n"
        "    ansible_host: 203.0.113.91\n"
        "    monitoring:\n"
        "      labels:\n"
        "        country: nl\n"
        "        role: node\n"
    )

    proc, _, vars_path, _ = run_renderer(yaml_text, "deploy")
    assert_true(proc.returncode == 0, f"Renderer failed: {proc.stderr or proc.stdout}")
    runtime_vars = json.loads(vars_path.read_text(encoding="utf-8"))
    de_cfg = runtime_vars["fleet_hosts"]["de_node"]["monitoring"]
    nl_cfg = runtime_vars["fleet_hosts"]["nl_node"]["monitoring"]
    assert_true(
        de_cfg["loki_push_url"] == "http://127.0.0.1:3100/loki/api/v1/push",
        "Stack host should resolve local loki_push_url when ingest bind address is loopback",
    )
    assert_true(
        nl_cfg["loki_push_url"] == "http://203.0.113.90:3100/loki/api/v1/push",
        "Node host should resolve loki_push_url to monitoring stack host address",
    )
    assert_true(nl_cfg["labels"]["country"] == "nl", "Host labels override defaults")
    assert_true(nl_cfg["labels"]["role"] == "node", "Host labels role mismatch")


def main() -> int:
    tests = [
        test_valid_yaml_modes,
        test_firewall_defaults_and_overrides,
        test_valid_json_input,
        test_invalid_missing_ansible_host,
        test_invalid_custom_roles_item,
        test_invalid_ipv6_state,
        test_invalid_monitoring_port,
        test_invalid_monitoring_acl_sources_type,
        test_invalid_firewall_port,
        test_invalid_firewall_sources_type,
        test_monitoring_requires_single_stack_host,
        test_monitoring_fails_with_multiple_stack_hosts,
        test_monitoring_resolves_loki_push_url_and_labels,
    ]

    for test in tests:
        test()
        print(f"PASS: {test.__name__}")

    print("All render-fleet-runtime contract tests passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
