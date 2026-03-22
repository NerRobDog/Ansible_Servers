#!/usr/bin/env python3
"""Contract tests for collect-openwrt-host-config.py."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml


SCRIPT_DIR = Path(__file__).resolve().parent
COLLECTOR = SCRIPT_DIR / "collect-openwrt-host-config.py"
RENDERER = SCRIPT_DIR / "render-openwrt-fleet-runtime.py"


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def write_mock_ssh_script(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "cat \"${MOCK_OPENWRT_COLLECTOR_STDOUT_FILE}\"\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def run_collector(
    mock_output: str,
    *,
    alias: str = "wrt_new",
    extra_args: list[str] | None = None,
    use_password: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path, Path, tempfile.TemporaryDirectory]:
    temp_dir = tempfile.TemporaryDirectory()
    root = Path(temp_dir.name)

    mock_ssh = root / "mock-ssh.sh"
    write_mock_ssh_script(mock_ssh)

    output_file = root / "mock-output.txt"
    output_file.write_text(mock_output, encoding="utf-8")

    key_file = root / "id_ed25519"
    key_file.write_text("dummy-private-key\n", encoding="utf-8")

    yaml_out = root / "host.yml"
    json_out = root / "report.json"

    cmd = [
        str(COLLECTOR),
        "--alias",
        alias,
        "--host",
        "198.51.100.10",
        "--user",
        "root",
        "--yaml-out",
        str(yaml_out),
        "--json-out",
        str(json_out),
        "--host-key-check",
        "off",
    ]
    if use_password:
        cmd.extend(["--password", "root-password"])
    else:
        cmd.extend(["--key-file", str(key_file)])

    if extra_args:
        cmd.extend(extra_args)

    env = os.environ.copy()
    env["OPENWRT_COLLECTOR_SSH_CMD"] = str(mock_ssh)
    env["MOCK_OPENWRT_COLLECTOR_STDOUT_FILE"] = str(output_file)

    proc = subprocess.run(cmd, text=True, capture_output=True, check=False, env=env)
    return proc, yaml_out, json_out, temp_dir


def test_static_wan_and_renderer_compat() -> None:
    mock = """\
NETWORK_CONFIG_PRESENT=1
FIREWALL_CONFIG_PRESENT=1
LAN_DEVICE=br-lan
LAN_IPADDR=192.168.88.1
LAN_NETMASK=255.255.255.0
LAN_IP6ASSIGN=60
ULA_PREFIX=
WAN_PROTO=static
WAN_DEVICE=eth0
WAN_IPADDR=10.174.197.112
WAN_NETMASK=255.255.255.0
WAN_GATEWAY=10.174.197.254
WAN_DNS=1.1.1.1 1.0.0.1
WAN_PPPOE_USERNAME=
WAN_PPPOE_PASSWORD=
WAN_PPPOE_IPV6=auto
WAN_FAILOVER_ENABLED=1
WAN_FAILOVER_WAIT_SEC=35
WAN_FAILOVER_PROBE_HOST=1.1.1.1
WAN_FAILOVER_PROBE_COUNT=2
WAN_FAILOVER_START_PRIORITY=97
ZT_IP=172.23.236.127
ZT_NETWORK_ID=a84ac5c10a8906ee
ZT_SECRET=
ZT_SRC_CIDR=172.16.0.0/12
ZT_PKG_INSTALLED=1
ZT_SERVICE_PRESENT=1
ZT_SERVICE_ENABLED=1
TAILSCALE_PKG_INSTALLED=0
TAILSCALE_SERVICE_PRESENT=0
TAILSCALE_SERVICE_ENABLED=0
TAILSCALE_BINARY_PRESENT=0
PASSWALL2_CONFIG_PRESENT=1
PASSWALL2_PKG_INSTALLED=1
PASSWALL2_ENABLED=1
PASSWALL2_SUBSCRIBE_URL=https://provider.example/sub
PASSWALL2_PROBE_URL=https://www.gstatic.com/generate_204
PASSWALL2_SOCKS_PORT=1070
PASSWALL2_ACL_MACS=AA:BB:CC:DD:EE:FF
MONITORING_SERVICE_PRESENT=1
MONITORING_SERVICE_ENABLED=1
MONITORING_EXPORTER_PORT=9100
MONITORING_PROBE_INTERVAL=5
DOCKER_SERVICE_PRESENT=1
DOCKER_SERVICE_ENABLED=1
DOCKER_RUNTIME_PACKAGES=dockerd,docker,docker-compose
DOCKER_DAEMON_CONFIG_PRESENT=1
DOCKER_DAEMON_CONFIG_JSON={"log-driver":"json-file"}
DOCKER_STACK_NAMES=
HOMEPROXY_CONFIG_PRESENT=0
HOMEPROXY_PKG_INSTALLED=0
SSH_LOCKDOWN_PASSWORD_OFF=1
SSH_LOCKDOWN_ROOT_OFF=1
"""
    proc, yaml_out, _, tmpdir = run_collector(mock, alias="wrt_static")
    assert_true(proc.returncode == 0, f"collector failed: {proc.stderr or proc.stdout}")

    generated = yaml.safe_load(yaml_out.read_text(encoding="utf-8"))
    host = generated["wrt_static"]
    assert_true(host["wan"]["proto"] == "static", "wan.proto should be static")
    assert_true(host["wan"]["ipaddr"] == "10.174.197.112", "wan.ipaddr mismatch")
    assert_true(host["wan"]["boot_try_dhcp_first"] is True, "wan failover flag should be true")
    assert_true(host["features"]["feature_openwrt_wan_apply_in_prod"] is False, "wan_apply_in_prod should be false")
    assert_true(host["access"]["zt_host"] == "172.23.236.127", "zt_host should be populated")

    # Contract integration: generated host block must be accepted by renderer.
    root = Path(tmpdir.name)
    fleet_cfg = root / "fleet.yaml"
    fleet_cfg.write_text(
        yaml.safe_dump({"hosts": generated}, sort_keys=False, allow_unicode=False),
        encoding="utf-8",
    )
    inventory_out = root / "hosts.ini"
    vars_out = root / "runtime_vars.json"
    bootstrap_out = root / "bootstrap_map.json"
    render_proc = subprocess.run(
        [
            str(RENDERER),
            "--fleet-config",
            str(fleet_cfg),
            "--mode",
            "deploy",
            "--inventory-out",
            str(inventory_out),
            "--vars-out",
            str(vars_out),
            "--bootstrap-out",
            str(bootstrap_out),
            "--skip-connectivity-check",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert_true(render_proc.returncode == 0, f"renderer compatibility failed: {render_proc.stderr or render_proc.stdout}")


def test_pppoe_and_passwall2_guard() -> None:
    mock = """\
