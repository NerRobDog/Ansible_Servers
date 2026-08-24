#!/usr/bin/env python3
"""Contract tests for mihomo_panel_api.py, mihomo_candidate.py and the
report-redaction helpers in mihomo_gate.py."""

from __future__ import annotations

import base64
import sys
import traceback
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import mihomo_candidate  # noqa: E402
import mihomo_gate  # noqa: E402
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


# A body that is not YAML at all, carrying a value that must never surface in
# an error message. PyYAML quotes a snippet of the offending document, and the
# gate's Actions log is world-readable.
LEAKY_MALFORMED_BODY = (
    "proxies: [\n"
    "  name: leaky-node\n"
    "  password: hunter2-do-not-leak\n"
)


def test_extract_proxies_rejects_invalid_yaml() -> None:
    try:
        mihomo_panel_api.extract_proxies(LEAKY_MALFORMED_BODY)
    except RuntimeError as exc:
        assert_true("not valid YAML" in str(exc), f"unhelpful error: {exc}")
        return
    raise AssertionError("a non-YAML subscription body must raise RuntimeError")


def test_invalid_yaml_error_withholds_the_body() -> None:
    # The whole point of the guard: PyYAML's own message embeds a snippet of the
    # offending document. What matters is what an unhandled failure would
    # actually print, so assert on the rendered traceback - that is where
    # `raise ... from None` does its work via __suppress_context__.
    try:
        mihomo_panel_api.extract_proxies(LEAKY_MALFORMED_BODY)
    except RuntimeError as exc:
        rendered = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        assert_true("hunter2-do-not-leak" not in rendered, f"traceback leaked the body:\n{rendered}")
        assert_true(exc.__suppress_context__, "the original YAMLError must be suppressed")
        return
    raise AssertionError("a non-YAML subscription body must raise RuntimeError")


def leaky_proxies() -> list[dict]:
    return [
        {
            "name": "🇳🇱 Netherlands-2",
            "type": "vless",
            "server": "nl.example",
            "port": 443,
            "uuid": "1ef0346d-7989-4e50-88e6-bbe2b99672fc",
            "alpn": ["h2", "http/1.1"],
            "reality-opts": {"public-key": "Xy9-public-key-material"},
        },
    ]


def test_redaction_tokens_walk_nested_dicts_and_lists() -> None:
    tokens = mihomo_gate._redaction_tokens(leaky_proxies())
    assert_true("Xy9-public-key-material" in tokens, f"nested dict value missed: {tokens}")
    assert_true("http/1.1" in tokens, f"list value missed: {tokens}")
    assert_true("nl.example" in tokens, f"top-level value missed: {tokens}")


def test_redaction_tokens_are_longest_first() -> None:
    # A longer secret must be masked before a shorter one that is its
    # substring, otherwise the shorter pass shreds the longer token.
    tokens = mihomo_gate._redaction_tokens([{"a": "abcdefgh", "b": "abcd", "c": "abcdef"}])
    assert_true(tokens == ["abcdefgh", "abcdef", "abcd"], f"not longest-first: {tokens}")


def test_redaction_tokens_ignore_short_values() -> None:
    # Masking "443" would shred every port number in unrelated diagnostics.
    tokens = mihomo_gate._redaction_tokens([{"port": 443, "sni": "443", "type": "vless"}])
    assert_true("443" not in tokens, f"a 3-character value must not be a token: {tokens}")
    assert_true("vless" in tokens, f"a 5-character value must be a token: {tokens}")


def test_redact_masks_every_proxy_value() -> None:
    tokens = mihomo_gate._redaction_tokens(leaky_proxies())
    output = (
        "proxy 🇳🇱 Netherlands-2 initialisation failed: "
        "dial nl.example:443 uuid=1ef0346d-7989-4e50-88e6-bbe2b99672fc"
    )
    redacted = mihomo_gate._redact(output, tokens)
    for secret in ("🇳🇱 Netherlands-2", "nl.example", "1ef0346d-7989-4e50-88e6-bbe2b99672fc"):
        assert_true(secret not in redacted, f"{secret!r} survived redaction: {redacted}")
    assert_true("initialisation failed" in redacted, f"diagnostic text was lost: {redacted}")
    assert_true(":443" in redacted, f"a bare port must survive: {redacted}")


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
        test_extract_proxies_rejects_invalid_yaml,
        test_invalid_yaml_error_withholds_the_body,
        test_redaction_tokens_walk_nested_dicts_and_lists,
        test_redaction_tokens_are_longest_first,
        test_redaction_tokens_ignore_short_values,
        test_redact_masks_every_proxy_value,
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
