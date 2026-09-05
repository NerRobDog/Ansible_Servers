#!/usr/bin/env python3
"""Contract tests for mihomo_panel_api.py, mihomo_candidate.py and the
report-redaction, expectations-loading and --no-routing behaviour of
mihomo_gate.py."""

from __future__ import annotations

import base64
import contextlib
import io
import os
import sys
import tempfile
import traceback
from pathlib import Path
from unittest import mock

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import mihomo_candidate  # noqa: E402
import mihomo_gate  # noqa: E402
import mihomo_panel_api  # noqa: E402
import mihomo_routing  # noqa: E402


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


def test_extract_proxies_rejects_a_scalar_proxies_value() -> None:
    # `proxies: Not Found` is valid YAML and truthy, so an emptiness check
    # alone lets it through and the caller then indexes ['name'] on a str.
    try:
        mihomo_panel_api.extract_proxies("proxies: Not Found\n")
    except RuntimeError as exc:
        assert_true("not a list" in str(exc), f"unhelpful error: {exc}")
        assert_true("str" in str(exc), f"the error should name the type it got: {exc}")
        return
    raise AssertionError("a scalar 'proxies' value must raise")


def test_extract_proxies_rejects_a_non_mapping_entry() -> None:
    try:
        mihomo_panel_api.extract_proxies("proxies:\n- name: ok\n- just-a-string\n")
    except RuntimeError as exc:
        assert_true("#1 of 2" in str(exc), f"the error should index the entry: {exc}")
        assert_true("not a mapping" in str(exc), f"unhelpful error: {exc}")
        return
    raise AssertionError("a non-mapping proxy entry must raise")


def test_extract_proxies_rejects_an_entry_without_a_name() -> None:
    try:
        mihomo_panel_api.extract_proxies("proxies:\n- type: direct\n")
    except RuntimeError as exc:
        assert_true("'name'" in str(exc), f"the error should name the missing field: {exc}")
        return
    raise AssertionError("a proxy without a name must raise")


def test_malformed_proxy_errors_never_quote_the_entry() -> None:
    # Every field of a proxy entry is a credential and the gate's Actions log
    # is world-readable, so these errors may carry types and counts only.
    bodies = (
        "proxies: hunter2-do-not-leak\n",
        "proxies:\n- hunter2-do-not-leak\n",
        "proxies:\n- password: hunter2-do-not-leak\n",
    )
    for body in bodies:
        try:
            mihomo_panel_api.extract_proxies(body)
        except RuntimeError as exc:
            rendered = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            assert_true(
                "hunter2-do-not-leak" not in rendered,
                f"the error leaked the body:\n{rendered}",
            )
            continue
        raise AssertionError(f"a malformed proxies payload must raise: {body!r}")


def write_expectations(directory: str, document: object) -> Path:
    path = Path(directory) / "routing-expectations.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    return path


def baseline_expectations() -> list[dict]:
    return [{"domain": domain, "group": "🌍 Остальные сайты"} for domain in mihomo_gate.BASELINE_DOMAINS]


def load_expectations_error(document: object) -> str:
    """Return the message load_expectations raises for `document`."""
    with tempfile.TemporaryDirectory() as directory:
        path = write_expectations(directory, document)
        try:
            mihomo_gate.load_expectations(path)
        except ValueError as exc:
            return str(exc)
    raise AssertionError(f"this document must be rejected: {document!r}")


def test_load_expectations_accepts_the_repo_file() -> None:
    # The checked-in golden file must satisfy its own validator, baseline
    # domains included - otherwise every run fails before it starts.
    expectations = mihomo_gate.load_expectations(mihomo_gate.DEFAULT_EXPECTATIONS)
    domains = {item["domain"] for item in expectations}
    missing = [d for d in mihomo_gate.BASELINE_DOMAINS if d not in domains]
    assert_true(not missing, f"the repo golden file is missing baseline domains: {missing}")


def test_load_expectations_rejects_a_missing_key() -> None:
    message = load_expectations_error({"routes": []})
    assert_true("expectations" in message, f"unhelpful error: {message}")


def test_load_expectations_rejects_a_non_list() -> None:
    message = load_expectations_error({"expectations": {"www.youtube.com": "📺 YouTube"}})
    assert_true("must be a list" in message, f"unhelpful error: {message}")


