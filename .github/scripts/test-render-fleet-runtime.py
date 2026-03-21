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


def run_renderer(
    config_text: str,
    mode: str,
    suffix: str = ".yaml",
    target: str = "remnawave",
) -> tuple[subprocess.CompletedProcess[str], Path, Path, Path]:
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
        "--target",
        target,
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

        bootstrap_map = json.loads(boot_path.read_text(encoding="utf-8"))
        assert_true(bootstrap_map["de_node"]["deploy_user"] == "deploy", "bootstrap_map deploy_user mismatch")


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


def test_yusic_worker_runtime_success() -> None:
    yaml_text = (
        "---\n"
        "defaults:\n"
        "  deploy_user: deploy\n"
        "  yusic_worker:\n"
        "    relay_host_alias: relay_ru\n"
        "    image_repo: ghcr.io/example/download-worker\n"
        "    redis_url: redis://127.0.0.1:6379/0\n"
        "    cache_bot_token: token\n"
        "    inline_cache_chat_id: -1001\n"
        "hosts:\n"
        "  relay_ru:\n"
        "    ansible_host: 203.0.113.10\n"
        "workers:\n"
        "  wrt_me:\n"
        "    arch: arm64\n"
        "    ssh:\n"
        "      host: 100.64.0.10\n"
        "      port: 22\n"
        "      user: root\n"
        "    tags:\n"
        "      - provider:soundcloud\n"
        "      - region:nl\n"
    )

    proc, inv_path, vars_path, _ = run_renderer(yaml_text, "deploy", target="yusic_worker")
    assert_true(proc.returncode == 0, f"yusic_worker render failed: {proc.stderr or proc.stdout}")

    inv_text = inv_path.read_text(encoding="utf-8")
    parse_inventory_line(inv_text, "relay_ru")

    runtime_vars = json.loads(vars_path.read_text(encoding="utf-8"))
    assert_true(runtime_vars["fleet_target"] == "yusic_worker", "fleet_target mismatch for yusic_worker")
    yusic_runtime = runtime_vars["yusic_worker_runtime"]
    assert_true(yusic_runtime["relay_host_alias"] == "relay_ru", "relay_host_alias mismatch")
    assert_true(yusic_runtime["enabled_workers"] == ["wrt_me"], "enabled workers mismatch")
    worker = yusic_runtime["workers"]["wrt_me"]
    assert_true(worker["arch"] == "arm64", "worker arch mismatch")
    assert_true(worker["image_repo"] == "ghcr.io/example/download-worker", "worker image repo mismatch")
    assert_true(worker["ssh"]["host"] == "100.64.0.10", "worker ssh host mismatch")


def test_yusic_worker_invalid_relay_alias() -> None:
    config = {
        "defaults": {
            "yusic_worker": {
                "relay_host_alias": "missing_relay",
                "image_repo": "ghcr.io/example/download-worker",
                "redis_url": "redis://127.0.0.1:6379/0",
                "cache_bot_token": "token",
                "inline_cache_chat_id": "-1001",
            }
        },
        "hosts": {
            "relay_ru": {
                "ansible_host": "203.0.113.10",
            }
        },
        "workers": {
            "wrt_me": {
                "ssh": {
                    "host": "100.64.0.10",
                    "user": "root",
                }
            }
        },
    }

    proc, _, _, _ = run_renderer(json.dumps(config), "deploy", suffix=".json", target="yusic_worker")
    assert_true(proc.returncode != 0, "Renderer should fail for unknown relay_host_alias")
    assert_true("relay_host_alias" in (proc.stderr + proc.stdout), "Error should mention relay_host_alias")


def test_yusic_worker_requires_enabled_workers_for_target() -> None:
    config = {
        "hosts": {
            "relay_ru": {
                "ansible_host": "203.0.113.10",
            }
        },
        "workers": {
            "wrt_me": {
                "enabled": False,
                "relay_host_alias": "relay_ru",
                "image_repo": "ghcr.io/example/download-worker",
                "ssh": {
                    "host": "100.64.0.10",
                    "user": "root",
                },
                "redis_url": "redis://127.0.0.1:6379/0",
                "cache_bot_token": "token",
                "inline_cache_chat_id": "-1001",
            }
        },
    }
    proc, _, _, _ = run_renderer(json.dumps(config), "deploy", suffix=".json", target="yusic_worker")
    assert_true(proc.returncode != 0, "Renderer should fail when target=yusic_worker has no enabled workers")
    assert_true("enabled worker" in (proc.stderr + proc.stdout), "Error should mention enabled worker")


def test_remnawave_target_ignores_invalid_yusic_contract() -> None:
    yaml_text = (
        "---\n"
        "defaults:\n"
        "  yusic_worker:\n"
        "    arch: x86_64\n"
        "hosts:\n"
        "  de_node:\n"
        "    ansible_host: 203.0.113.10\n"
        "workers:\n"
        "  - unexpected-list-item\n"
    )
    proc, _, vars_path, _ = run_renderer(yaml_text, "deploy", target="remnawave")
    assert_true(proc.returncode == 0, f"remnawave target should ignore invalid yusic contract: {proc.stderr or proc.stdout}")
    runtime_vars = json.loads(vars_path.read_text(encoding="utf-8"))
    assert_true(runtime_vars["fleet_target"] == "remnawave", "fleet_target mismatch for remnawave")
    assert_true("yusic_worker_runtime" in runtime_vars, "yusic_worker_runtime should stay present in runtime vars")


def main() -> int:
    tests = [
        test_valid_yaml_modes,
        test_valid_json_input,
        test_invalid_missing_ansible_host,
        test_invalid_custom_roles_item,
        test_invalid_ipv6_state,
        test_yusic_worker_runtime_success,
        test_yusic_worker_invalid_relay_alias,
        test_yusic_worker_requires_enabled_workers_for_target,
        test_remnawave_target_ignores_invalid_yusic_contract,
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
