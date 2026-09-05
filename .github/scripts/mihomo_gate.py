#!/usr/bin/env python3
"""Run every validation layer against the repo's mihomo config.

Layers, in order of how cheap they are to fail:
  1. mihomo -t                 - syntax and schema
  2. referential integrity     - dangling rules, providers, filters
  3. routing probe             - what actually happens to each domain
  4. regression comparison     - does that match the checked-in expectations

Layers 1 and 2 need no credential: the template ships its own static `proxies:`
entries and they are enough to assemble a config mihomo will parse. `--no-routing`
runs exactly those two, which is what lets a fork PR be validated at all.

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

# Shorter than this and a *generic* value is not a secret - a port such as
# "443" would be masked out of unrelated prose and shred the diagnostics this
# report exists to carry. Values found under a sensitive key are exempt: a
# two-character password is still a password.
_MIN_REDACTED_LENGTH = 4

# Keys whose value is masked at any length. Credentials (`password`, `uuid`,
# `token`) because they authenticate, and identifiers (`server`, `sni`, `name`)
# because together they are the fleet's exit inventory. There is no minimum
# useful length for any of them, and an operator is free to pick a short one.
_SENSITIVE_KEYS = frozenset({
    "password",
    "uuid",
    "username",
    "psk",
    "private-key",
    "public-key",
    "short-id",
    "auth",
    "token",
    "servername",
    "sni",
    "server",
    "name",
})


def _redaction_tokens(proxies: list[dict]) -> list[str]:
    """Every maskable string value in the proxy list, longest first.

    Two rules, not one. A value reached through a key in `_SENSITIVE_KEYS` is
    collected at *any* length, because a short password is still a password.
    Every other string is collected only at `_MIN_REDACTED_LENGTH` or longer,
    so a stray "443" or "tcp" does not shred the surrounding diagnostics.

    The rendered subscription is a bearer artifact: names, servers, UUIDs and
    passwords all identify the fleet. Longest-first so a longer secret is
    masked before a shorter one that happens to be its substring.
    """
    tokens: set[str] = set()

    def walk(value: object, *, sensitive: bool) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                walk(item, sensitive=str(key) in _SENSITIVE_KEYS)
        elif isinstance(value, list):
            # A list inherits its key's sensitivity: `alpn` stays generic,
            # while a hypothetical list under `password` does not.
            for item in value:
                walk(item, sensitive=sensitive)
        elif isinstance(value, str) and value:
            # `value` must be non-empty: str.replace("") matches at every
            # position and would turn the whole report into <redacted>.
            if sensitive or len(value) >= _MIN_REDACTED_LENGTH:
                tokens.add(value)

    walk(proxies, sensitive=False)
    # Length first, then the token itself, so the order is deterministic
    # regardless of set iteration order.
    return sorted(tokens, key=lambda token: (-len(token), token))


def _redact(text: str, tokens: list[str]) -> str:
    """Mask every proxy-derived value out of text bound for a public comment."""
    for token in tokens:
        text = text.replace(token, "<redacted>")
    return text


# Domains that must stay covered no matter how the golden file is edited.
# Without a floor, the routing layer can be switched off by deleting the
# assertions it makes - the file it reads is in the same PR it is guarding.
# `no.watchd0g.dev` is the subscription-distribution host: every router in the
# fleet fetches its profile from it, so losing coverage of that one domain is
# how a config lands that no router can replace.
BASELINE_DOMAINS = ("www.youtube.com", "no.watchd0g.dev", "yandex.ru")


def load_expectations(path: Path) -> list[dict]:
    """Parse and validate the golden routing file.

    Every rejection here exists because the alternative is a *green* gate: an
    empty or malformed `expectations:` list makes the probe drive no domains
    and `compare_routes` compare nothing, which reports PASS for any config.
    """
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or "expectations" not in document:
        raise ValueError(f"{path}: no top-level 'expectations:' key")

    expectations = document["expectations"]
    if not isinstance(expectations, list):
        raise ValueError(
            f"{path}: 'expectations:' must be a list, got {type(expectations).__name__}"
        )
    if not expectations:
        raise ValueError(
            f"{path}: 'expectations:' is empty - an empty golden file makes the "
            "routing layer a no-op that passes any config"
        )

    for index, item in enumerate(expectations):
        if not isinstance(item, dict):
            raise ValueError(
                f"{path}: expectation #{index} must be a mapping, "
                f"got {type(item).__name__}"
            )
        for field in ("domain", "group"):
            value = item.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"{path}: expectation #{index} needs a non-empty string "
                    f"{field!r}, got {value!r}"
                )

    domains = {item["domain"] for item in expectations}
    missing = [domain for domain in BASELINE_DOMAINS if domain not in domains]
    if missing:
        raise ValueError(
            f"{path}: baseline domains must stay covered, missing: "
            f"{', '.join(missing)}. Removing one silently narrows the gate; "
            "if a baseline domain genuinely no longer applies, change "
            "BASELINE_DOMAINS in mihomo_gate.py in the same commit."
        )

    return expectations


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the mihomo routing config.")
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--expectations", type=Path, default=DEFAULT_EXPECTATIONS)
    parser.add_argument("--report", type=Path, default=Path("mihomo-gate-report.md"))
    parser.add_argument(
        "--no-routing",
        action="store_true",
        help=(
            "Run syntax and referential integrity only, against the template's own "
            "static proxies. Needs no subscription credential, so it can run on a "
            "fork PR. Does not measure a single route."
        ),
    )
    args = parser.parse_args()

    template = yaml.safe_load(args.template.read_text(encoding="utf-8"))
    # Validated in both modes on purpose. The golden file is the gate's set of
    # assertions and it travels in the PR being gated, so the check that it has
    # not been hollowed out must not itself depend on a credential.
    expectations = load_expectations(args.expectations)
    domains = [item["domain"] for item in expectations]

    if args.no_routing:
        # The template's own `proxies:` list - two static entries, `type: direct`
        # and `type: dns`. They carry nothing secret and are checked into this
        # repo, which is exactly why layers 1 and 2 can run without the panel.
        proxies = template.get("proxies") or []
        if not proxies:
            print(
                f"{args.template}: no 'proxies:' in the template; --no-routing has "
                "nothing to assemble a candidate from.",
                file=sys.stderr,
            )
            return 2
    else:
        subscription_url = os.environ.get("MIHOMO_TEST_SUBSCRIPTION_URL", "").strip()
        if not subscription_url:
            print("MIHOMO_TEST_SUBSCRIPTION_URL is required.", file=sys.stderr)
            return 2
        # Fetched via curl inside mihomo_panel_api: the macOS Python.framework
        # interpreters have no CA bundle and urllib fails against the panel host.
        # The response carries live credentials - never log it or upload it.
        proxies = mihomo_panel_api.fetch_rendered_proxies(subscription_url)

    candidate = mihomo_candidate.build_candidate(template, proxies)

    report: list[str] = ["## mihomo config gate", ""]
    if args.no_routing:
        # Stated up front and in the report itself, not just in the workflow.
        # This report is posted verbatim; a reader who sees three PASS bullets
        # and no caveat will reasonably conclude routing was checked.
        report.extend([
            "**Mode: syntax and integrity only (`--no-routing`).** The routing probe did",
            "not run: no route was measured, and none of the "
            f"{len(domains)} domains in",
            "`routing-expectations.yaml` were checked. A PASS here does **not** mean",
            "routing is unchanged - only that the config parses and its references resolve.",
            "",
        ])
    failed = False

    ok, output = mihomo_routing.config_test(candidate)
    report.append(f"- **Syntax (`mihomo -t`)**: {'PASS' if ok else 'FAIL'}")
    if not ok:
        failed = True
        # This is the failure path, i.e. exactly when the output is least
        # predictable: a proxy-initialisation error echoes the offending
        # proxy's fields, and this report is posted as a public PR comment.
        #
        # Redaction is keyed to provenance, not to the mode name: under
        # --no-routing the proxies came out of a template that is checked into
        # this public repo, so there is nothing to disclose and masking them
        # would gut the one diagnostic a fork contributor gets to see.
        tokens = [] if args.no_routing else _redaction_tokens(proxies)
        redacted = _redact(output, tokens).strip()
        report.extend(["", "```", redacted[-2000:], "```", ""])

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

    if args.no_routing:
        report.extend([
            "",
            "- **Routing regression**: NOT CHECKED (`--no-routing`)",
            "",
            "Routing is verified by the `gate` job, which holds the subscription",
            "credential and is the check to trust for a route change.",
        ])
        args.report.write_text("\n".join(report) + "\n", encoding="utf-8")
        print("\n".join(report))
        return 0

    routes = mihomo_routing.probe_routes(
        candidate, domains, secret=mihomo_candidate.HARNESS_SECRET
    )
    mismatches = mihomo_routing.compare_routes(routes, expectations)
    report.append(f"- **Routing regression**: {'PASS' if not mismatches else 'FAIL'}")

    # No `node` column, deliberately - do not restore it. Two reasons: the node
    # names come from the rendered subscription and are descriptive (country,
    # role), so publishing them in a PR comment publishes the fleet's exit
    # inventory; and url-test / load-balance groups resolve to whichever node
    # won the last health check, so the column churns run-to-run on an
    # unchanged config - noise in the one artifact a human diffs by eye.
    report.extend(["", "| domain | rule | group |", "|---|---|---|"])
    for item in expectations:
        route = routes.get(item["domain"])
        if route is None:
            # Distinct from a group mismatch: the probe never produced a log
            # line, which is a failed measurement and not a routing change.
            report.append(f"| {item['domain']} | — | **probe failed (no route observed)** |")
            continue
        marker = "" if route.group == item["group"] else " ⚠️"
        report.append(f"| {item['domain']} | `{route.rule}` | {route.group}{marker} |")

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