NETWORK_CONFIG_PRESENT=1
FIREWALL_CONFIG_PRESENT=1
LAN_DEVICE=br-lan
LAN_IPADDR=192.168.5.1
LAN_NETMASK=255.255.255.0
LAN_IP6ASSIGN=60
ULA_PREFIX=
WAN_PROTO=pppoe
WAN_DEVICE=eth0
WAN_IPADDR=
WAN_NETMASK=
WAN_GATEWAY=
WAN_DNS=
WAN_PPPOE_USERNAME=user123
WAN_PPPOE_PASSWORD=secret123
WAN_PPPOE_IPV6=auto
WAN_FAILOVER_ENABLED=0
WAN_FAILOVER_WAIT_SEC=
WAN_FAILOVER_PROBE_HOST=
WAN_FAILOVER_PROBE_COUNT=
WAN_FAILOVER_START_PRIORITY=
ZT_IP=
ZT_NETWORK_ID=
ZT_SECRET=
ZT_SRC_CIDR=
ZT_PKG_INSTALLED=0
ZT_SERVICE_PRESENT=0
ZT_SERVICE_ENABLED=0
TAILSCALE_PKG_INSTALLED=0
TAILSCALE_SERVICE_PRESENT=0
TAILSCALE_SERVICE_ENABLED=0
TAILSCALE_BINARY_PRESENT=0
PASSWALL2_CONFIG_PRESENT=1
PASSWALL2_PKG_INSTALLED=1
PASSWALL2_ENABLED=1
PASSWALL2_SUBSCRIBE_URL=
PASSWALL2_PROBE_URL=
PASSWALL2_SOCKS_PORT=
PASSWALL2_ACL_MACS=
MONITORING_SERVICE_PRESENT=0
MONITORING_SERVICE_ENABLED=0
MONITORING_EXPORTER_PORT=
MONITORING_PROBE_INTERVAL=
DOCKER_SERVICE_PRESENT=0
DOCKER_SERVICE_ENABLED=0
DOCKER_RUNTIME_PACKAGES=
DOCKER_DAEMON_CONFIG_PRESENT=0
DOCKER_DAEMON_CONFIG_JSON=
DOCKER_STACK_NAMES=
HOMEPROXY_CONFIG_PRESENT=1
HOMEPROXY_PKG_INSTALLED=1
SSH_LOCKDOWN_PASSWORD_OFF=0
SSH_LOCKDOWN_ROOT_OFF=0
"""
    proc, yaml_out, _, _ = run_collector(mock, alias="wrt_pppoe")
    assert_true(proc.returncode == 0, f"collector failed: {proc.stderr or proc.stdout}")
    host = yaml.safe_load(yaml_out.read_text(encoding="utf-8"))["wrt_pppoe"]
    assert_true(host["wan"]["proto"] == "pppoe", "wan.proto should be pppoe")
    assert_true(host["wan"]["pppoe_password"] == "secret123", "pppoe password should be preserved")
    assert_true(host["passwall2"]["enabled"] is False, "passwall2.enabled should be forced false without subscribe_url")
    assert_true(
        "Passwall2 enabled but subscribe URL is empty" in proc.stderr,
        "warning about missing subscribe_url should be present",
    )


def test_dhcp_wan_parsing() -> None:
    mock = """\
