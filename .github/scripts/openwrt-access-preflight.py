#!/usr/bin/env python3
"""OpenWrt access preflight with multi-jump fallback and RCA classification."""

from __future__ import annotations

import argparse
import json
import socket
import shlex
import subprocess
import sys
from pathlib import Path


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def parse_inventory(path: Path) -> tuple[list[str], dict[str, dict[str, str]]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    hosts: dict[str, dict[str, str]] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("[") or stripped.startswith("#"):
            continue
        tokens = shlex.split(stripped)
        if not tokens:
            continue
        alias = tokens[0]
        fields: dict[str, str] = {}
        for token in tokens[1:]:
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            fields[key] = value
        hosts[alias] = fields
    return lines, hosts


def render_inventory_line(alias: str, fields: dict[str, str]) -> str:
    ordered = [
        "ansible_host",
        "ansible_port",
        "ansible_user",
        "ansible_ssh_private_key_file",
        "ansible_ssh_common_args",
    ]
    parts = [alias]
    for key in ordered:
        if key not in fields:
            continue
        value = fields[key]
        if any(c in value for c in " '"):
            parts.append(f"{key}='{value}'")
        else:
            parts.append(f"{key}={value}")
    for key in sorted(k for k in fields if k not in ordered):
        value = fields[key]
        if any(c in value for c in " '"):
            parts.append(f"{key}='{value}'")
        else:
            parts.append(f"{key}={value}")
    return " ".join(parts)


def run_ssh(host: str, port: str, user: str, proxy_jump: str, key_file: str, timeout: int, command: str) -> tuple[int, str, str]:
    cmd = [
        "ssh",
        "-i",
        key_file,
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"ConnectTimeout={timeout}",
        "-p",
        str(port),
    ]
    if proxy_jump:
        cmd.extend(["-o", f"ProxyJump={proxy_jump}"])
    cmd.extend([f"{user}@{host}", command])
    proc = subprocess.run(cmd, text=True, capture_output=True, check=False)
    return proc.returncode, proc.stdout, proc.stderr


def run_jump_probe(jump: str, timeout: int) -> tuple[int, str, str]:
    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"ConnectTimeout={timeout}",
        jump,
        "echo jump-ok",
    ]
    proc = subprocess.run(cmd, text=True, capture_output=True, check=False)
    return proc.returncode, proc.stdout, proc.stderr


def classify_failure(stderr: str, jump_reachable: bool) -> str:
    lower = stderr.lower()
    if not jump_reachable:
        return "jump_unreachable"
    if "no route to host" in lower:
        return "zt_policy_block"
    if "name or service not known" in lower or "could not resolve hostname" in lower:
        return "wrong_network"
    if "connection timed out" in lower or "operation timed out" in lower:
        return "router_offline"
    if "permission denied" in lower:
        return "router_offline"
    return "router_offline"


def parse_limit(limit: str, all_aliases: list[str]) -> list[str]:
    if limit.strip() in {"", "all"}:
        return all_aliases
    result = []
    for part in limit.split(","):
        alias = part.strip()
        if alias:
            result.append(alias)
    return result


