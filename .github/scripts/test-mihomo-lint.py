#!/usr/bin/env python3
"""Contract tests for mihomo_lint.py."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import mihomo_lint  # noqa: E402


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def healthy_config() -> dict:
    return {
        "proxies": [{"name": "DNS-OUT", "type": "dns"}, {"name": "🇳🇱 NL", "type": "vless"}],
        "proxy-groups": [
            {"name": "📺 YouTube", "type": "select", "proxies": ["🇳🇱 NL"]},
            {"name": "🌍 Остальные", "type": "select", "proxies": ["📺 YouTube", "DIRECT"]},
        ],
        "rule-providers": {"youtube": {"type": "http", "url": "https://example/yt.yaml"}},
        "rules": [
            "DST-PORT,53,DNS-OUT",
            "RULE-SET,youtube,📺 YouTube",
            "MATCH,🌍 Остальные",
        ],
    }


def test_healthy_config_has_no_errors() -> None:
    errors, _ = mihomo_lint.lint_config(healthy_config())
    assert_true(errors == [], f"healthy config reported errors: {errors}")


def test_dangling_ruleset_reference_is_an_error() -> None:
    config = healthy_config()
    config["rules"].insert(0, "RULE-SET,does-not-exist,📺 YouTube")
    errors, _ = mihomo_lint.lint_config(config)
    assert_true(any("does-not-exist" in e for e in errors), f"dangling RULE-SET not caught: {errors}")


def test_unknown_rule_target_is_an_error() -> None:
    config = healthy_config()
    config["rules"].insert(0, "RULE-SET,youtube,📺 Ghost Group")
    errors, _ = mihomo_lint.lint_config(config)
    assert_true(any("Ghost Group" in e for e in errors), f"unknown target not caught: {errors}")


def test_proxy_is_a_valid_rule_target() -> None:
    # DNS-OUT is a proxy (type: dns), not a group. Treating only groups as
    # valid targets would flag the very first rule of the real config.
    errors, _ = mihomo_lint.lint_config(healthy_config())
    assert_true(not any("DNS-OUT" in e for e in errors), f"proxy target wrongly rejected: {errors}")


def test_logic_rule_with_nested_parens_is_parsed() -> None:
    # A naive split(',') tears this rule apart and reports garbage targets.
    config = healthy_config()
    config["rules"].insert(
        0, "AND,((RULE-SET,youtube),(NETWORK,udp),(DST-PORT,50000-50100)),📺 YouTube"
    )
    errors, _ = mihomo_lint.lint_config(config)
    assert_true(errors == [], f"logic rule mis-parsed: {errors}")


def test_no_resolve_modifier_is_ignored_when_finding_target() -> None:
    config = healthy_config()
    config["rules"].insert(0, "IP-CIDR,193.233.75.48/32,DIRECT,no-resolve")
    errors, _ = mihomo_lint.lint_config(config)
    assert_true(errors == [], f"no-resolve modifier confused target detection: {errors}")


def test_dangling_group_member_is_an_error() -> None:
    config = healthy_config()
    config["proxy-groups"][0]["proxies"].append("🇩🇪 Missing")
    errors, _ = mihomo_lint.lint_config(config)
    assert_true(any("Missing" in e for e in errors), f"dangling group member not caught: {errors}")


def test_bad_exclude_filter_regex_is_an_error() -> None:
    config = healthy_config()
    config["proxy-groups"][0]["exclude-filter"] = "(?i)(unclosed"
    errors, _ = mihomo_lint.lint_config(config)
    assert_true(any("exclude-filter" in e for e in errors), f"bad regex not caught: {errors}")


def test_orphan_group_is_a_warning_not_an_error() -> None:
    config = healthy_config()
    config["proxy-groups"].append({"name": "🕳 Unused", "type": "select", "proxies": ["DIRECT"]})
    errors, warnings = mihomo_lint.lint_config(config)
    assert_true(errors == [], f"orphan group must not be a hard error: {errors}")
    assert_true(any("Unused" in w for w in warnings), f"orphan group not warned about: {warnings}")


def test_proxy_group_is_allowlisted_as_orphan() -> None:
    # Remnawave surfaces a group named PROXY that no rule targets. It is not a
    # defect and must not generate noise on every run.
    config = healthy_config()
    config["proxy-groups"].append({"name": "PROXY", "type": "select", "proxies": ["DIRECT"]})
    _, warnings = mihomo_lint.lint_config(config)
    assert_true(not any("PROXY" in w for w in warnings), f"PROXY should be allowlisted: {warnings}")


def test_unused_provider_is_a_warning() -> None:
    config = healthy_config()
    config["rule-providers"]["never-used"] = {"type": "http", "url": "https://example/x.yaml"}
    errors, warnings = mihomo_lint.lint_config(config)
    assert_true(errors == [], f"unused provider must not be a hard error: {errors}")
    assert_true(any("never-used" in w for w in warnings), f"unused provider not warned: {warnings}")


def main() -> int:
    tests = [
        test_healthy_config_has_no_errors,
        test_dangling_ruleset_reference_is_an_error,
        test_unknown_rule_target_is_an_error,
        test_proxy_is_a_valid_rule_target,
        test_logic_rule_with_nested_parens_is_parsed,
        test_no_resolve_modifier_is_ignored_when_finding_target,
        test_dangling_group_member_is_an_error,
        test_bad_exclude_filter_regex_is_an_error,
        test_orphan_group_is_a_warning_not_an_error,
        test_proxy_group_is_allowlisted_as_orphan,
        test_unused_provider_is_a_warning,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print("All mihomo lint contract tests passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
