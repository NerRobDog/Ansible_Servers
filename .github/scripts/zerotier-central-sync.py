#!/usr/bin/env python3
"""Validate/authorize OpenWrt members in ZeroTier Central by zt_host IP."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def api_request(token: str, method: str, url: str, payload: dict | None = None):
    data = None
    headers = {
        "Authorization": f"token {token}",
        "Content-Type": "application/json",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url=url, method=method, headers=headers, data=data)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8") if resp.length != 0 else ""
            return resp.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        fail(f"ZeroTier API error {exc.code} {url}: {body}")
    except urllib.error.URLError as exc:
        fail(f"ZeroTier API unreachable {url}: {exc}")


def parse_limit(limit: str, all_aliases: list[str]) -> list[str]:
    if limit.strip() in {"", "all"}:
        return all_aliases
    result = []
    for part in limit.split(","):
        alias = part.strip()
        if alias:
            result.append(alias)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="ZeroTier Central read+authorize sync for OpenWrt fleet")
    parser.add_argument("--runtime-vars", required=True)
    parser.add_argument("--limit", default="all")
    parser.add_argument("--authorize", action="store_true")
    parser.add_argument("--network-id", default="")
    parser.add_argument("--report-out", required=True)
    args = parser.parse_args()

    token = os.getenv("ZEROTIER_API_TOKEN", "").strip()
    if not token:
        fail("ZEROTIER_API_TOKEN is required")

    runtime_vars_path = Path(args.runtime_vars)
    if not runtime_vars_path.exists():
        fail(f"Runtime vars file not found: {runtime_vars_path}")

    runtime_vars = json.loads(runtime_vars_path.read_text(encoding="utf-8"))
    fleet_hosts = runtime_vars.get("fleet_hosts", {})
    if not isinstance(fleet_hosts, dict) or not fleet_hosts:
        fail("fleet_hosts is empty in runtime vars")

    aliases = parse_limit(args.limit, list(fleet_hosts.keys()))
    base_url = "https://api.zerotier.com/api/v1"

    reports: dict[str, dict[str, object]] = {}
    failed = False
    members_cache: dict[str, list[dict]] = {}

    for alias in aliases:
        cfg = fleet_hosts.get(alias)
        if not isinstance(cfg, dict):
            fail(f"Host '{alias}' missing in runtime vars")

        zt_host = str(((cfg.get("access") or {}).get("zt_host", "") or "")).strip()
        feature_zt = bool(((cfg.get("features") or {}).get("feature_openwrt_zerotier", False)))
        if not feature_zt:
            reports[alias] = {"status": "skipped", "reason": "feature_openwrt_zerotier=false"}
            continue
        if not zt_host:
            reports[alias] = {"status": "failed", "reason": "missing_zt_host"}
            failed = True
            continue

        network_id = str((((cfg.get("zerotier") or {}).get("network_id", "") or "")).strip() or args.network_id.strip())
        if not network_id:
            reports[alias] = {"status": "failed", "reason": "missing_network_id"}
            failed = True
            continue

        if network_id not in members_cache:
            _, members = api_request(token, "GET", f"{base_url}/network/{network_id}/member")
            if not isinstance(members, list):
                fail(f"Unexpected members payload for network {network_id}")
            members_cache[network_id] = members

        members = members_cache[network_id]
        matched = None
        for member in members:
            cfg_member = member.get("config") or {}
            ips = cfg_member.get("ipAssignments") or []
            if zt_host in ips:
                matched = member
                break

        if not matched:
            reports[alias] = {
                "status": "failed",
                "reason": "node_not_in_network",
                "network_id": network_id,
                "zt_host": zt_host,
            }
            failed = True
            continue

        node_id = str(matched.get("nodeId", "") or "")
        authorized = bool((matched.get("config") or {}).get("authorized", False))

        changed = False
        if not authorized and args.authorize:
            payload = {"config": {"authorized": True}}
            api_request(token, "POST", f"{base_url}/network/{network_id}/member/{node_id}", payload)
            changed = True
            authorized = True

        if not authorized:
            reports[alias] = {
                "status": "failed",
                "reason": "not_authorized",
                "network_id": network_id,
                "node_id": node_id,
                "zt_host": zt_host,
            }
            failed = True
            continue

        reports[alias] = {
            "status": "ok",
            "network_id": network_id,
            "node_id": node_id,
            "zt_host": zt_host,
            "authorized": True,
            "changed": changed,
        }

    report_path = Path(args.report_out)
    report_path.write_text(json.dumps(reports, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(reports, ensure_ascii=True, indent=2))
    if failed:
        fail("ZeroTier API validation failed for one or more hosts")


if __name__ == "__main__":
    main()