def can_connect_tcp(host: str, port: str, timeout: int) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenWrt access preflight with multi-jump fallback.")
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--bootstrap-map", required=True)
    parser.add_argument("--output-inventory", required=True)
    parser.add_argument("--report-out", required=True)
    parser.add_argument("--mode", choices=["bootstrap", "deploy", "lockdown"], required=True)
    parser.add_argument("--limit", default="all")
    parser.add_argument("--key-file", default=str(Path.home() / ".ssh" / "id_ed25519"))
    parser.add_argument("--timeout", type=int, default=8)
    args = parser.parse_args()

    inventory_path = Path(args.inventory)
    bootstrap_path = Path(args.bootstrap_map)
    output_inventory_path = Path(args.output_inventory)
    report_path = Path(args.report_out)

    if not inventory_path.exists():
        fail(f"Inventory file not found: {inventory_path}")
    if not bootstrap_path.exists():
        fail(f"Bootstrap map not found: {bootstrap_path}")

    lines, hosts = parse_inventory(inventory_path)
    bootstrap_map = json.loads(bootstrap_path.read_text(encoding="utf-8"))

    aliases = parse_limit(args.limit, list(hosts.keys()))
    report: dict[str, dict[str, object]] = {}
    failed = False

    for alias in aliases:
        if alias not in hosts:
            fail(f"Host '{alias}' not found in inventory")
        if alias not in bootstrap_map:
            fail(f"Host '{alias}' not found in bootstrap map")

        fields = hosts[alias]
        host = fields.get("ansible_host", bootstrap_map[alias].get("ansible_host", ""))
        port = fields.get("ansible_port", str(bootstrap_map[alias].get("ansible_port", 22)))
        user = fields.get(
            "ansible_user",
            bootstrap_map[alias].get("bootstrap_username") if args.mode == "bootstrap" else bootstrap_map[alias].get("deploy_user"),
        )
        key_file = str(Path(fields.get("ansible_ssh_private_key_file", args.key_file)).expanduser())
        proxy_jumps = bootstrap_map[alias].get("proxy_jumps", []) or []
        if not isinstance(proxy_jumps, list):
            proxy_jumps = [str(proxy_jumps)]

        selected_jump = ""
        jump_errors: dict[str, str] = {}
        reachable_jumps = 0

        if proxy_jumps:
            # Prefer direct route when it is available; proxy_jumps are fallback-only.
            if args.mode == "bootstrap":
                if can_connect_tcp(host, port, timeout=args.timeout):
                    fields.pop("ansible_ssh_common_args", None)
                    bootstrap_map[alias]["proxy_jump"] = ""
                    bootstrap_map[alias]["selected_proxy_jump"] = ""
                    report[alias] = {
                        "status": "ok",
                        "classification": "ok",
                        "selected_jump": "",
                        "attempted_jumps": proxy_jumps,
                        "route": "direct",
                    }
                    continue
            else:
                rc_direct, _, err_direct = run_ssh(host, port, user, "", key_file, args.timeout, "echo access-ok")
                if rc_direct == 0:
                    fields.pop("ansible_ssh_common_args", None)
                    bootstrap_map[alias]["proxy_jump"] = ""
                    bootstrap_map[alias]["selected_proxy_jump"] = ""
                    report[alias] = {
                        "status": "ok",
                        "classification": "ok",
                        "selected_jump": "",
                        "attempted_jumps": proxy_jumps,
                        "route": "direct",
                    }
                    continue
                jump_errors["direct"] = err_direct.strip()

            for jump in proxy_jumps:
                if not str(jump).strip():
                    continue
                jump = str(jump).strip()
                jump_ok_rc, _, jump_err = run_jump_probe(jump, timeout=args.timeout)
                if jump_ok_rc != 0:
                    jump_errors[jump] = jump_err.strip()
                    continue
                reachable_jumps += 1

                if args.mode == "bootstrap":
                    # Reachability probe through jump without requiring key auth on router.
                    cmd = [
                        "ssh",
                        "-o",
                        "BatchMode=yes",
                        "-o",
                        "StrictHostKeyChecking=yes",
                        "-o",
                        f"ConnectTimeout={args.timeout}",
                        jump,
                        f"nc -z -w {args.timeout} {host} {port}",
                    ]
                    proc = subprocess.run(cmd, text=True, capture_output=True, check=False)
                    if proc.returncode == 0:
                        selected_jump = jump
                        break
                    jump_errors[jump] = (proc.stderr or proc.stdout).strip()
                else:
                    rc, _, err = run_ssh(host, port, user, jump, key_file, args.timeout, "echo access-ok")
                    if rc == 0:
                        selected_jump = jump
                        break
                    jump_errors[jump] = err.strip()

            if not selected_jump:
                if reachable_jumps == 0:
                    # if no jump succeeded and all have errors, treat as unreachable jump host
                    reason = "jump_unreachable"
                    sample = next(iter(jump_errors.values()), "")
                else:
                    sample = "\n".join(jump_errors.values())
                    reason = classify_failure(sample, jump_reachable=True)
                report[alias] = {
                    "status": "failed",
                    "classification": reason,
                    "selected_jump": "",
                    "attempted_jumps": proxy_jumps,
                    "errors": jump_errors,
                }
                failed = True
                continue

            fields["ansible_ssh_common_args"] = f"-o ProxyJump={selected_jump}"
            bootstrap_map[alias]["proxy_jump"] = selected_jump
            bootstrap_map[alias]["selected_proxy_jump"] = selected_jump
            report[alias] = {
                "status": "ok",
                "classification": "ok",
                "selected_jump": selected_jump,
                "attempted_jumps": proxy_jumps,
            }
        else:
            # direct path
            if args.mode == "bootstrap":
                if not can_connect_tcp(host, port, timeout=args.timeout):
                    report[alias] = {
                        "status": "failed",
                        "classification": "router_offline",
                        "selected_jump": "",
                        "attempted_jumps": [],
                        "errors": {"direct": f"tcp_connect_failed:{host}:{port}"},
                    }
                    failed = True
                    continue
            else:
                rc, _, err = run_ssh(host, port, user, "", key_file, args.timeout, "echo access-ok")
                if rc != 0:
                    report[alias] = {
                        "status": "failed",
                        "classification": classify_failure(err, jump_reachable=True),
                        "selected_jump": "",
                        "attempted_jumps": [],
                        "errors": {"direct": err.strip()},
                    }
                    failed = True
                    continue
            report[alias] = {
                "status": "ok",
                "classification": "ok",
                "selected_jump": "",
                "attempted_jumps": [],
            }

    rendered_lines: list[str] = []
    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("[") or stripped.startswith("#"):
            rendered_lines.append(raw_line)
            continue
        tokens = shlex.split(stripped)
        if not tokens:
            rendered_lines.append(raw_line)
            continue
        alias = tokens[0]
        if alias in hosts:
            rendered_lines.append(render_inventory_line(alias, hosts[alias]))
        else:
            rendered_lines.append(raw_line)

    output_inventory_path.write_text("\n".join(rendered_lines) + "\n", encoding="utf-8")
    bootstrap_path.write_text(json.dumps(bootstrap_map, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")

    if failed:
        print(json.dumps(report, ensure_ascii=True, indent=2))
        fail("OpenWrt access preflight failed. See report for RCA classification.")


if __name__ == "__main__":
    main()