def test_load_expectations_rejects_an_empty_list() -> None:
    # The whole reason this validator exists: with `expectations: []` the probe
    # drives no domains, compare_routes compares nothing, and the routing layer
    # reports PASS for any config at all.
    message = load_expectations_error({"expectations": []})
    assert_true("empty" in message, f"unhelpful error: {message}")
    assert_true("no-op" in message, f"the error should say why an empty file is fatal: {message}")


def test_load_expectations_rejects_a_non_mapping_entry() -> None:
    message = load_expectations_error({"expectations": ["www.youtube.com"]})
    assert_true("must be a mapping" in message, f"unhelpful error: {message}")


def test_load_expectations_rejects_a_blank_field() -> None:
    for entry in ({"domain": "", "group": "📺 YouTube"}, {"domain": "a.com", "group": "  "}):
        message = load_expectations_error({"expectations": [entry]})
        assert_true("non-empty string" in message, f"unhelpful error for {entry}: {message}")


def test_load_expectations_rejects_a_missing_field() -> None:
    message = load_expectations_error({"expectations": [{"domain": "a.com"}]})
    assert_true("'group'" in message, f"the error should name the missing field: {message}")


def test_load_expectations_requires_every_baseline_domain() -> None:
    # Deleting an assertion is the cheapest way to make a gate green, and the
    # file holding the assertions travels in the PR the gate is guarding.
    for dropped in mihomo_gate.BASELINE_DOMAINS:
        kept = [item for item in baseline_expectations() if item["domain"] != dropped]
        message = load_expectations_error({"expectations": kept})
        assert_true(dropped in message, f"dropping {dropped} must be named in the error: {message}")


def test_load_expectations_covers_the_subscription_host() -> None:
    # Named explicitly: every router fetches its profile from no.watchd0g.dev,
    # so losing coverage of it is how a config lands that no router can replace.
    assert_true(
        "no.watchd0g.dev" in mihomo_gate.BASELINE_DOMAINS,
        f"the subscription host must be a baseline domain: {mihomo_gate.BASELINE_DOMAINS}",
    )


def run_gate(argv: list[str], environment: dict[str, str]) -> tuple[int, str]:
    """Run mihomo_gate.main() with a controlled argv and environment.

    main() prints the report; swallowed here so the PASS lines stay readable.
    """
    with mock.patch.object(sys, "argv", ["mihomo_gate.py", *argv]), \
            mock.patch.dict(os.environ, environment, clear=True), \
            contextlib.redirect_stdout(io.StringIO()):
        code = mihomo_gate.main()
    return code, Path(argv[argv.index("--report") + 1]).read_text(encoding="utf-8")


def test_no_routing_needs_no_subscription_url() -> None:
    # The point of the mode: a fork PR gets a real check rather than a skipped
    # job, and a skipped job scores as a successful check on GitHub.
    calls: list[object] = []

    def refuse_fetch(url: str) -> list[dict]:
        calls.append(url)
        raise AssertionError("--no-routing must never fetch the subscription")

    with tempfile.TemporaryDirectory() as directory:
        report = Path(directory) / "report.md"
        with mock.patch.object(mihomo_panel_api, "fetch_rendered_proxies", refuse_fetch), \
                mock.patch.object(mihomo_routing, "config_test", lambda candidate: (True, "")), \
                mock.patch.object(mihomo_routing, "probe_routes", _forbidden_probe):
            code, body = run_gate(["--no-routing", "--report", str(report)], {})

    assert_true(code == 0, f"--no-routing must pass with no environment at all, got {code}")
    assert_true(not calls, "the subscription must not be fetched in --no-routing mode")
    return_marker = "Routing regression"
    assert_true(return_marker in body, f"the report should still list every layer: {body}")


def _forbidden_probe(*args: object, **kwargs: object) -> dict:
    raise AssertionError("--no-routing must never start the routing harness")


