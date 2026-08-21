#!/usr/bin/env python3
"""Contract tests for mihomo_panel_api.py and mihomo_candidate.py."""

from __future__ import annotations

import base64
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import mihomo_panel_api  # noqa: E402


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_decode_unwraps_response_envelope() -> None:
    body = "mixed-port: 7890\n"
    payload = {"response": {"encodedTemplateYaml": base64.b64encode(body.encode()).decode()}}
    assert_true(mihomo_panel_api.decode_template_payload(payload) == body, "response envelope not unwrapped")


def test_decode_accepts_bare_payload() -> None:
    body = "mode: rule\n"
    payload = {"encodedTemplateYaml": base64.b64encode(body.encode()).decode()}
    assert_true(mihomo_panel_api.decode_template_payload(payload) == body, "bare payload not decoded")


def test_decode_rejects_empty_template() -> None:
    try:
        mihomo_panel_api.decode_template_payload({"response": {"uuid": "x"}})
    except ValueError as exc:
        assert_true("uuid" in str(exc), "error should name the keys it did see")
        return
    raise AssertionError("empty encodedTemplateYaml must raise")


def test_roundtrip_preserves_non_ascii() -> None:
    # Group names are emoji-heavy ("📺 YouTube"); a latin-1 slip here would
    # corrupt every rule target in the config.
    body = 'rules:\n- MATCH,🌍 Остальные сайты\n'
    encoded = mihomo_panel_api.encode_template_yaml(body)
    decoded = mihomo_panel_api.decode_template_payload({"encodedTemplateYaml": encoded})
    assert_true(decoded == body, "non-ASCII round-trip corrupted the body")


import mihomo_candidate  # noqa: E402


def sample_template() -> dict:
    return {
        "mixed-port": 7890,
        "allow-lan": False,
        "tun": {"enable": True, "stack": "system"},
        "proxies": [{"name": "🇷🇺 Без VPN", "type": "direct"}],
        "proxy-groups": [
            {
                "name": "🌍 Остальные сайты",
                "type": "select",
                "remnawave": {"include-proxies": False},
                "proxies": ["DIRECT"],
            },
            {"name": "📺 YouTube", "type": "select", "include-all": True},
        ],
        "rules": ["MATCH,🌍 Остальные сайты"],
    }


def real_proxies() -> list[dict]:
    return [
        {"name": "🇳🇱 Netherlands-2", "type": "vless", "server": "nl.example", "port": 443},
        {"name": "🇺🇸 USA-2", "type": "vless", "server": "us.example", "port": 443},
    ]


def test_candidate_replaces_proxies() -> None:
    result = mihomo_candidate.build_candidate(sample_template(), real_proxies())
    names = [p["name"] for p in result["proxies"]]
    assert_true(names == ["🇳🇱 Netherlands-2", "🇺🇸 USA-2"], f"stub proxies not replaced: {names}")


def test_candidate_strips_remnawave_key() -> None:
    result = mihomo_candidate.build_candidate(sample_template(), real_proxies())
    leftovers = [g["name"] for g in result["proxy-groups"] if "remnawave" in g]
    assert_true(not leftovers, f"remnawave key survived in: {leftovers}")


def test_candidate_disables_tun() -> None:
    # TUN needs NET_ADMIN and a real device; the harness is userspace-only.
    result = mihomo_candidate.build_candidate(sample_template(), real_proxies())
    assert_true(result["tun"]["enable"] is False, "tun must be disabled in the harness")
    assert_true(result["tun"]["stack"] == "system", "unrelated tun settings must survive")


def test_candidate_forces_allow_lan() -> None:
    # With allow-lan false mihomo listens on loopback INSIDE the container, so a
    # published port goes nowhere and curl fails in 3ms with http=000.
    result = mihomo_candidate.build_candidate(sample_template(), real_proxies())
    assert_true(result["allow-lan"] is True, "allow-lan must be forced on")
    assert_true(result["bind-address"] == "*", "bind-address must be wildcard")


def test_candidate_sets_controller() -> None:
    result = mihomo_candidate.build_candidate(sample_template(), real_proxies(), secret="s3cr3t")
    assert_true(result["external-controller"] == "0.0.0.0:9099", "controller not set")
    assert_true(result["secret"] == "s3cr3t", "secret not set")
    assert_true(result["log-level"] == "debug", "debug log level is required to read rule matches")


def test_candidate_does_not_mutate_input() -> None:
    template = sample_template()
    mihomo_candidate.build_candidate(template, real_proxies())
    assert_true("remnawave" in template["proxy-groups"][0], "input template was mutated")
    assert_true(template["tun"]["enable"] is True, "input template tun was mutated")


def test_extract_proxies_reads_the_list() -> None:
    document = (
        "proxies:\n"
        "- name: '🇷🇺 Без VPN'\n"
        "  type: direct\n"
        "- name: DNS-OUT\n"
        "  type: dns\n"
    )
    proxies = mihomo_panel_api.extract_proxies(document)
    assert_true([p["name"] for p in proxies] == ["🇷🇺 Без VPN", "DNS-OUT"], f"wrong proxies: {proxies}")


def test_extract_proxies_rejects_a_document_without_proxies() -> None:
    try:
        mihomo_panel_api.extract_proxies("rules:\n- MATCH,DIRECT\n")
    except RuntimeError as exc:
        assert_true("no proxies" in str(exc), f"unhelpful error: {exc}")
        return
    raise AssertionError("a subscription without proxies must raise")


def test_extract_proxies_rejects_an_empty_document() -> None:
    # A blank or error response must fail loudly rather than yield an empty
    # config that would later look like "every route disappeared".
    try:
        mihomo_panel_api.extract_proxies("")
    except RuntimeError as exc:
        assert_true("no proxies" in str(exc), f"unhelpful error: {exc}")
        return
    raise AssertionError("an empty subscription must raise")


def main() -> int:
    tests = [
        test_decode_unwraps_response_envelope,
        test_decode_accepts_bare_payload,
        test_decode_rejects_empty_template,
        test_roundtrip_preserves_non_ascii,
        test_candidate_replaces_proxies,
        test_candidate_strips_remnawave_key,
        test_candidate_disables_tun,
        test_candidate_forces_allow_lan,
        test_candidate_sets_controller,
        test_candidate_does_not_mutate_input,
        test_extract_proxies_reads_the_list,
        test_extract_proxies_rejects_a_document_without_proxies,
        test_extract_proxies_rejects_an_empty_document,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print("All mihomo candidate contract tests passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
