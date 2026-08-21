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


def main() -> int:
    tests = [
        test_decode_unwraps_response_envelope,
        test_decode_accepts_bare_payload,
        test_decode_rejects_empty_template,
        test_roundtrip_preserves_non_ascii,
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
