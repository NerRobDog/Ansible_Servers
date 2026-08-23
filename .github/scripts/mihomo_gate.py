#!/usr/bin/env python3
"""Run every validation layer against the repo's mihomo config.

Layers, in order of how cheap they are to fail:
  1. mihomo -t                 - syntax and schema
  2. referential integrity     - dangling rules, providers, filters
  3. routing probe             - what actually happens to each domain
  4. regression comparison     - does that match the checked-in expectations

Phase A only. Nothing here patches the panel or touches a router.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import mihomo_candidate  # noqa: E402
import mihomo_lint  # noqa: E402
import mihomo_panel_api  # noqa: E402
import mihomo_routing  # noqa: E402

REPO_ROOT = SCRIPT_DIR.parent.parent
DEFAULT_TEMPLATE = REPO_ROOT / "config" / "mihomo" / "default.template.yaml"
DEFAULT_EXPECTATIONS = REPO_ROOT / "config" / "mihomo" / "routing-expectations.yaml"


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the mihomo routing config.")
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--expectations", type=Path, default=DEFAULT_EXPECTATIONS)
    parser.add_argument("--report", type=Path, default=Path("mihomo-gate-report.md"))
    args = parser.parse_args()

    subscription_url = os.environ.get("MIHOMO_TEST_SUBSCRIPTION_URL", "").strip()
    if not subscription_url:
        print("MIHOMO_TEST_SUBSCRIPTION_URL is required.", file=sys.stderr)
        return 2

    template = yaml.safe_load(args.template.read_text(encoding="utf-8"))
    expectations = yaml.safe_load(args.expectations.read_text(encoding="utf-8"))["expectations"]
    domains = [item["domain"] for item in expectations]

    # Fetched via curl inside mihomo_panel_api: the macOS Python.framework
    # interpreters have no CA bundle and urllib fails against the panel host.
    # The response carries live credentials - never log it or upload it.
    proxies = mihomo_panel_api.fetch_rendered_proxies(subscription_url)
    candidate = mihomo_candidate.build_candidate(template, proxies)

    report: list[str] = ["## mihomo config gate", ""]
    failed = False

    ok, output = mihomo_routing.config_test(candidate)
    report.append(f"- **Syntax (`mihomo -t`)**: {'PASS' if ok else 'FAIL'}")
    if not ok:
        failed = True
        report.extend(["", "```", output.strip()[-2000:], "```", ""])

    errors, warnings = mihomo_lint.lint_config(candidate)
    report.append(f"- **Referential integrity**: {'PASS' if not errors else 'FAIL'}")
    for error in errors:
        failed = True
        report.append(f"  - error: {error}")
    for warning in warnings:
        report.append(f"  - warning: {warning}")

    if failed:
        # A config that does not parse cannot be routed through; stop here
        # rather than emitting a confusing pile of "no route observed".
        report.append("")
        report.append("Routing probe skipped: fix the failures above first.")
        args.report.write_text("\n".join(report) + "\n", encoding="utf-8")
        print("\n".join(report))
        return 1

    routes = mihomo_routing.probe_routes(
        candidate, domains, secret=mihomo_candidate.HARNESS_SECRET
    )
    mismatches = mihomo_routing.compare_routes(routes, expectations)
    report.append(f"- **Routing regression**: {'PASS' if not mismatches else 'FAIL'}")

    report.extend(["", "| domain | rule | group | node |", "|---|---|---|---|"])
    for item in expectations:
        route = routes.get(item["domain"])
        if route is None:
            report.append(f"| {item['domain']} | — | **no route** | — |")
            continue
        marker = "" if route.group == item["group"] else " ⚠️"
        report.append(
            f"| {item['domain']} | `{route.rule}` | {route.group}{marker} | {route.node} |"
        )

    if mismatches:
        failed = True
        report.extend(["", "**Mismatches against `routing-expectations.yaml`:**", ""])
        report.extend(f"- {mismatch}" for mismatch in mismatches)
        report.extend([
            "",
            "If this change is intentional, update `config/mihomo/routing-expectations.yaml`",
            "in the same commit so the diff records the intent.",
        ])

    args.report.write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
