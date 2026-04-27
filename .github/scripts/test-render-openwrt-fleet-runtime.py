#!/usr/bin/env python3
"""Contract tests for render-openwrt-fleet-runtime.py."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
RENDERER = SCRIPT_DIR / "render-openwrt-fleet-runtime.py"


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
        "--skip-connectivity-check",
    ]
    proc = subprocess.run(cmd, text=True, capture_output=True, check=False)
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


def test_valid_yaml_modes_and_access_selection() -> None:
    yaml_text = (
        "---\n"
        "defaults:\n"
        "  deploy_user: root\n"
        "  features:\n"
        "    feature_openwrt_zerotier: false\n"
        "  bootstrap:\n"
        "    username: root\n"
        "    password: pw\n"
        "  access:\n"
        "    proxy_jumps:\n"
        "      - jump-a.example\n"
        "      - jump-b.example\n"
        "    ssh_private_key_file: ~/.ssh/ansible_actions\n"
        "hosts:\n"
        "  wrt_1:\n"
        "    access:\n"
        "      lan_host: 192.168.1.1\n"
        "      zt_host: 172.23.1.1\n"
        "    passwall2:\n"
        "      subscribe_url: https://example.invalid/sub\n"
    )

    expectations = {
        "bootstrap": ("ansible_user=root", "ansible_host=192.168.1.1"),
        "deploy": ("ansible_user=root", "ansible_host=172.23.1.1"),
        "lockdown": ("ansible_user=root", "ansible_host=172.23.1.1"),
    }

    for mode, (expected_user, expected_host) in expectations.items():
        proc, inv_path, vars_path, boot_path = run_renderer(yaml_text, mode)
        assert_true(proc.returncode == 0, f"Mode {mode} failed: {proc.stderr or proc.stdout}")

        inv_text = inv_path.read_text(encoding="utf-8")
        line = parse_inventory_line(inv_text, "wrt_1")
        assert_true(expected_user in line, f"Mode {mode} expected '{expected_user}' in inventory line: {line}")
        assert_true(expected_host in line, f"Mode {mode} expected '{expected_host}' in inventory line: {line}")
        assert_true("ProxyJump=jump-a.example" in line, f"Mode {mode} expected first proxy_jumps candidate in inventory line: {line}")
        assert_true(
            "ansible_ssh_private_key_file=~/.ssh/ansible_actions" in line,
            f"Mode {mode} expected custom ssh_private_key_file in inventory line: {line}",
        )

        runtime_vars = json.loads(vars_path.read_text(encoding="utf-8"))
        assert_true(runtime_vars["fleet_mode"] == mode, f"fleet_mode mismatch for {mode}")
        assert_true("wrt_1" in runtime_vars["openwrt_fleet_hosts"], "wrt_1 missing in openwrt_fleet_hosts")

        bootstrap_map = json.loads(boot_path.read_text(encoding="utf-8"))
        assert_true(bootstrap_map["wrt_1"]["deploy_user"] == "root", "bootstrap_map deploy_user mismatch")
        assert_true(
            bootstrap_map["wrt_1"]["proxy_jumps"] == ["jump-a.example", "jump-b.example"],
            "bootstrap_map proxy_jumps mismatch",
        )


def test_valid_json_input() -> None:
    config = {
        "defaults": {
            "features": {
                "feature_openwrt_zerotier": False,
            }
        },
        "hosts": {
            "wrt_2": {
                "access": {"zt_host": "172.23.5.2"},
                "deploy_user": "root",
                "bootstrap": {"username": "root", "password": "pw"},
                "features": {"feature_tailscale": True},
                "passwall2": {
                    "subscribe_url": "https://example.invalid/2",
                    "acl_bypass_macs": ["AA:BB:CC:DD:EE:FF"],
                },
                "profile": "fresh",
                "wan": {
                    "proto": "pppoe",
                    "device": "eth1",
                    "pppoe_username": "user",
                    "pppoe_password": "pass",
                },
                "docker": {
                    "runtime_packages": ["dockerd", "docker"],
                    "manage_daemon_config": True,
                    "daemon_config": {"live-restore": True},
                },
            }
        }
    }

    proc, inv_path, vars_path, _ = run_renderer(json.dumps(config), "deploy", suffix=".json")
    assert_true(proc.returncode == 0, f"JSON input failed: {proc.stderr or proc.stdout}")
    inv_text = inv_path.read_text(encoding="utf-8")
    line = parse_inventory_line(inv_text, "wrt_2")
    assert_true("ansible_host=172.23.5.2" in line, f"zt_host from JSON not applied: {line}")
    runtime_vars = json.loads(vars_path.read_text(encoding="utf-8"))
    assert_true(
        runtime_vars["openwrt_fleet_hosts"]["wrt_2"]["features"]["feature_tailscale"] is True,
        "feature_tailscale from JSON not applied",
    )
    assert_true(
        runtime_vars["openwrt_fleet_hosts"]["wrt_2"]["wan"]["proto"] == "pppoe",
        "wan.proto from JSON not applied",
    )
    assert_true(
        runtime_vars["openwrt_runtime_host_vars"]["wrt_2"]["openwrt_wan_device"] == "eth1",
        "openwrt_wan_device not exported to runtime host vars",
    )
    assert_true(
        runtime_vars["openwrt_fleet_hosts"]["wrt_2"]["profile"] == "fresh",
        "profile from JSON not applied",
    )
    assert_true(
        runtime_vars["openwrt_runtime_host_vars"]["wrt_2"]["passwall2_acl_bypass_macs"] == ["AA:BB:CC:DD:EE:FF"],
        "passwall2_acl_bypass_macs not exported to runtime host vars",
    )
    assert_true(
        runtime_vars["openwrt_runtime_host_vars"]["wrt_2"]["openwrt_docker_runtime_manage_daemon_config"] is True,
        "openwrt_docker_runtime_manage_daemon_config not exported",
    )


def test_legacy_proxy_jump_backward_compatibility() -> None:
    config = {
        "defaults": {
            "features": {
                "feature_openwrt_zerotier": False,
            },
            "access": {
                "proxy_jump": "legacy-jump.example",
            },
        },
        "hosts": {
            "wrt_legacy": {
                "access": {"zt_host": "172.23.77.7"},
                "passwall2": {"subscribe_url": "https://example.invalid/sub"},
            }
        },
    }

    proc, inv_path, _, boot_path = run_renderer(json.dumps(config), "deploy", suffix=".json")
    assert_true(proc.returncode == 0, f"Legacy proxy_jump input failed: {proc.stderr or proc.stdout}")
    line = parse_inventory_line(inv_path.read_text(encoding="utf-8"), "wrt_legacy")
    assert_true("ProxyJump=legacy-jump.example" in line, "Legacy proxy_jump was not applied")
    bootstrap_map = json.loads(boot_path.read_text(encoding="utf-8"))
    assert_true(
        bootstrap_map["wrt_legacy"]["proxy_jumps"] == ["legacy-jump.example"],
        "Legacy proxy_jump should be normalized to proxy_jumps list",
    )


def test_host_level_ssh_key_override() -> None:
    config = {
        "defaults": {
            "access": {
                "ssh_private_key_file": "~/.ssh/default_key",
            },
            "features": {
                "feature_openwrt_zerotier": False,
            },
        },
        "hosts": {
            "wrt_key": {
                "access": {
                    "zt_host": "172.23.55.5",
                    "ssh_private_key_file": "~/.ssh/override_key",
                },
                "passwall2": {"subscribe_url": "https://example.invalid/sub"},
            }
        },
    }

    proc, inv_path, _, _ = run_renderer(json.dumps(config), "deploy", suffix=".json")
    assert_true(proc.returncode == 0, f"Host-level ssh key override failed: {proc.stderr or proc.stdout}")
    line = parse_inventory_line(inv_path.read_text(encoding="utf-8"), "wrt_key")
    assert_true(
        "ansible_ssh_private_key_file=~/.ssh/override_key" in line,
        "Host-level ssh_private_key_file override was not applied",
    )


def test_invalid_missing_access() -> None:
    config = {
        "defaults": {
            "features": {
                "feature_openwrt_zerotier": False,
            }
        },
        "hosts": {
            "bad": {
                "passwall2": {"subscribe_url": "https://example.invalid/sub"},
            }
        }
    }

    proc, _, _, _ = run_renderer(json.dumps(config), "deploy", suffix=".json")
    assert_true(proc.returncode != 0, "Renderer should fail when access endpoints are missing")
    assert_true("access.lan_host" in (proc.stderr + proc.stdout) or "access.zt_host" in (proc.stderr + proc.stdout), "Error should mention access endpoints")


def test_invalid_missing_passwall_subscribe_url() -> None:
    config = {
        "defaults": {
            "features": {
                "feature_openwrt_zerotier": False,
            }
        },
        "hosts": {
            "bad": {
                "access": {"zt_host": "172.23.10.10"},
                "features": {"feature_openwrt_passwall2": True},
                "passwall2": {"enabled": True, "subscribe_url": ""},
            }
        }
    }

    proc, _, _, _ = run_renderer(json.dumps(config), "deploy", suffix=".json")
    assert_true(proc.returncode != 0, "Renderer should fail for empty passwall2.subscribe_url")
    assert_true("subscribe_url" in (proc.stderr + proc.stdout), "Error should mention subscribe_url")


def test_invalid_zerotier_manage_secret_without_secret() -> None:
    config = {
        "hosts": {
            "bad": {
                "access": {"zt_host": "172.23.10.11"},
                "passwall2": {"subscribe_url": "https://example.invalid/sub"},
                "zerotier": {"network_id": "a84ac5c10a8906ee", "manage_secret": True, "secret": ""},
            }
        }
    }

    proc, _, _, _ = run_renderer(json.dumps(config), "deploy", suffix=".json")
    assert_true(proc.returncode != 0, "Renderer should fail when zerotier.manage_secret=true and secret is empty")
    assert_true("zerotier.secret" in (proc.stderr + proc.stdout), "Error should mention zerotier.secret")


def test_invalid_static_wan_without_ip_or_mask() -> None:
    config = {
        "defaults": {
            "features": {
                "feature_openwrt_zerotier": False,
            }
        },
        "hosts": {
            "bad": {
                "access": {"zt_host": "172.23.10.12"},
                "passwall2": {"subscribe_url": "https://example.invalid/sub"},
                "wan": {"proto": "static", "device": "eth0", "ipaddr": "", "netmask": ""},
            }
        }
    }

    proc, _, _, _ = run_renderer(json.dumps(config), "deploy", suffix=".json")
    assert_true(proc.returncode != 0, "Renderer should fail for static WAN without ipaddr/netmask")
    assert_true(
        ("wan.ipaddr" in (proc.stderr + proc.stdout)) or ("wan.netmask" in (proc.stderr + proc.stdout)),
        "Error should mention wan.ipaddr or wan.netmask",
    )


def main() -> int:
    tests = [
        test_valid_yaml_modes_and_access_selection,
        test_valid_json_input,
        test_legacy_proxy_jump_backward_compatibility,
        test_host_level_ssh_key_override,
        test_invalid_missing_access,
        test_invalid_missing_passwall_subscribe_url,
        test_invalid_zerotier_manage_secret_without_secret,
        test_invalid_static_wan_without_ip_or_mask,
    ]

    for test in tests:
        test()
        print(f"PASS: {test.__name__}")

    print("All render-openwrt-fleet-runtime contract tests passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