NETWORK_CONFIG_PRESENT=1
FIREWALL_CONFIG_PRESENT=1
LAN_DEVICE=br-lan
LAN_IPADDR=192.168.1.1
LAN_NETMASK=255.255.255.0
LAN_IP6ASSIGN=60
ULA_PREFIX=
WAN_PROTO=dhcp
WAN_DEVICE=eth0
WAN_IPADDR=
WAN_NETMASK=
WAN_GATEWAY=
WAN_DNS=
WAN_PPPOE_USERNAME=
WAN_PPPOE_PASSWORD=
WAN_PPPOE_IPV6=auto
WAN_FAILOVER_ENABLED=1
WAN_FAILOVER_WAIT_SEC=99
WAN_FAILOVER_PROBE_HOST=9.9.9.9
WAN_FAILOVER_PROBE_COUNT=5
WAN_FAILOVER_START_PRIORITY=98
ZT_IP=
ZT_NETWORK_ID=
ZT_SECRET=
ZT_SRC_CIDR=
ZT_PKG_INSTALLED=0
ZT_SERVICE_PRESENT=0
ZT_SERVICE_ENABLED=0
TAILSCALE_PKG_INSTALLED=0
TAILSCALE_SERVICE_PRESENT=0
TAILSCALE_SERVICE_ENABLED=0
TAILSCALE_BINARY_PRESENT=0
PASSWALL2_CONFIG_PRESENT=0
PASSWALL2_PKG_INSTALLED=0
PASSWALL2_ENABLED=0
PASSWALL2_SUBSCRIBE_URL=
PASSWALL2_PROBE_URL=
PASSWALL2_SOCKS_PORT=
PASSWALL2_ACL_MACS=
MONITORING_SERVICE_PRESENT=0
MONITORING_SERVICE_ENABLED=0
MONITORING_EXPORTER_PORT=
MONITORING_PROBE_INTERVAL=
DOCKER_SERVICE_PRESENT=0
DOCKER_SERVICE_ENABLED=0
DOCKER_RUNTIME_PACKAGES=
DOCKER_DAEMON_CONFIG_PRESENT=0
DOCKER_DAEMON_CONFIG_JSON=
DOCKER_STACK_NAMES=
HOMEPROXY_CONFIG_PRESENT=0
HOMEPROXY_PKG_INSTALLED=0
SSH_LOCKDOWN_PASSWORD_OFF=0
SSH_LOCKDOWN_ROOT_OFF=0
"""
    proc, yaml_out, _, _ = run_collector(mock, alias="wrt_dhcp")
    assert_true(proc.returncode == 0, f"collector failed: {proc.stderr or proc.stdout}")
    host = yaml.safe_load(yaml_out.read_text(encoding="utf-8"))["wrt_dhcp"]
    assert_true(host["wan"]["proto"] == "dhcp", "wan.proto should be dhcp")
    assert_true(host["wan"]["boot_try_dhcp_first"] is False, "wan failover should be disabled for dhcp proto")


def test_cli_auth_validation() -> None:
    temp_dir = tempfile.TemporaryDirectory()
    root = Path(temp_dir.name)
    key_file = root / "id_ed25519"
    key_file.write_text("dummy-private-key\n", encoding="utf-8")

    proc_both = subprocess.run(
        [
            str(COLLECTOR),
            "--alias",
            "wrt_invalid",
            "--host",
            "203.0.113.1",
            "--user",
            "root",
            "--key-file",
            str(key_file),
            "--password",
            "pw",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert_true(proc_both.returncode != 0, "collector should fail when both --key-file and --password are passed")

    proc_none = subprocess.run(
        [
            str(COLLECTOR),
            "--alias",
            "wrt_invalid",
            "--host",
            "203.0.113.1",
            "--user",
            "root",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert_true(proc_none.returncode != 0, "collector should fail when auth option is missing")


def main() -> int:
    tests = [
        test_static_wan_and_renderer_compat,
        test_pppoe_and_passwall2_guard,
        test_dhcp_wan_parsing,
        test_cli_auth_validation,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print("All collect-openwrt-host-config contract tests passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
