#!/usr/bin/env python3
import argparse
import re
import sys
from pathlib import Path


RECAP_RE = re.compile(
    r"^(?P<host>\S+)\s*:\s+ok=(?P<ok>\d+)\s+changed=(?P<changed>\d+)\s+unreachable=(?P<unreachable>\d+)\s+failed=(?P<failed>\d+)\b"
)
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_inventory_hosts(path: Path) -> list[str]:
    hosts: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        alias = line.split()[0].strip()
        if alias:
            hosts.append(alias)
    return hosts


def resolve_targets(limit: str, inventory_hosts: list[str]) -> list[str]:
    if limit.strip() in {"", "all"}:
        return inventory_hosts
    targets = [item.strip() for item in limit.split(",") if item.strip()]
    return targets


def parse_recap(path: Path) -> dict[str, dict[str, int]]:
    recap: dict[str, dict[str, int]] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = ANSI_ESCAPE_RE.sub("", raw_line).strip()
        match = RECAP_RE.match(line)
        if not match:
            continue
        host = match.group("host")
        recap[host] = {
            "changed": int(match.group("changed")),
            "unreachable": int(match.group("unreachable")),
            "failed": int(match.group("failed")),
        }
    return recap


def main() -> int:
    parser = argparse.ArgumentParser(description="Assert Ansible idempotence from play recap output.")
    parser.add_argument("--log", required=True, help="Path to ansible-playbook log containing PLAY RECAP.")
    parser.add_argument("--inventory", required=True, help="Path to rendered inventory file.")
    parser.add_argument("--limit", default="all", help="all or comma-separated host aliases.")
    args = parser.parse_args()

    log_path = Path(args.log)
    inventory_path = Path(args.inventory)

    if not log_path.is_file():
        fail(f"Log file not found: {log_path}")
    if not inventory_path.is_file():
        fail(f"Inventory file not found: {inventory_path}")

    inventory_hosts = load_inventory_hosts(inventory_path)
    if not inventory_hosts:
        fail("No hosts found in inventory.")

    targets = resolve_targets(args.limit, inventory_hosts)
    if not targets:
        fail("No targets resolved from --limit.")

    unknown_targets = sorted(set(targets) - set(inventory_hosts))
    if unknown_targets:
        fail(f"Targets from --limit are not present in inventory: {', '.join(unknown_targets)}")

    recap = parse_recap(log_path)
    if not recap:
        fail("No PLAY RECAP lines found in ansible log.")

    errors: list[str] = []
    for target in targets:
        item = recap.get(target)
        if item is None:
            errors.append(f"{target}: recap line is missing")
            continue
        if item["unreachable"] > 0:
            errors.append(f"{target}: unreachable={item['unreachable']}")
        if item["failed"] > 0:
            errors.append(f"{target}: failed={item['failed']}")
        if item["changed"] > 0:
            errors.append(f"{target}: changed={item['changed']} (expected 0)")

    if errors:
        fail("Idempotence check failed: " + "; ".join(errors))

    print("Idempotence check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