def test_no_routing_report_names_its_own_limitation() -> None:
    # A PASS from this mode is posted verbatim. A reader who sees only green
    # bullets will reasonably conclude routing was checked; it was not.
    with tempfile.TemporaryDirectory() as directory:
        report = Path(directory) / "report.md"
        with mock.patch.object(mihomo_routing, "config_test", lambda candidate: (True, "")), \
                mock.patch.object(mihomo_routing, "probe_routes", _forbidden_probe):
            code, body = run_gate(["--no-routing", "--report", str(report)], {})

    assert_true(code == 0, f"expected a pass, got {code}")
    assert_true("--no-routing" in body, f"the report must name the mode: {body}")
    assert_true("NOT CHECKED" in body, f"the report must not imply routing passed: {body}")
    assert_true(
        "routing probe" in body and "did" in body,
        f"the report must say the probe did not run: {body}",
    )


def test_no_routing_still_validates_the_expectations_file() -> None:
    # The golden file is the gate's set of assertions and it travels in the PR
    # being gated, so the hollowing-out check must not need a credential.
    with tempfile.TemporaryDirectory() as directory:
        path = write_expectations(directory, {"expectations": []})
        report = Path(directory) / "report.md"
        try:
            with mock.patch.object(sys, "argv", [
                "mihomo_gate.py", "--no-routing",
                "--expectations", str(path), "--report", str(report),
            ]), mock.patch.dict(os.environ, {}, clear=True):
                mihomo_gate.main()
        except ValueError as exc:
            assert_true("empty" in str(exc), f"unhelpful error: {exc}")
            return
    raise AssertionError("--no-routing must still reject an emptied golden file")


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


def test_redaction_tokens_ignore_short_generic_values() -> None:
    # Masking "tcp" would shred the word out of every unrelated diagnostic.
    # `network` is not a sensitive key, so the length floor still applies.
    tokens = mihomo_gate._redaction_tokens([{"port": 443, "network": "tcp", "type": "vless"}])
    assert_true("tcp" not in tokens, f"a short generic value must not be a token: {tokens}")
    assert_true("vless" in tokens, f"a 5-character value must be a token: {tokens}")


def test_redaction_tokens_collect_short_sensitive_values() -> None:
    # The length floor is for generic values only. A two-character password is
    # still a password, and the report it would land in is a public comment.
    tokens = mihomo_gate._redaction_tokens([{"password": "hi", "uuid": "a", "sni": "x.io"}])
    assert_true("hi" in tokens, f"a 2-character password must be masked: {tokens}")
    assert_true("a" in tokens, f"a 1-character uuid must be masked: {tokens}")
    assert_true("x.io" in tokens, f"a sensitive value must be masked: {tokens}")


def test_redaction_tokens_never_collect_the_empty_string() -> None:
    # str.replace("") matches at every position; one empty token would turn
    # the entire report into a wall of <redacted>.
    tokens = mihomo_gate._redaction_tokens([{"password": "", "name": ""}])
    assert_true("" not in tokens, f"the empty string must never be a token: {tokens}")


def test_redact_masks_a_short_password() -> None:
    tokens = mihomo_gate._redaction_tokens([{"name": "node", "password": "q7"}])
    redacted = mihomo_gate._redact("auth failed for password q7 on port 443", tokens)
    assert_true(" q7 " not in redacted, f"short password survived: {redacted}")
    assert_true("443" in redacted, f"an unrelated port must survive: {redacted}")


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
        test_extract_proxies_rejects_a_scalar_proxies_value,
        test_extract_proxies_rejects_a_non_mapping_entry,
        test_extract_proxies_rejects_an_entry_without_a_name,
        test_malformed_proxy_errors_never_quote_the_entry,
        test_load_expectations_accepts_the_repo_file,
        test_load_expectations_rejects_a_missing_key,
        test_load_expectations_rejects_a_non_list,
        test_load_expectations_rejects_an_empty_list,
        test_load_expectations_rejects_a_non_mapping_entry,
        test_load_expectations_rejects_a_blank_field,
        test_load_expectations_rejects_a_missing_field,
        test_load_expectations_requires_every_baseline_domain,
        test_load_expectations_covers_the_subscription_host,
        test_no_routing_needs_no_subscription_url,
        test_no_routing_report_names_its_own_limitation,
        test_no_routing_still_validates_the_expectations_file,
        test_redaction_tokens_walk_nested_dicts_and_lists,
        test_redaction_tokens_are_longest_first,
        test_redaction_tokens_ignore_short_generic_values,
        test_redaction_tokens_collect_short_sensitive_values,
        test_redaction_tokens_never_collect_the_empty_string,
        test_redact_masks_a_short_password,
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
