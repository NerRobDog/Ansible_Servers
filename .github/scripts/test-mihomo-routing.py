#!/usr/bin/env python3
"""Contract tests for mihomo_routing.py."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import mihomo_routing  # noqa: E402


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


# Verbatim lines captured from a real harness run on 2026-08-21.
REAL_LOG = '''\
time="2026-08-21T10:11:45.800262511Z" level=info msg="[TCP] 172.56.198.41:30089 --> www.youtube.com:443 match RuleSet(youtube) using 📺 YouTube[🇳🇱 Netherlands-2]"
time="2026-08-21T10:11:47.131630053Z" level=info msg="[TCP] 172.56.198.41:40549 --> chatgpt.com:443 match RuleSet(openai-inline) using 🤖 ChatGPT и AI[🇺🇸 USA-2]"
time="2026-08-21T10:11:52.796671792Z" level=info msg="[TCP] 172.56.198.41:43576 --> yandex.ru:443 match RuleSet(ru-inline) using ⚪🔵🔴 RU сайты[🇷🇺 Без VPN]"
time="2026-08-21T10:10:45.496878761Z" level=info msg="[TCP] mihomo --> raw.githubusercontent.com:443 using 🚫 Недоступные из РФ[🇷🇺 MSK YouTube Inst TG]"
time="2026-08-21T10:10:50.168255096Z" level=debug msg="Health Checked, proxy: DNS-OUT, url: https://www.gstatic.com/generate_204, alive: false"
'''


def test_parses_domain_rule_and_group() -> None:
    routes = mihomo_routing.parse_routes(REAL_LOG)
    assert_true(routes["www.youtube.com"].group == "📺 YouTube", f"group wrong: {routes['www.youtube.com']}")
    assert_true(routes["www.youtube.com"].rule == "RuleSet(youtube)", "rule wrong")
    assert_true(routes["www.youtube.com"].node == "🇳🇱 Netherlands-2", "node wrong")


def test_parses_group_names_containing_spaces_and_emoji() -> None:
    routes = mihomo_routing.parse_routes(REAL_LOG)
    assert_true(routes["chatgpt.com"].group == "🤖 ChatGPT и AI", f"multiword group wrong: {routes['chatgpt.com']}")
    assert_true(routes["yandex.ru"].group == "⚪🔵🔴 RU сайты", f"emoji group wrong: {routes['yandex.ru']}")


def test_ignores_internal_connections_without_a_match() -> None:
    # mihomo's own rule-provider downloads log a "using" line with no "match".
    # Counting them would attribute routes to domains nobody asked about.
    routes = mihomo_routing.parse_routes(REAL_LOG)
    assert_true("raw.githubusercontent.com" not in routes, "internal connection was treated as a route")


def test_ignores_non_connection_lines() -> None:
    routes = mihomo_routing.parse_routes(REAL_LOG)
    assert_true(len(routes) == 3, f"expected 3 routes, got {sorted(routes)}")


def test_parses_direct_outbound_without_node_brackets() -> None:
    line = 'time="x" level=info msg="[TCP] 10.0.0.1:5 --> example.com:443 match Match() using DIRECT"'
    routes = mihomo_routing.parse_routes(line)
    assert_true(routes["example.com"].group == "DIRECT", f"DIRECT not parsed: {routes}")
    assert_true(routes["example.com"].node == "", "DIRECT has no node and must not invent one")


def test_last_observation_wins() -> None:
    lines = (
        'time="x" level=info msg="[TCP] 1.1.1.1:1 --> a.com:443 match RuleSet(x) using 🅰 One[n1]"\n'
        'time="y" level=info msg="[TCP] 1.1.1.1:2 --> a.com:443 match RuleSet(x) using 🅱 Two[n2]"\n'
    )
    routes = mihomo_routing.parse_routes(lines)
    assert_true(routes["a.com"].group == "🅱 Two", f"later line should win: {routes['a.com']}")


def test_parse_routes_merges_across_two_log_reads() -> None:
    # probe_routes re-probes the domains that produced no log line and reads the
    # container logs a second time. The merge must keep the first pass's
    # observations and let the retry's win where they overlap.
    first = (
        'time="2026-08-21T10:11:45.800262511Z" level=info msg="[TCP] 172.56.198.41:30089 '
        '--> www.youtube.com:443 match RuleSet(youtube) using 📺 YouTube[🇳🇱 Netherlands-2]"\n'
        'time="2026-08-21T10:11:47.131630053Z" level=info msg="[TCP] 172.56.198.41:40549 '
        '--> chatgpt.com:443 match RuleSet(openai-inline) using 🤖 ChatGPT и AI[🇺🇸 USA-2]"\n'
    )
    second = (
        'time="2026-08-21T10:12:01.004112233Z" level=info msg="[TCP] 172.56.198.41:41880 '
        '--> x.com:443 match RuleSet(no-russia-hosts) using 🚫 Недоступные из РФ[🇺🇸 USA-2]"\n'
        'time="2026-08-21T10:12:02.551900871Z" level=info msg="[TCP] 172.56.198.41:41902 '
        '--> www.youtube.com:443 match RuleSet(youtube) using 📺 YouTube[🇳🇱 Netherlands-3]"\n'
    )
    merged = {**mihomo_routing.parse_routes(first), **mihomo_routing.parse_routes(second)}
    assert_true(merged["chatgpt.com"].group == "🤖 ChatGPT и AI", "first-pass route was dropped")
    assert_true(merged["x.com"].group == "🚫 Недоступные из РФ", "retry route was not merged in")
    assert_true(
        merged["www.youtube.com"].node == "🇳🇱 Netherlands-3",
        f"retry observation must win: {merged['www.youtube.com']}",
    )


def test_compare_routes_reports_missing_domain() -> None:
    expectations = [{"domain": "a.com", "group": "🅰 One"}]
    mismatches = mihomo_routing.compare_routes({}, expectations)
    assert_true(any("a.com" in m and "no route" in m for m in mismatches), f"missing domain not reported: {mismatches}")


def test_compare_routes_reports_wrong_group() -> None:
    actual = {"a.com": mihomo_routing.Route(rule="RuleSet(x)", group="🅱 Two", node="n2")}
    expectations = [{"domain": "a.com", "group": "🅰 One"}]
    mismatches = mihomo_routing.compare_routes(actual, expectations)
    assert_true(any("🅰 One" in m and "🅱 Two" in m for m in mismatches), f"wrong group not reported: {mismatches}")


def test_compare_routes_passes_when_matching() -> None:
    actual = {"a.com": mihomo_routing.Route(rule="RuleSet(x)", group="🅰 One", node="n1")}
    expectations = [{"domain": "a.com", "group": "🅰 One"}]
    assert_true(mihomo_routing.compare_routes(actual, expectations) == [], "matching routes must not report mismatches")


def main() -> int:
    tests = [
        test_parses_domain_rule_and_group,
        test_parses_group_names_containing_spaces_and_emoji,
        test_ignores_internal_connections_without_a_match,
        test_ignores_non_connection_lines,
        test_parses_direct_outbound_without_node_brackets,
        test_last_observation_wins,
        test_parse_routes_merges_across_two_log_reads,
        test_compare_routes_reports_missing_domain,
        test_compare_routes_reports_wrong_group,
        test_compare_routes_passes_when_matching,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print("All mihomo routing contract tests passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
